# -*- coding: utf-8 -*-
"""后台保活运行时:把 agent 生成逻辑从 HTTP 连接生命周期里剥离。

assistant-stream 0.0.32 的 ``create_run`` 把"生成 task 生命周期"与"消费者连接
生命周期"焊死:客户端断连 → ``create_run`` finally 强制 ``task.cancel()`` 杀掉生成。
本模块用自建的 ``RunBroadcaster``(controller-like sink)替代 ``RunController`` 的
产出端,生成跑在独立后台 task,断连不杀;首连与续流统一走"订阅广播 queue"路径。

设计要点:
- ``RunBroadcaster`` 方法名与 ``RunController`` 对齐 (append_text / add_tool_call /
  add_data / add_error / append_reasoning),这样 ``_run_react_loop`` 几乎不用改。
- 保留 chunk 历史游标。刷新时先用 conversations detail 恢复稳定 messages,
  再从 ``after_chunk_index`` 之后续流增量,避免重建一条空白运行气泡。
- 慢订阅者:queue maxsize=256,满则 drop oldest,避免拖死生成。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

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
# 单次 run 内保留已广播 chunk,用于刷新后 resume 回放。一个 agent run 的 chunk
# 数量通常不大;设置上限是为了避免极端工具输出把进程内存撑爆。
_RUN_HISTORY_MAX_CHUNKS = 100_000
# run 结束后在注册表保留的时长:让最后断开的连接仍能拿到 final chunk / 哨兵。
_RUN_RETENTION_SECONDS = 300.0


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
        self._broadcaster._emit(
            ToolResultChunk(
                tool_call_id=self._tool_call_id,
                result=result,
                is_error=is_error,
            )
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

    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self.finished: asyncio.Event = asyncio.Event()
        self._history: List[AssistantStreamChunk] = []
        # 生成逻辑会把已累积的 assistant 文本写到这里,供续流端点补齐用。
        # (与 chat.py 的 state["assistant_text"] 同源,由 run_callback 实时镜像。)
        self.assistant_text_snapshot: str = ""

    # ── 产出方法 (与 RunController 同名) ────────────────────────────────

    def append_text(self, text_delta: str) -> None:
        self._emit(TextDeltaChunk(text_delta=text_delta))

    def append_reasoning(self, reasoning_delta: str) -> None:
        self._emit(ReasoningDeltaChunk(reasoning_delta=reasoning_delta))

    async def add_tool_call(
        self, tool_name: str, tool_call_id: Optional[str] = None
    ) -> _BroadcasterToolCallController:
        # 保持 async 签名,与 RunController.add_tool_call 一致,_run_react_loop 里
        # 用 await 调用,无需改调用方。
        if tool_call_id is None:
            tool_call_id = f"call_{asyncio.get_running_loop().time()}"
        self._emit(
            ToolCallBeginChunk(tool_call_id=tool_call_id, tool_name=tool_name)
        )
        return _BroadcasterToolCallController(self, tool_call_id, tool_name)

    def add_tool_result(self, tool_call_id: str, result: Any) -> None:
        # 与 RunController.add_tool_result 同名 (当前 _run_react_loop 未用,保留兼容)。
        self._emit(ToolResultChunk(tool_call_id=tool_call_id, result=result))

    def add_data(self, data: Any) -> None:
        self._emit(DataChunk(data=data))

    def add_error(self, error: str) -> None:
        self._emit(ErrorChunk(error=error))

    # ── 订阅管理 ───────────────────────────────────────────────────────

    @property
    def history_length(self) -> int:
        return len(self._history)

    @property
    def has_tool_events(self) -> bool:
        return any(
            isinstance(chunk, (ToolCallBeginChunk, ToolCallDeltaChunk, ToolResultChunk))
            for chunk in self._history
        )

    def subscribe(
        self,
        *,
        replay_from: Optional[int] = None,
    ) -> "asyncio.Queue[Optional[AssistantStreamChunk]]":
        replay_chunks: List[AssistantStreamChunk] = []
        if replay_from is not None:
            safe_index = max(0, min(replay_from, len(self._history)))
            replay_chunks = self._history[safe_index:]

        # 续流重放历史时不能套用慢订阅者 drop-oldest 策略,否则超过 256 个
        # chunk 的回答会从中间开始显示。给 replay 队列预留完整历史容量,
        # 后续实时增量仍保留慢消费者保护。
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
        self.finished.set()
        for queue in list(self._subscribers):
            self._safe_put(queue, None)

    # ── 内部 ───────────────────────────────────────────────────────────

    def _emit(self, chunk: AssistantStreamChunk) -> None:
        self._history.append(chunk)
        if len(self._history) > _RUN_HISTORY_MAX_CHUNKS:
            del self._history[: len(self._history) - _RUN_HISTORY_MAX_CHUNKS]
        for queue in list(self._subscribers):
            self._safe_put(queue, chunk)

    @staticmethod
    def _safe_put(queue: "asyncio.Queue", chunk: Optional[AssistantStreamChunk]) -> None:
        """向订阅者 queue 投递 chunk;满则 drop oldest 再投,避免阻塞生成。"""
        try:
            queue.put_nowait(chunk)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()  # 丢弃最旧
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(chunk)
            except asyncio.QueueFull:
                logger.debug("[RunBroadcaster] subscriber queue full, dropped chunk")

    # 兼容 _flush_substreams (chat.py:423) 访问 controller._stream_tasks:
    # broadcaster 不用 substream task,提供空列表使现有调用成为 no-op。
    @property
    def _stream_tasks(self) -> list:
        return []


RunStatus = str  # "running" | "completed" | "failed" | "cancelled"


@dataclass
class ActiveRun:
    """一个进行中(或刚结束、保留期内)的 agent 生成 run。"""

    conversation_id: str
    broadcaster: RunBroadcaster
    task: Optional["asyncio.Task"] = None
    status: RunStatus = "running"
    started_at: datetime = field(default_factory=datetime.now)
    final_text: Optional[str] = None
    error: Optional[str] = None

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


class ActiveRunRegistry:
    """进程内活跃 run 注册表 (单例)。

    多 worker 部署下不跨进程 —— 当前部署为单 worker (DatabaseManager 单例 +
    _pending_approvals 进程级均暗示单进程)。多 worker 需 sticky session 或
    外置注册表 (Redis pub/sub),本次不解决。
    """

    def __init__(self) -> None:
        self._runs: Dict[str, ActiveRun] = {}
        self._lock = asyncio.Lock()

    async def start_or_get(self, conversation_id: str) -> ActiveRun:
        """若有 running run 则返回它 (首连复用),否则建一个新 run (尚未启动 task)。

        返回的 run.task 可能为 None —— 调用方需在首连接 subscribe 之后调
        ``run.start(factory)`` 启动后台生成,确保首连能收到首个 chunk。
        """
        async with self._lock:
            existing = self._runs.get(conversation_id)
            if existing is not None and existing.is_running:
                return existing
            broadcaster = RunBroadcaster()
            run = ActiveRun(
                conversation_id=conversation_id,
                broadcaster=broadcaster,
            )
            self._runs[conversation_id] = run
            return run

    def get(self, conversation_id: str) -> Optional[ActiveRun]:
        return self._runs.get(conversation_id)

    def is_active(self, conversation_id: str) -> bool:
        run = self._runs.get(conversation_id)
        return run is not None and run.is_running

    async def mark_done(
        self,
        conversation_id: str,
        status: RunStatus,
        final_text: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """run 结束:置状态、通知订阅者、延迟清理注册表项。"""
        async with self._lock:
            run = self._runs.get(conversation_id)
            if run is None:
                return
            run.status = status
            run.final_text = final_text
            run.error = error
            run.broadcaster.mark_finished()

        # 延迟清理:让最后断开的连接仍能拿到 final chunk / None 哨兵。
        retention = _RUN_RETENTION_SECONDS

        async def _cleanup():
            await asyncio.sleep(retention)
            async with self._lock:
                self._runs.pop(conversation_id, None)

        asyncio.create_task(_cleanup())

    async def cancel(self, conversation_id: str, *, remove: bool = True) -> bool:
        """显式取消一个 run,用于删除会话/用户放弃任务。

        与普通断连不同,删除会话意味着这个后台生成结果已经没有落点,
        必须取消 task,否则会继续占用模型请求并拖慢新会话。
        """
        async with self._lock:
            run = self._runs.get(conversation_id)
            if run is None:
                return False
            task = run.task
            run.status = "cancelled"
            run.error = "cancelled"
            run.broadcaster.mark_finished()
            if remove:
                self._runs.pop(conversation_id, None)

        if task is not None and not task.done():
            task.cancel()
        return True

    async def shutdown(self) -> None:
        """进程关闭时取消所有进行中的后台 task (lifespan 调用)。"""
        async with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            if run.task is not None and not run.task.done():
                run.task.cancel()
        for run in runs:
            try:
                await run.task
            except (asyncio.CancelledError, Exception):
                pass
        self._runs.clear()


# 模块级单例:与 chat.py 的 _pending_approvals 同级,便于 approve.py /
# conversations.py / chat.py 直接 import。
active_run_registry = ActiveRunRegistry()
