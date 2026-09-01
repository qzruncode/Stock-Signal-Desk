# -*- coding: utf-8 -*-
"""后台保活运行时:把 agent 生成逻辑从 HTTP 连接生命周期里剥离。

assistant-stream 0.0.32 的 ``create_run`` 把"生成 task 生命周期"与"消费者连接
生命周期"焊死:客户端断连 → ``create_run`` finally 强制 ``task.cancel()`` 杀掉生成。
本模块用自建的 ``RunBroadcaster``(controller-like sink)替代 ``RunController`` 的
产出端,生成跑在独立后台 task,断连不杀;首连与续流统一走"订阅广播 queue"路径。

设计要点:
- ``RunBroadcaster`` 方法名与 ``RunController`` 对齐 (append_text / add_tool_call /
  add_data / add_error / append_reasoning)，供标准任务流水线持续写入。
- 保留 chunk 历史游标。刷新时先用 conversations detail 恢复稳定 messages,
  再从 ``after_chunk_index`` 之后续流增量,避免重建一条空白运行气泡。
- 慢订阅者:有界 queue 溢出时终止该订阅，客户端可从持久游标续流；
  不静默丢弃中间事件。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Set

from assistant_stream.assistant_stream_chunk import (
    AssistantStreamChunk,
    DataChunk,
    ErrorChunk,
    ReasoningDeltaChunk,
    TextDeltaChunk,
    ToolCallBeginChunk,
    ToolCallDeltaChunk,
    ToolResultChunk,
)

logger = logging.getLogger(__name__)

# 订阅者 queue 容量上限:满则丢弃最旧的 chunk (慢订阅者保护)。
_SUBSCRIBER_QUEUE_MAXSIZE = 256
# 单次 run 内保留已广播 chunk,用于刷新后 resume 回放。实时分析可能持续数十
# 分钟，供应商 reasoning 又可能按 token 产生 delta；100k 级历史会同时放大
# 后端内存和浏览器恢复压力。保留最近 20k 条作为硬保护，并用全局游标记录被裁
# 掉的前缀，避免 trim 后 after_chunk_index 语义错位。
_RUN_HISTORY_MAX_CHUNKS = 20_000
# run 结束后在注册表保留的时长:让最后断开的连接仍能拿到 final chunk / 哨兵。
_RUN_RETENTION_SECONDS = 300.0
_RUN_LEASE_SECONDS = 45.0
_RUN_HEARTBEAT_SECONDS = 15.0
_RUN_CANCEL_POLL_SECONDS = 1.0
_EVENT_BATCH_MAX_CHUNKS = 64
_EVENT_BATCH_FLUSH_SECONDS = 0.05
# Tool observations may contain many source rows or long article extracts.  The
# complete observation is retained by the atomic-step ledger and the graph's
# model state; this is only the browser/data-stream copy.  Bounding both one
# result and a complete run prevents several successful tools from turning a
# live chat into an oversized React tree.
_TOOL_RESULT_STREAM_MAX_BYTES = 16_000
_TOOL_RESULT_STREAM_RUN_MAX_BYTES = 96_000
_TOOL_RESULT_STREAM_MIN_REMAINING_BYTES = 1_024
# Progress/reasoning integrations can emit token-sized deltas.  The durable
# execution timeline already carries the useful user-facing process, so keep
# this optional auxiliary channel bounded by both characters and chunk count.
# These are presentation limits only, not model-thinking timeouts.
_REASONING_STREAM_MAX_CHARACTERS = 12_000
_REASONING_STREAM_MAX_CHUNKS = 64
_REASONING_STREAM_TRUNCATION_NOTICE = "\n（其余内部过程已截断；执行步骤仍会继续更新。）"


def _serialized_bytes(value: Any) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8", errors="replace"))


def _bounded_stream_value(
    value: Any,
    *,
    depth: int = 0,
    collection_limit: int,
    mapping_limit: int,
    text_limit: int,
) -> Any:
    """Build a JSON-safe browser projection without changing durable data."""
    if depth >= 10:
        return "[详情已截断]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:text_limit]
    if isinstance(value, Mapping):
        items = list(value.items())
        projected = {
            str(key)[:128]: _bounded_stream_value(
                item,
                depth=depth + 1,
                collection_limit=collection_limit,
                mapping_limit=mapping_limit,
                text_limit=text_limit,
            )
            for key, item in items[:mapping_limit]
        }
        if len(items) > mapping_limit:
            projected["_stream_omitted_key_count"] = len(items) - mapping_limit
        return projected
    if isinstance(value, (list, tuple)):
        projected = [
            _bounded_stream_value(
                item,
                depth=depth + 1,
                collection_limit=collection_limit,
                mapping_limit=mapping_limit,
                text_limit=text_limit,
            )
            for item in value[:collection_limit]
        ]
        if len(value) > collection_limit:
            projected.append({"_stream_omitted_item_count": len(value) - collection_limit})
        return projected
    return str(value)[:text_limit]


def _tool_result_stream_summary(
    value: Any,
    *,
    reason: str,
    original_bytes: int,
) -> dict[str, Any]:
    """Keep the generic tool-result contract visible after UI truncation."""
    safe = _json_safe(value)
    summary: dict[str, Any] = {}
    if isinstance(safe, Mapping):
        # These are protocol-level result fields, not tool/domain routing.
        for key in (
            "success",
            "partial",
            "error_code",
            "errors",
            "warnings",
            "data_time",
            "data_time_provenance",
            "is_stale",
            "freshness_unknown",
        ):
            if key in safe:
                summary[key] = _bounded_stream_value(
                    safe[key],
                    collection_limit=3,
                    mapping_limit=6,
                    text_limit=360,
                )
    else:
        summary["result_preview"] = _bounded_stream_value(
            safe,
            collection_limit=2,
            mapping_limit=4,
            text_limit=360,
        )
    summary["_stream_presentation"] = {
        "truncated": True,
        "reason": reason,
        "original_bytes": original_bytes,
    }
    return summary


def _project_tool_result_for_stream(value: Any, *, byte_limit: int) -> Any:
    """Bound a tool result for streaming while preserving the graph/audit copy."""
    safe = _json_safe(value)
    original_bytes = _serialized_bytes(safe)
    if original_bytes <= byte_limit:
        return safe

    for collection_limit, mapping_limit, text_limit in (
        (24, 64, 3_000),
        (12, 32, 1_200),
        (6, 16, 600),
        (3, 8, 300),
    ):
        projected = _bounded_stream_value(
            safe,
            collection_limit=collection_limit,
            mapping_limit=mapping_limit,
            text_limit=text_limit,
        )
        if isinstance(projected, Mapping):
            projected = dict(projected)
            projected["_stream_presentation"] = {
                "truncated": True,
                "reason": "per_tool_result_budget",
                "original_bytes": original_bytes,
            }
        if _serialized_bytes(projected) <= byte_limit:
            return projected

    return _tool_result_stream_summary(
        safe,
        reason="per_tool_result_budget",
        original_bytes=original_bytes,
    )


def runtime_worker_id() -> str:
    """Stable identity for one API worker process."""
    configured = (os.getenv("AGENT_WORKER_ID") or "").strip()
    if configured:
        return configured[:128]
    return f"{socket.gethostname()}:{os.getpid()}"


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 20:
        return "[depth-truncated]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth=depth + 1) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump(mode="json"), depth=depth + 1)
    return str(value)


def serialize_assistant_chunk(chunk: AssistantStreamChunk) -> dict[str, Any]:
    """Serialize assistant-stream dataclasses without private implementation APIs."""
    return {str(key): _json_safe(value) for key, value in vars(chunk).items() if not str(key).startswith("_")}


def deserialize_assistant_chunk(
    event_type: str,
    payload: Mapping[str, Any],
) -> AssistantStreamChunk:
    """Rebuild a persisted event for ``DataStreamResponse`` replay."""
    data = dict(payload)
    data.pop("type", None)
    constructors = {
        "text-delta": TextDeltaChunk,
        "reasoning-delta": ReasoningDeltaChunk,
        "data": DataChunk,
        "error": ErrorChunk,
        "tool-call-begin": ToolCallBeginChunk,
        "tool-call-delta": ToolCallDeltaChunk,
        "tool-result": ToolResultChunk,
    }
    constructor = constructors.get(event_type)
    if constructor is None:
        raise ValueError(f"unsupported assistant stream event type: {event_type}")
    return constructor(**data)


class _BroadcasterToolCallController:
    """``RunController.add_tool_call`` 返回的轻量句柄。

    与 assistant-stream 的 ``ToolCallController`` 同名方法对齐
    (``append_args_text`` / ``set_response``),直接构造 chunk 并经 broadcaster
    广播,不依赖 substream task 机制 (那些 task 依附于 controller._stream_tasks,
    断连时会被一起处理,broadcaster 版本不需要)。
    """

    def __init__(
        self,
        broadcaster: "RunBroadcaster",
        tool_call_id: str,
        tool_name: str,
    ) -> None:
        self._broadcaster = broadcaster
        self._tool_call_id = tool_call_id
        self._tool_name = tool_name
        # begin chunk 在 add_tool_call 时已 emit;这里只记 id 供回填用。
        self._closed = False

    def append_args_text(self, args_text_delta: str) -> None:
        self._broadcaster._emit(
            ToolCallDeltaChunk(
                tool_call_id=self._tool_call_id,
                args_text_delta=args_text_delta,
            )
        )

    def set_response(self, result: Any, is_error: bool = False) -> None:
        self._broadcaster.add_tool_result(
            self._tool_call_id,
            result,
            is_error=is_error,
        )
        self._closed = True

    def close(self) -> None:
        # 兼容 assistant-stream ToolCallController 的 close 语义 (substream 收尾)。
        # broadcaster 版本无 substream,no-op。
        self._closed = True


class RunBroadcaster:
    """controller-like 产出层:把 chunk 广播给所有订阅者。

    取代 ``RunController`` 的产出端,但不绑定任何单个 HTTP 连接 —— 生成 task
    可独立存活,断连只取消对应订阅,不杀生成。
    """

    def __init__(
        self,
        *,
        run_id: str | None = None,
        event_sink: Callable[[str, int, AssistantStreamChunk], None] | None = None,
        event_batch_sink: Callable[[str, int, List[AssistantStreamChunk]], None] | None = None,
        initial_sequence: int = 0,
    ) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self.finished: asyncio.Event = asyncio.Event()
        self._history: List[AssistantStreamChunk] = []
        self._history_start_index = max(0, int(initial_sequence))
        self._history_next_index = max(0, int(initial_sequence))
        self._has_tool_events = False
        self._run_id = run_id
        self._event_sink = event_sink
        self._event_batch_sink = event_batch_sink
        self._pending_events: List[AssistantStreamChunk] = []
        self._flush_handle: asyncio.TimerHandle | None = None
        self._flush_task: asyncio.Task | None = None
        self._flush_lock = asyncio.Lock()
        self._persistence_error: BaseException | None = None
        self._finish_requested = False
        # 生成逻辑会把已累积的 assistant 文本写到这里,供续流端点补齐用。
        # (与 chat.py 的 state["assistant_text"] 同源,由 run_callback 实时镜像。)
        self.assistant_text_snapshot: str = ""
        self._tool_result_stream_bytes = 0
        self._reasoning_stream_characters = 0
        self._reasoning_stream_chunks = 0
        self._reasoning_stream_truncated = False

    # ── 产出方法 (与 RunController 同名) ────────────────────────────────

    def append_text(self, text_delta: str) -> None:
        self._emit(TextDeltaChunk(text_delta=text_delta))

    def append_reasoning(self, reasoning_delta: str) -> None:
        """Publish a bounded auxiliary reasoning stream for renderer safety.

        Structured ``agent_stage`` events remain the canonical explanation of
        what the agent is doing.  This guard only prevents an integration that
        sends thousands of token-sized private progress deltas from turning one
        assistant message into an ever-growing React tree.
        """
        text = str(reasoning_delta or "")
        if not text or self._reasoning_stream_truncated:
            return

        remaining = _REASONING_STREAM_MAX_CHARACTERS - self._reasoning_stream_characters
        can_emit = remaining > 0 and self._reasoning_stream_chunks < _REASONING_STREAM_MAX_CHUNKS
        if can_emit:
            visible = text[:remaining]
            if visible:
                self._emit(ReasoningDeltaChunk(reasoning_delta=visible))
                self._reasoning_stream_characters += len(visible)
                self._reasoning_stream_chunks += 1
            if len(visible) == len(text) and self._reasoning_stream_characters < _REASONING_STREAM_MAX_CHARACTERS \
                    and self._reasoning_stream_chunks < _REASONING_STREAM_MAX_CHUNKS:
                return

        self._reasoning_stream_truncated = True
        self._emit(ReasoningDeltaChunk(reasoning_delta=_REASONING_STREAM_TRUNCATION_NOTICE))

    async def add_tool_call(
        self,
        tool_name: str,
        tool_call_id: Optional[str] = None,
        parent_id: Optional[str] = None,
    ) -> _BroadcasterToolCallController:
        # 保持 async 签名，与 RunController.add_tool_call 一致，标准任务流水线里
        # 用 await 调用,无需改调用方。
        if tool_call_id is None:
            tool_call_id = f"call_{asyncio.get_running_loop().time()}"
        self._emit(
            ToolCallBeginChunk(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                parent_id=parent_id,
            )
        )
        return _BroadcasterToolCallController(self, tool_call_id, tool_name)

    def add_tool_result(
        self,
        tool_call_id: str,
        result: Any,
        *,
        is_error: bool = False,
    ) -> None:
        """Emit a browser-safe observation without shrinking graph/audit data."""
        original_bytes = _serialized_bytes(result)
        remaining = _TOOL_RESULT_STREAM_RUN_MAX_BYTES - self._tool_result_stream_bytes
        if remaining < min(
            _TOOL_RESULT_STREAM_MAX_BYTES,
            _TOOL_RESULT_STREAM_MIN_REMAINING_BYTES,
        ):
            presentation = _tool_result_stream_summary(
                result,
                reason="run_tool_result_budget",
                original_bytes=original_bytes,
            )
        else:
            presentation = _project_tool_result_for_stream(
                result,
                byte_limit=min(_TOOL_RESULT_STREAM_MAX_BYTES, remaining),
            )
        self._tool_result_stream_bytes += _serialized_bytes(presentation)
        self._emit(
            ToolResultChunk(
                tool_call_id=tool_call_id,
                result=presentation,
                is_error=is_error,
            )
        )

    def add_data(self, data: Any) -> None:
        self._emit(DataChunk(data=data))

    def add_error(self, error: str) -> None:
        self._emit(ErrorChunk(error=error))

    # ── 订阅管理 ───────────────────────────────────────────────────────

    @property
    def history_length(self) -> int:
        return self._history_next_index

    @property
    def has_tool_events(self) -> bool:
        return self._has_tool_events

    def stage_history_snapshot(self) -> list[dict[str, Any]]:
        """Return the committed LangGraph stage events for this run.

        The broadcaster is also the durable stream boundary, so this snapshot
        includes events emitted by a resumed invocation after ``drain()``.  It
        deliberately projects only ``agent_stage`` data; tool arguments and
        result payloads remain in their existing tool/event channels.
        """
        stages: list[dict[str, Any]] = []
        for chunk in self._history:
            if not isinstance(chunk, DataChunk) or not isinstance(chunk.data, Mapping):
                continue
            if chunk.data.get("event") != "agent_stage":
                continue
            stages.append(dict(chunk.data))
        return stages

    def subscribe(
        self,
        *,
        replay_from: Optional[int] = None,
    ) -> "asyncio.Queue[Optional[AssistantStreamChunk]]":
        replay_chunks: List[AssistantStreamChunk] = []
        if replay_from is not None:
            requested_index = max(0, replay_from)
            safe_index = max(
                self._history_start_index,
                min(requested_index, self._history_next_index),
            )
            history_offset = safe_index - self._history_start_index
            replay_chunks = self._history[history_offset:]

        # 进程内兼容重放为完整历史预留空间；正式跨连接续流使用数据库事件
        # 游标，避免历史裁剪或慢消费者队列造成缺口。
        queue_size = _SUBSCRIBER_QUEUE_MAXSIZE
        if replay_chunks:
            queue_size = max(queue_size, len(replay_chunks) + _SUBSCRIBER_QUEUE_MAXSIZE)
        queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        if replay_from is not None:
            for chunk in replay_chunks:
                queue.put_nowait(chunk)
            if self.finished.is_set():
                queue.put_nowait(None)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue") -> None:
        self._subscribers.discard(queue)

    def mark_finished(self) -> None:
        """生成结束:向所有订阅者投递 None 哨兵,让续流 generator 自然结束。"""
        self._finish_requested = True
        if self._pending_events or self._flush_task is not None:
            self._schedule_flush(immediate=True)
            return
        self._finish_now()

    async def drain(self) -> None:
        """Persist and publish every buffered event before terminal state."""
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        running = self._flush_task
        if running is not None:
            await running
        if self._pending_events:
            await self._flush_pending()
        if self._persistence_error is not None:
            raise RuntimeError("durable Agent event persistence failed") from self._persistence_error
        if self._finish_requested:
            self._finish_now()

    def _finish_now(self) -> None:
        if self.finished.is_set():
            return
        self.finished.set()
        for queue in list(self._subscribers):
            if not self._safe_put(queue, None):
                self._subscribers.discard(queue)

    # ── 内部 ───────────────────────────────────────────────────────────

    def _emit(self, chunk: AssistantStreamChunk) -> None:
        if self._persistence_error is not None:
            raise RuntimeError("durable Agent event persistence is unavailable") from self._persistence_error
        if self._run_id is not None and (self._event_batch_sink is not None or self._event_sink is not None):
            self._pending_events.append(chunk)
            self._schedule_flush(immediate=len(self._pending_events) >= _EVENT_BATCH_MAX_CHUNKS)
            return
        self._publish_committed_batch([chunk])

    def _schedule_flush(self, *, immediate: bool) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            return
        if self._flush_handle is not None:
            if not immediate:
                return
            self._flush_handle.cancel()
            self._flush_handle = None
        loop = asyncio.get_running_loop()

        def launch() -> None:
            self._flush_handle = None
            self._flush_task = asyncio.create_task(
                self._flush_pending(),
                name=f"agent-event-flush-{self._run_id or 'memory'}",
            )

            def completed(task: asyncio.Task) -> None:
                if self._flush_task is task:
                    self._flush_task = None
                try:
                    task.result()
                except BaseException as exc:
                    self._persistence_error = exc
                    logger.error(
                        "[RunBroadcaster] durable event batch failed run_id=%s",
                        self._run_id,
                        exc_info=(type(exc), exc, exc.__traceback__),
                    )
                    self._finish_requested = True
                    self._finish_now()
                    return
                if self._pending_events:
                    self._schedule_flush(immediate=self._finish_requested)
                elif self._finish_requested:
                    self._finish_now()

            self._flush_task.add_done_callback(completed)

        if immediate:
            launch()
        else:
            self._flush_handle = loop.call_later(
                _EVENT_BATCH_FLUSH_SECONDS,
                launch,
            )

    async def _flush_pending(self) -> None:
        async with self._flush_lock:
            while self._pending_events:
                batch = self._pending_events[:_EVENT_BATCH_MAX_CHUNKS]
                del self._pending_events[: len(batch)]
                start_sequence = self._history_next_index
                if self._event_batch_sink is not None and self._run_id is not None:
                    await asyncio.to_thread(
                        self._event_batch_sink,
                        self._run_id,
                        start_sequence,
                        batch,
                    )
                elif self._event_sink is not None and self._run_id is not None:
                    await asyncio.to_thread(
                        self._persist_legacy_batch,
                        start_sequence,
                        batch,
                    )
                self._publish_committed_batch(batch)

    def _persist_legacy_batch(
        self,
        start_sequence: int,
        batch: List[AssistantStreamChunk],
    ) -> None:
        assert self._event_sink is not None
        assert self._run_id is not None
        for offset, chunk in enumerate(batch):
            self._event_sink(
                self._run_id,
                start_sequence + offset,
                chunk,
            )

    def _publish_committed_batch(
        self,
        chunks: List[AssistantStreamChunk],
    ) -> None:
        # Persisted batches are published in the same order. A client can never
        # observe a cursor that has no durable representation.
        for chunk in chunks:
            self._publish_committed(chunk)

    def _publish_committed(self, chunk: AssistantStreamChunk) -> None:
        self._history.append(chunk)
        self._history_next_index += 1
        if isinstance(chunk, TextDeltaChunk):
            # Keep the reconnect snapshot in lockstep with the durable stream.
            # Updating it at emission time would expose text that has not yet
            # been persisted and could make a resumed client duplicate data.
            self.assistant_text_snapshot += chunk.text_delta
        if isinstance(
            chunk,
            (ToolCallBeginChunk, ToolCallDeltaChunk, ToolResultChunk),
        ):
            self._has_tool_events = True
        if len(self._history) > _RUN_HISTORY_MAX_CHUNKS:
            trim_count = len(self._history) - _RUN_HISTORY_MAX_CHUNKS
            del self._history[:trim_count]
            self._history_start_index += trim_count
        for queue in list(self._subscribers):
            if not self._safe_put(queue, chunk):
                self._subscribers.discard(queue)

    @staticmethod
    def _safe_put(
        queue: "asyncio.Queue",
        chunk: Optional[AssistantStreamChunk],
    ) -> bool:
        """Deliver one chunk, or terminate a subscriber that fell behind.

        Dropping the oldest item would create an undetectable hole in tool and
        stage events. A terminated connection can replay every missing event
        from the durable cursor without slowing the owning run.
        """
        try:
            queue.put_nowait(chunk)
            return True
        except asyncio.QueueFull:
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            queue.put_nowait(
                ErrorChunk(error=("subscriber_backpressure: reconnect with the last " "durable event cursor"))
            )
            queue.put_nowait(None)
            logger.warning("[RunBroadcaster] slow subscriber disconnected for durable replay")
            return False

    # 兼容 _flush_substreams (chat.py:423) 访问 controller._stream_tasks:
    # broadcaster 不用 substream task,提供空列表使现有调用成为 no-op。
    @property
    def _stream_tasks(self) -> list:
        return []


RunStatus = str  # "running" | "completed" | "partial" | "failed" | "cancelled"


class RunCapacityExceeded(RuntimeError):
    """Raised when the process-wide Agent concurrency budget is exhausted."""


@dataclass
class ActiveRun:
    """一个进行中(或刚结束、保留期内)的 agent 生成 run。"""

    conversation_id: str
    broadcaster: RunBroadcaster
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    attempt: int = 1
    task: Optional["asyncio.Task"] = None
    status: RunStatus = "running"
    started_at: datetime = field(default_factory=datetime.now)
    final_text: Optional[str] = None
    error: Optional[str] = None
    terminal_recorded: bool = False
    lease_task: Optional["asyncio.Task"] = None
    lease_callback: Optional[Callable[[], Awaitable[bool]]] = None
    cancel_callback: Optional[Callable[[], Awaitable[bool]]] = None
    cancel_reason: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    async def start(self, factory: Callable[["RunBroadcaster"], Awaitable["asyncio.Task"]]) -> None:
        """启动后台生成 task。

        必须在首连接 subscribe 之后调用 —— 否则后台 task 可能在首个订阅者
        subscribe 之前就 emit 完所有 chunk,导致首连收不到任何内容。
        """
        if self.task is not None:
            return
        self.task = await factory(self.broadcaster)
        if self.lease_callback is not None:
            self.lease_task = asyncio.create_task(
                self._lease_loop(),
                name=f"agent-run-lease-{self.run_id}",
            )

    async def _lease_loop(self) -> None:
        last_heartbeat = 0.0
        while self.task is not None and not self.task.done():
            await asyncio.sleep(_RUN_CANCEL_POLL_SECONDS)
            if self.task.done():
                break
            try:
                cancel_requested = await self.cancel_callback() if self.cancel_callback is not None else False
                now = asyncio.get_running_loop().time()
                if (
                    not cancel_requested
                    and self.lease_callback is not None
                    and now - last_heartbeat >= _RUN_HEARTBEAT_SECONDS
                ):
                    cancel_requested = await self.lease_callback()
                    last_heartbeat = now
            except Exception:
                # Losing the durable lease is unsafe: another worker may
                # recover the same run.  Stop local execution instead of
                # permitting split-brain effects.
                logger.exception("[AgentRun] lease heartbeat failed run_id=%s", self.run_id)
                self.cancel_reason = "lease_lost"
                self.task.cancel()
                break
            if cancel_requested:
                self.cancel_reason = "user_cancelled"
                self.task.cancel()
                break


from ._run_registry_methods1 import _ActiveRunRegistryMethods1
from ._run_registry_methods2 import _ActiveRunRegistryMethods2
class ActiveRunRegistry(_ActiveRunRegistryMethods1, _ActiveRunRegistryMethods2):
        """Local execution handles backed by an optional durable database ledger.

        The dictionary contains only tasks owned by this worker.  Claim,
        cancellation, events and terminal state use the configured database, so
        other workers can attach, cancel and recover without sticky sessions.
        """


def _bind_mixin_member(_member):
    import functools
    import types

    if isinstance(_member, staticmethod):
        return staticmethod(_bind_mixin_member(_member.__func__))
    if isinstance(_member, classmethod):
        return classmethod(_bind_mixin_member(_member.__func__))
    if not isinstance(_member, types.FunctionType):
        return _member
    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _mixin in (_ActiveRunRegistryMethods1, _ActiveRunRegistryMethods2):
    for _name, _member in _mixin.__dict__.items():
        if _name not in {"__dict__", "__weakref__"}:
            setattr(ActiveRunRegistry, _name, _bind_mixin_member(_member))


# 模块级单例:与 chat.py 的 _pending_approvals 同级,便于 approve.py /
# conversations.py / chat.py 直接 import。
active_run_registry = ActiveRunRegistry()


__all__ = [
    "ActiveRun",
    "ActiveRunRegistry",
    "RunBroadcaster",
    "RunCapacityExceeded",
    "deserialize_assistant_chunk",
    "runtime_worker_id",
    "serialize_assistant_chunk",
    "active_run_registry",
]
