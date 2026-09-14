"""Presentation adapter from generic graph activity to assistant-stream chunks."""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
import json
import re
from typing import Any, Callable, Mapping, Sequence

from langchain_core.messages import AIMessage, AIMessageChunk


_CLIENT_STAGE_HISTORY_MAX_EVENTS = 120
_CLIENT_STAGE_HISTORY_MAX_BYTES = 160_000
_CLIENT_STAGE_DETAIL_MAX_BYTES = 24_000
_PROGRESS_NEAR_DUPLICATE_MIN_LENGTH = 24
_PROGRESS_NEAR_DUPLICATE_THRESHOLD = 0.84


def _client_stage_value(value: Any, *, depth: int = 0) -> Any:
    """Project one stage detail for conversation hydration.

    The durable event table keeps the full, ordered audit trail.  This
    projection exists solely for the browser's timeline, where replaying many
    verbose historical details can otherwise turn one long conversation into a
    multi-megabyte React tree.
    """
    if depth >= 7:
        return "[详情已截断]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:2_400]
    if isinstance(value, Mapping):
        return {
            str(key)[:96]: _client_stage_value(item, depth=depth + 1)
            for key, item in list(value.items())[:32]
        }
    if isinstance(value, (list, tuple)):
        return [
            _client_stage_value(item, depth=depth + 1)
            for item in list(value)[:24]
        ]
    return str(value)[:600]


def _client_stage_details(details: Mapping[str, Any]) -> dict[str, Any]:
    projected = _client_stage_value(details)
    if not isinstance(projected, dict):
        return {"detail_truncated": True}
    try:
        size = len(json.dumps(projected, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return {"detail_truncated": True, "detail_reason": "not_serializable"}
    if size <= _CLIENT_STAGE_DETAIL_MAX_BYTES:
        return projected
    return {
        "detail_truncated": True,
        "detail_size_bytes": size,
        **{
            str(key)[:96]: (
                value[:240] if isinstance(value, str) else value
            )
            for key, value in list(projected.items())[:12]
            if value is None or isinstance(value, (bool, int, float, str))
        },
    }


def project_stage_history_for_client(
    stages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return a bounded, newest-first-safe stage history for UI hydration.

    Every normal run fits without truncation.  For an unusually long or
    verbose run, keep the newest terminal context and fold older details; the
    original durable event stream remains available for audit/replay.
    """
    selected: list[dict[str, Any]] = []
    used_bytes = 2
    for raw in reversed(list(stages)):
        if not isinstance(raw, Mapping):
            continue
        event = {
            key: raw.get(key)
            for key in (
                "event",
                "engine",
                "run_id",
                "stage",
                "status",
                "action_id",
                "tool_call_id",
                "round_id",
                "error_code",
                "summary",
                "occurred_at",
            )
            if raw.get(key) is not None
        }
        event["summary"] = str(event.get("summary") or "")[:1_000]
        details = raw.get("details")
        if isinstance(details, Mapping):
            event["details"] = _client_stage_details(details)
        try:
            event_bytes = len(
                json.dumps(event, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
            )
        except (TypeError, ValueError):
            continue
        if selected and (
            len(selected) >= _CLIENT_STAGE_HISTORY_MAX_EVENTS
            or used_bytes + event_bytes > _CLIENT_STAGE_HISTORY_MAX_BYTES
        ):
            break
        if not selected and event_bytes > _CLIENT_STAGE_HISTORY_MAX_BYTES:
            event.pop("details", None)
            event["details"] = {"detail_truncated": True}
            event_bytes = len(
                json.dumps(event, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
            )
        selected.append(event)
        used_bytes += event_bytes
    selected.reverse()
    return selected


class _ProjectedToolCallController:
    """Controller-shaped handle whose chunks use the graph custom stream."""

    def __init__(
        self,
        bridge: "GraphEventBridge",
        tool_call_id: str,
        tool_name: str,
        parent_id: str | None,
        *,
        use_stream: bool,
    ) -> None:
        self.bridge = bridge
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.parent_id = parent_id
        self.use_stream = use_stream
        self.direct_handle: Any | None = None
        self.closed = False

    def append_args_text(self, args_text_delta: str) -> None:
        value = str(args_text_delta or "")
        if not value:
            return
        if not self.use_stream and self.direct_handle is not None:
            self.direct_handle.append_args_text(value)
            return
        self.bridge._emit_tool_record(
            {
                "kind": "tool-call-delta",
                "tool_call_id": self.tool_call_id,
                "args_text_delta": value[:8_000],
            },
            lambda: None,
        )

    def set_response(self, result: Any, is_error: bool = False) -> None:
        if not self.use_stream and self.direct_handle is not None:
            self.direct_handle.set_response(result, is_error=is_error)
            self.closed = True
            return
        self.bridge._emit_tool_record(
            {
                "kind": "tool-result",
                "tool_call_id": self.tool_call_id,
                "result": self.bridge._stream_tool_result(result),
                "is_error": bool(is_error),
            },
            lambda: None,
        )
        self.closed = True

    def close(self) -> None:
        self.closed = True


class GraphEventBridge:
    # Stage events are a presentation channel.  The complete tool result and
    # audit trace remain in the durable run state; sending an unbounded copy
    # of them through every browser event can freeze the chat renderer.
    _MAX_DETAIL_BYTES = 24_000
    _MAX_DETAIL_KEYS = 24
    _MAX_STREAM_TOOL_RESULT_BYTES = 16_000

    def __init__(self, controller: Any | None, *, run_id: str) -> None:
        self.controller = controller
        self.run_id = run_id
        self._stage_history: list[dict[str, Any]] = []
        self._round_id: str | None = None
        # Model text is provisional until the application validates the model
        # turn.  Publishing it immediately makes an invalid answer impossible
        # to retract from an append-only assistant stream and was the source
        # of repeated full answers during repair loops.  Tool-progress text is
        # flushed explicitly after the completed model turn; final text is
        # flushed only by ``commit_model_answer``.
        self._model_text_buffer: list[str] = []
        self._model_text_published = ""
        self._model_chunks_seen = False
        self._model_progress_committed = False
        self._last_committed_answer: str | None = None
        # A validation loop may emit the same user-facing progress sentence
        # more than once while its durable stage events are intentionally kept
        # separate.  Coalesce repeated progress projection text within this
        # run; model text and the audit history remain untouched.
        self._progress_projection_seen: set[str] = set()
        # Tool chunks and progress text are emitted through LangGraph's
        # official ``custom`` stream while the graph is running.  The map is
        # populated by the runtime-side projection consumer, which keeps the
        # assistant-stream tool handle on the same ordered side of the
        # boundary as the begin/delta/result chunks.
        self._stream_tool_calls: dict[str, Any] = {}

    def set_round(self, round_id: str | int | None) -> None:
        self._round_id = str(round_id) if round_id not in (None, "") else None

    @staticmethod
    def _message_text(message: Any) -> str:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            return content
        if isinstance(content, (list, tuple)):
            parts: list[str] = []
            for item in content:
                if isinstance(item, Mapping):
                    value = item.get("text") or item.get("content")
                    if value:
                        parts.append(str(value))
                elif item:
                    parts.append(str(item))
            return "".join(parts)
        return str(content or "")

    def begin_model_turn(self, model_turn: int | str | None = None) -> None:
        """Start an isolated model-output stream for one native graph turn.

        The model turn is also the durable phase identity.  Every stage and
        tool event emitted until the next model turn inherits this round id,
        which lets the client group parallel tools without reconstructing a
        phase from display text.
        """
        self.set_round(model_turn)
        self._model_text_buffer = []
        self._model_text_published = ""
        self._model_chunks_seen = False
        self._model_progress_committed = False
        self._last_committed_answer = None

    def model_message(self, message: AIMessage | AIMessageChunk) -> None:
        """Buffer native LangChain text until the turn's owner validates it."""
        if isinstance(message, AIMessageChunk):
            self._model_chunks_seen = True
            text = self._message_text(message)
            if text:
                self._model_text_buffer.append(text)
            return
        if not isinstance(message, AIMessage):
            return
        # The final AIMessage follows callback chunks in LangGraph's standard
        # ``messages`` stream.  Only use its content when no chunks were
        # delivered (for example a deterministic test/adapter model).
        if not self._model_chunks_seen:
            text = self._message_text(message)
            if text:
                self._model_text_buffer.append(text)

    def commit_model_progress(self) -> None:
        """Publish buffered text belonging to a model tool-planning turn."""
        if self._model_progress_committed:
            return
        for text in self._model_text_buffer:
            # Model planning output is provisional progress, not the accepted
            # answer.  Send it through the same projection gate as stage
            # progress so a repair turn that restates the same observation is
            # not appended as another full paragraph.  Keep individual
            # provider chunks separate for smooth live rendering.
            published = self._publish_progress_projection(text)
            if published:
                self._model_text_published += published
        self._model_text_buffer = []
        self._model_progress_committed = True

    def commit_model_answer(self, answer: str) -> None:
        """Publish exactly one server-accepted answer for the current turn."""
        normalized = str(answer or "")
        if not normalized:
            return
        if normalized == self._last_committed_answer:
            self._model_text_published = normalized
            return

        # A no-tool model turn is a candidate, not a committed stream.  Drop
        # it before publishing the validated answer so retries cannot append
        # the same full answer over and over.
        self._model_text_buffer = []
        streamed = self._model_text_published if self._model_progress_committed else ""
        if streamed == normalized:
            pass
        elif streamed and normalized.startswith(streamed):
            # Partial-result suffixes (for example a bounded-budget warning)
            # can be appended smoothly after the already visible prefix.
            self._publish_model_text(normalized[len(streamed):])
        else:
            # A repair can produce text different from the provisional stream.
            # Keep the stream append-only and let terminal hydration replace the
            # provisional display with this accepted server-owned answer.
            self._publish_model_text(normalized)
        self._last_committed_answer = normalized

    def _publish_model_text(self, value: str) -> None:
        text = str(value or "")
        if not text:
            return
        self._publish_text_delta(text)
        self._model_text_published += text

    def _publish_text_delta(self, value: str) -> None:
        text = str(value or "")
        if not text:
            return
        self._emit_stream_record(
            {
                "kind": "text",
                "text": text,
                "display_kind": "progress",
                "round_id": self._round_id,
            },
            fallback=lambda: self.controller.append_text(text) if self.controller is not None else None,
        )

    @staticmethod
    def _active_stream_writer() -> Callable[[Any], Any] | None:
        """Return LangGraph's custom stream writer when inside a graph run.

        ``GraphEventBridge`` is also used by terminal/error paths and by unit
        tests outside a LangGraph runnable context.  Those paths must retain
        the existing direct-controller fallback instead of treating the
        missing writer as a runtime failure.
        """
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
        except (KeyError, LookupError, RuntimeError):
            return None
        return writer if callable(writer) else None

    def _emit_stream_record(
        self,
        record: Mapping[str, Any],
        *,
        fallback: Callable[[], None],
    ) -> None:
        """Send one product projection through LangGraph or the safe fallback.

        The custom record is deliberately small and JSON-safe.  The graph
        runtime consumes it and materializes assistant-stream chunks in order;
        code running outside LangGraph still writes directly to the injected
        controller, preserving recovery and test compatibility.
        """
        writer = self._active_stream_writer()
        if writer is not None:
            try:
                writer(dict(record))
                return
            except Exception:
                # A provider/graph shutdown must not suppress a user answer
                # just because its optional stream projection disappeared.
                pass
        fallback()

    @classmethod
    def _stream_tool_result(cls, result: Any) -> Any:
        """Bound a tool result before it enters LangGraph's custom channel."""
        safe = cls._safe_detail(result)
        try:
            encoded = json.dumps(
                safe,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return {"result_preview": str(result)[:1_200], "truncated": True}
        if len(encoded) <= cls._MAX_STREAM_TOOL_RESULT_BYTES:
            return safe
        if isinstance(safe, Mapping):
            return {
                key: value
                for key, value in list(safe.items())[:16]
                if value is None or isinstance(value, (str, int, float, bool))
            } | {
                "_stream_presentation": {
                    "truncated": True,
                    "reason": "custom_channel_budget",
                    "original_bytes": len(encoded),
                }
            }
        return {
            "result_preview": str(safe)[:1_200],
            "_stream_presentation": {
                "truncated": True,
                "reason": "custom_channel_budget",
                "original_bytes": len(encoded),
            },
        }

    async def add_tool_call(
        self,
        tool_name: str,
        tool_call_id: str | None = None,
        parent_id: str | None = None,
    ) -> "_ProjectedToolCallController":
        """Expose the controller contract through the ordered custom stream."""
        normalized_id = str(tool_call_id or "").strip()
        if not normalized_id:
            normalized_id = f"call_{id(self)}_{len(self._stream_tool_calls)}"
        projected = _ProjectedToolCallController(
            self,
            normalized_id,
            str(tool_name or "原子工具"),
            str(parent_id) if parent_id else None,
            use_stream=self._active_stream_writer() is not None,
        )
        if projected.use_stream:
            self._emit_tool_record(
                {
                    "kind": "tool-call-begin",
                    "tool_call_id": projected.tool_call_id,
                    "tool_name": projected.tool_name,
                    "parent_id": projected.parent_id,
                },
                lambda: None,
            )
        elif self.controller is not None:
            projected.direct_handle = await self.controller.add_tool_call(
                projected.tool_name,
                tool_call_id=projected.tool_call_id,
                parent_id=projected.parent_id,
            )
        return projected

    async def consume_stream_record(self, record: Any) -> None:
        """Materialize one LangGraph custom record into assistant-stream."""
        if not isinstance(record, Mapping) or self.controller is None:
            return
        kind = str(record.get("kind") or record.get("type") or "").strip()
        if kind == "text":
            text = str(record.get("text") or "")
            if text:
                self.controller.append_text(text)
            return
        if kind == "tool-call-begin":
            tool_call_id = str(record.get("tool_call_id") or "").strip()
            if not tool_call_id:
                return
            handle = await self.controller.add_tool_call(
                str(record.get("tool_name") or "原子工具"),
                tool_call_id=tool_call_id,
                parent_id=str(record.get("parent_id") or "") or None,
            )
            self._stream_tool_calls[tool_call_id] = handle
            return
        if kind == "tool-call-delta":
            tool_call_id = str(record.get("tool_call_id") or "").strip()
            delta = str(record.get("args_text_delta") or "")
            handle = self._stream_tool_calls.get(tool_call_id)
            if handle is not None and delta:
                handle.append_args_text(delta)
            return
        if kind == "tool-result":
            tool_call_id = str(record.get("tool_call_id") or "").strip()
            handle = self._stream_tool_calls.get(tool_call_id)
            if handle is not None:
                handle.set_response(record.get("result"), is_error=bool(record.get("is_error")))
            return

    def _emit_tool_record(self, record: Mapping[str, Any], fallback: Callable[[], None]) -> None:
        self._emit_stream_record(record, fallback=fallback)

    @staticmethod
    def _safe_detail(value: Any, *, depth: int = 0) -> Any:
        if depth >= 6:
            return "[depth-truncated]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value[:2_400]
        if isinstance(value, Mapping):
            return {
                str(key)[:160]: GraphEventBridge._safe_detail(item, depth=depth + 1)
                for key, item in list(value.items())[:64]
            }
        if isinstance(value, (list, tuple)):
            return [
                GraphEventBridge._safe_detail(item, depth=depth + 1)
                for item in list(value)[:64]
            ]
        return str(value)[:1_000]

    @classmethod
    def _bounded_details(cls, details: Mapping[str, Any]) -> dict[str, Any]:
        """Keep stage payloads safe for streaming without weakening audit data.

        A normal stage keeps its structured details.  If an integration puts a
        very large result into the presentation payload, retain a compact
        top-level explanation and a deterministic truncation marker instead
        of repeatedly serialising that object in the browser.
        """
        safe = cls._safe_detail(details)
        if not isinstance(safe, dict):
            return {"detail_truncated": True}
        try:
            encoded = json.dumps(
                safe,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return {"detail_truncated": True, "detail_reason": "not_serializable"}
        if len(encoded) <= cls._MAX_DETAIL_BYTES:
            return safe

        summary: dict[str, Any] = {
            "detail_truncated": True,
            "detail_size_bytes": len(encoded),
        }
        for key, value in list(safe.items())[: cls._MAX_DETAIL_KEYS]:
            if isinstance(value, str):
                summary[key] = value[:240]
            elif value is None or isinstance(value, (bool, int, float)):
                summary[key] = value
            elif isinstance(value, (list, tuple)):
                summary[f"{key}_count"] = len(value)
            elif isinstance(value, Mapping):
                summary[f"{key}_fields"] = [str(item)[:80] for item in list(value)[:12]]
            else:
                summary[key] = str(value)[:240]
        return summary

    def stage(
        self,
        stage: str,
        status: str,
        summary: str,
        *,
        action_id: str | None = None,
        tool_call_id: str | None = None,
        round_id: str | None = None,
        error_code: str | None = None,
        details: Mapping[str, Any] | None = None,
        user_message: str | None = None,
    ) -> dict[str, Any]:
        normalized_user_message = str(user_message or "").strip()
        stage_details = dict(details or {})
        if normalized_user_message:
            # Keep the text available to legacy timeline projection while the
            # live/native path receives it through the ordered custom stream.
            stage_details.setdefault("user_message", normalized_user_message)
            stage_details.setdefault("display_projection", "native_progress")
        payload = {
            "event": "agent_stage",
            "engine": "langgraph_agent_loop",
            "run_id": self.run_id,
            "stage": stage,
            "status": status,
            "action_id": action_id,
            "tool_call_id": tool_call_id,
            "round_id": round_id or self._round_id,
            "error_code": error_code,
            "summary": summary,
            "occurred_at": datetime.now().astimezone().isoformat(),
        }
        if stage_details:
            payload["details"] = self._bounded_details(stage_details)
        self._stage_history.append(dict(payload))
        if self.controller is not None:
            self.controller.add_data(payload)
        if normalized_user_message:
            self._publish_progress_projection(f"{normalized_user_message}\n\n")
        return payload

    @property
    def stage_history(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._stage_history]

    def close_open_stages(
        self,
        *,
        status: str,
        reason: str,
        error_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """Close stage instances left open by an exceptional graph stop."""
        open_stages: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        terminal_statuses = {"completed", "succeeded", "failed", "blocked", "cancelled"}
        for item in self._stage_history:
            key = tuple(
                str(item.get(field) or "")
                for field in ("stage", "action_id", "tool_call_id", "round_id")
            )
            item_status = str(item.get("status") or "")
            if item_status == "started":
                open_stages[key] = item
            elif item_status in terminal_statuses:
                open_stages.pop(key, None)

        closed: list[dict[str, Any]] = []
        for item in open_stages.values():
            original_summary = str(item.get("summary") or "").strip()
            summary = "；".join(
                part
                for part in (original_summary[:320], str(reason).strip()[:500])
                if part
            )
            closed.append(
                self.stage(
                    str(item.get("stage") or "unknown"),
                    status,
                    summary or "阶段因运行异常结束",
                    action_id=str(item.get("action_id") or "") or None,
                    tool_call_id=str(item.get("tool_call_id") or "") or None,
                    round_id=str(item.get("round_id") or "") or None,
                    error_code=error_code,
                )
            )
        return closed

    def approval_required(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        event = {
            "event": "approval_required",
            "engine": "langgraph_agent_loop",
            **dict(payload),
        }
        if self.controller is not None:
            self.controller.add_data(event)
        return event

    def reasoning(self, text: str) -> None:
        if self.controller is not None and text:
            self.controller.append_reasoning(text)

    def progress(self, text: str) -> None:
        """Publish user-facing progress without treating it as an answer.

        Planning and other control-plane middleware can use this boundary to
        keep the live assistant stream conversational.  It deliberately does
        not update the accepted answer state; terminal publication still owns
        the final answer and the durable trace keeps the structured details.
        """
        self._publish_progress_projection(str(text or ""))

    @staticmethod
    def _is_near_duplicate_progress(candidate: str, previous: str) -> bool:
        if not candidate or not previous:
            return False
        if candidate == previous or candidate in previous:
            return True
        shorter, longer = sorted((candidate, previous), key=len)
        if len(shorter) < _PROGRESS_NEAR_DUPLICATE_MIN_LENGTH:
            return False
        # A later model turn often prefixes the old observation with a new
        # transition sentence.  The extra context must not hide that this is
        # still a restatement of the same progress event.
        if len(shorter) / len(longer) < 0.60:
            return False
        return (
            SequenceMatcher(
                None,
                shorter[:4_000],
                longer[:4_000],
                autojunk=False,
            ).ratio()
            >= _PROGRESS_NEAR_DUPLICATE_THRESHOLD
        )

    def _publish_progress_projection(self, message: str) -> str | None:
        text = str(message or "")
        blocks = re.split(r"\n\s*\n", text)
        published: list[str] = []
        for index, block in enumerate(blocks):
            normalized_block = block.strip()
            if not normalized_block:
                continue
            visible_text = normalized_block
            has_following_block = any(item.strip() for item in blocks[index + 1:])
            if has_following_block or text.endswith(("\n\n", "\n \n")):
                visible_text += "\n\n"
            normalized = " ".join(visible_text.split())
            if not normalized or any(
                self._is_near_duplicate_progress(normalized, previous)
                for previous in self._progress_projection_seen
            ):
                continue
            self._publish_text_delta(visible_text)
            self._progress_projection_seen.add(normalized)
            published.append(visible_text)

        if len(self._progress_projection_seen) > 256:
            last = published[-1] if published else ""
            self._progress_projection_seen.clear()
            if last:
                self._progress_projection_seen.add(" ".join(last.split()))
        return "".join(published) or None

    def text(self, text: str) -> None:
        self.commit_model_answer(text)

    def error(self, text: str) -> None:
        if self.controller is not None and text:
            self.controller.add_error(text)


def redact_arguments(
    arguments: Mapping[str, Any],
    *,
    sensitive_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    sensitive = set(sensitive_fields)
    return {
        str(key): ("***" if str(key) in sensitive else value)
        for key, value in arguments.items()
        if str(key) != "confirmed"
    }


__all__ = ["GraphEventBridge", "project_stage_history_for_client", "redact_arguments"]
