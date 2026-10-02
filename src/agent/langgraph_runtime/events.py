"""Presentation adapter from generic graph activity to assistant-stream chunks."""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
import json
import re
from typing import Any, Mapping, Sequence

from langchain_core.messages import AIMessage, AIMessageChunk

from .answer_contract import (
    STRUCTURED_OUTPUT_TOOL_NAME,
    render_structured_answer,
    structured_answer_display_parts,
)


_CLIENT_STAGE_HISTORY_MAX_EVENTS = 120
_CLIENT_STAGE_HISTORY_MAX_BYTES = 160_000
_CLIENT_STAGE_DETAIL_MAX_BYTES = 24_000
_PROGRESS_NEAR_DUPLICATE_MIN_LENGTH = 24
_PROGRESS_NEAR_DUPLICATE_THRESHOLD = 0.84
_MODEL_PROGRESS_ENDINGS = ("。", "！", "？", ".", "!", "?")
_MODEL_PROGRESS_DELIMITERS = {
    "(": ")",
    "（": "）",
    "[": "]",
    "［": "］",
    "【": "】",
    "{": "}",
    "｛": "｝",
}


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


def _is_complete_model_progress(text: str) -> bool:
    """Reject visibly incomplete model narration before it becomes UI copy."""
    normalized = str(text or "").strip()
    if not normalized or not normalized.endswith(_MODEL_PROGRESS_ENDINGS):
        return False
    closing = set(_MODEL_PROGRESS_DELIMITERS.values())
    stack: list[str] = []
    for char in normalized:
        if char in _MODEL_PROGRESS_DELIMITERS:
            stack.append(_MODEL_PROGRESS_DELIMITERS[char])
        elif char in closing:
            if not stack or stack.pop() != char:
                return False
    return not stack


def _client_stage_details(details: Mapping[str, Any]) -> dict[str, Any]:
    # Team events carry a nested ``collaboration_event`` envelope for the
    # durable collaboration protocol.  That envelope repeats the same
    # identity and safe details already present on the stage event.  It is
    # useful in the audit log, but sending it through the browser projection
    # consumes the bounded stage budget and can evict the earliest worker-start
    # events—the exact events needed to show an expert's initial execution
    # state after refresh.  Keep direct stage details in the UI projection and
    # leave the full envelope in the append-only event log.
    compact_details = {
        key: value
        for key, value in details.items()
        if key not in {"collaboration_event", "safe_details"}
    }
    projected = _client_stage_value(compact_details or details)
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
                "schema_version",
                "event_type",
                "collaboration_id",
                "sequence",
                "scope",
                "stage",
                "phase",
                "status",
                "action_id",
                "parent_action_id",
                "task_id",
                "step_id",
                "attempt",
                "retry",
                "recovery",
                "tool_call_id",
                "round_id",
                "error_code",
                "summary",
                "occurred_at",
                "timestamp",
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


class _NoopToolCallController:
    """Keep unit-level graph calls safe when no presentation sink is attached."""

    def append_args_text(self, _args_text_delta: str) -> None:
        return

    def set_response(self, _result: Any, is_error: bool = False) -> None:
        return

    def close(self) -> None:
        return


class GraphEventBridge:
    # Stage events are a bounded control-plane projection.  The complete tool
    # result and audit trace remain in the durable run state; this projection
    # only carries the lifecycle fields needed by the timeline and Run
    # Explorer.
    _MAX_DETAIL_BYTES = 24_000
    _MAX_DETAIL_KEYS = 24

    def __init__(self, controller: Any | None, *, run_id: str) -> None:
        self.controller = controller
        self.run_id = run_id
        self._stage_history: list[dict[str, Any]] = []
        # Team stages share one monotonically increasing control-plane order.
        # Worker branches still keep their own agent/task identity; the order
        # lets the client replay the real interleaving without flattening the
        # branches into one synthetic phase.
        self._collaboration_sequence = 0
        self._round_id: str | None = None
        # All model-authored prose is provisional until the application has a
        # complete model turn.  Tool-planning narration is useful progress,
        # but it must use the typed projection path below rather than the
        # append-only answer text channel.
        self._model_text_buffer: list[str] = []
        self._model_text_published = ""
        self._model_chunks_seen = False
        self._model_tool_names: set[str] = set()
        self._model_domain_tool_seen = False
        self._model_progress_committed = False
        self._last_committed_answer: str | None = None
        self._displayed_structured_answer_key: str | None = None
        # A validation loop may emit the same user-facing progress sentence
        # more than once while its durable stage events are intentionally kept
        # separate.  Coalesce repeated progress projection text within this
        # run; model text and the audit history remain untouched.
        self._progress_projection_seen: set[str] = set()
        # Goal's conversational projection is committed only after its action
        # passes server-side validation. Keep semantic duplicates out of the
        # live transcript without changing the durable stage/event history.
        self._goal_progress_projection_seen: set[str] = set()
        # Model-authored Team projections use a parent-owned sequence.  The
        # sequence is durable in the display part and is preferable to
        # object identity, which changes every time a trace is replayed.
        self._model_projection_sequence = 0
        # One stable projection id represents one semantic model turn.  The
        # structured-contract callback may publish a growing preview before
        # the accepted contract is available; keep the canonical visible text
        # here so the accepted value cannot create a second paragraph or
        # rewrite the paragraph with a shorter/non-prefix value.
        self._model_projection_texts: dict[str, str] = {}

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
        self._model_tool_names.clear()
        self._model_domain_tool_seen = False
        self._model_progress_committed = False
        self._last_committed_answer = None
        self._displayed_structured_answer_key = None

    @staticmethod
    def _tool_call_name(value: Any) -> str:
        if isinstance(value, Mapping):
            return str(value.get("name") or "").strip()
        return str(getattr(value, "name", "") or "").strip()

    def _observe_tool_calls(self, message: Any) -> bool:
        """Return whether this model turn contains a non-answer tool call."""
        raw_chunks = getattr(message, "tool_call_chunks", None) or []
        raw_calls = getattr(message, "tool_calls", None) or []
        for raw_call in (*raw_chunks, *raw_calls):
            name = self._tool_call_name(raw_call)
            if name:
                self._model_tool_names.add(name)
        self._model_domain_tool_seen = any(
            name != STRUCTURED_OUTPUT_TOOL_NAME
            for name in self._model_tool_names
        )
        return self._model_domain_tool_seen

    def model_message(self, message: AIMessage | AIMessageChunk) -> None:
        """Buffer one model turn until its complete message is available.

        Provider message chunks are append-only transport fragments, not a
        user-facing progress contract.  In particular, a tool-call stream can
        interleave text and tool deltas, and a provider adapter may expose the
        same logical prefix across different chunk boundaries.  Publishing
        those fragments directly through ``append_text`` makes the browser
        render a provisional sentence as if it were durable prose.

        Keep the fragments for the audit/compatibility path, but publish the
        canonical text only from the completed ``AIMessage`` in the middleware
        turn boundary.  The accepted text is emitted as a typed projection,
        which is the same stable-part contract used by Team workers.
        """
        if isinstance(message, AIMessageChunk):
            self._model_chunks_seen = True
            self._observe_tool_calls(message)
            text = self._message_text(message)
            if text:
                self._model_text_buffer.append(text)
            return
        if not isinstance(message, AIMessage):
            return
        self._observe_tool_calls(message)
        # The final AIMessage follows callback chunks in LangGraph's standard
        # ``messages`` stream.  Only use its content when no chunks were
        # delivered (for example a deterministic test/adapter model).
        if not self._model_chunks_seen:
            text = self._message_text(message)
            if text:
                self._model_text_buffer.append(text)

    def commit_model_progress(self, text: str | None = None) -> None:
        """Publish one complete model tool-planning turn as a typed part.

        ``text`` should be the final merged ``AIMessage.content`` when the
        caller has it.  Falling back to the locally buffered chunks keeps
        deterministic adapters and Team child tests compatible, while the
        production Direct/Plan path uses the canonical message explicitly.
        """
        if self._model_progress_committed:
            return
        if not self._model_domain_tool_seen:
            # A structured-answer call is also a native tool call, but its
            # content is a candidate final answer and must stay behind the
            # validation gate.
            self._model_text_buffer = []
            self._model_progress_committed = True
            return
        normalized = str(text if text is not None else "".join(self._model_text_buffer)).strip()
        self._model_text_buffer = []
        if normalized and _is_complete_model_progress(normalized):
            self.publish_model_projection(
                normalized,
                display_part_name="agent-model-projection",
                scope="direct",
                phase="model",
                kind="tool-progress",
                projection_id=(
                    f"{self.run_id}:model:{self._round_id or 'turn'}:projection"
                )[:192],
            )
            self._model_text_published = normalized
        self._model_progress_committed = True

    def commit_model_answer(
        self,
        answer: str,
        *,
        structured_answer: Mapping[str, Any] | None = None,
        evidence: Sequence[Any] = (),
        tool_results: Sequence[Any] = (),
    ) -> None:
        """Publish one accepted answer, preserving typed blocks and charts."""
        normalized = str(answer or "")
        if not normalized:
            return

        evidence_records = list(evidence)
        tool_records = list(tool_results)
        structured_suffix: str | None = None
        if structured_answer is not None:
            structured_suffix = self._structured_answer_suffix(
                normalized,
                structured_answer,
                evidence_records,
                tool_records,
            )
            if structured_suffix is None:
                # A terminal fallback or repaired answer can supersede an
                # earlier model candidate. Never let the candidate's typed
                # parts replace the server-owned text that was actually
                # accepted for publication.
                structured_answer = None

        if structured_answer is not None:
            display_parts = structured_answer_display_parts(
                structured_answer,
                evidence_records,
                tool_records,
            )
            if display_parts:
                try:
                    display_key = json.dumps(
                        {"answer": normalized, "parts": display_parts},
                        ensure_ascii=False,
                        default=str,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                except (TypeError, ValueError):
                    display_key = normalized
                if display_key == self._displayed_structured_answer_key:
                    self._last_committed_answer = normalized
                    return

                self._model_text_buffer = []
                self._emit_display_part(
                    name="agent-answer-boundary",
                    data={"round_id": self._round_id},
                    part_id=f"{self.run_id}:answer",
                )
                # Keep the typed answer's own block order.  A chart selected
                # for the market block must stay after that block and before
                # the next block; moving every data part ahead of every text
                # part turns a structured answer into a chart footer.  The
                # durable snapshot can additionally anchor legacy late charts
                # by action/tool identity.
                ordered_display_parts = list(display_parts)
                answer_text_parts_emitted = 0
                for part in ordered_display_parts:
                    if part.get("type") == "text":
                        # assistant-stream coalesces adjacent text deltas into
                        # one Markdown part.  Keep section headings at the
                        # beginning of a line when two typed blocks are sent
                        # in consecutive deltas; otherwise ``##`` becomes
                        # literal text in the browser.
                        if answer_text_parts_emitted:
                            self._publish_text_delta(
                                "\n\n",
                                display_kind="answer",
                            )
                        self._publish_text_delta(
                            str(part.get("text") or ""),
                            display_kind="answer",
                        )
                        answer_text_parts_emitted += 1
                    elif part.get("type") == "data":
                        self._emit_display_part(
                            name=str(part.get("name") or ""),
                            data=part.get("data"),
                            part_id=str(part.get("data", {}).get("chart_id") or "")
                            if isinstance(part.get("data"), Mapping)
                            else None,
                        )

                if structured_suffix:
                    self._publish_text_delta(structured_suffix, display_kind="answer")
                self._last_committed_answer = normalized
                self._displayed_structured_answer_key = display_key
                return

        if normalized == self._last_committed_answer:
            self._model_text_published = normalized
            return

        # A no-tool model turn is a candidate, not a committed stream.  Drop
        # it before publishing the validated answer so retries cannot append
        # the same full answer over and over.
        self._model_text_buffer = []
        # Plain terminal fallbacks need the same boundary as typed answers.
        # Otherwise a preceding progress delta and this answer coalesce into
        # one progress part, and terminal hydration appends the answer again.
        self._emit_display_part(
            name="agent-answer-boundary",
            data={"round_id": self._round_id},
            part_id=f"{self.run_id}:answer",
        )
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

    @staticmethod
    def _structured_answer_suffix(
        normalized: str,
        structured_answer: Mapping[str, Any],
        evidence: Sequence[Any],
        tool_results: Sequence[Any],
    ) -> str | None:
        """Return the terminal suffix only when the typed answer matches it."""
        without_chart_fallback = render_structured_answer(
            structured_answer,
            evidence,
            tool_results,
            include_chart_fallback=False,
        )
        with_chart_fallback = render_structured_answer(
            structured_answer,
            evidence,
            tool_results,
            include_chart_fallback=True,
        )
        for rendered in (with_chart_fallback, without_chart_fallback):
            if rendered and normalized.startswith(rendered):
                return normalized[len(rendered):].strip()
        return None

    def _publish_model_text(self, value: str) -> None:
        text = str(value or "")
        if not text:
            return
        self._publish_text_delta(text)
        self._model_text_published += text

    def _publish_text_delta(self, value: str, *, display_kind: str = "progress") -> None:
        text = str(value or "")
        if not text:
            return
        # The controller is the one ordered presentation sink.  LangGraph's
        # custom stream is intentionally not used here: nested worker
        # invocations have a different custom-stream scope and would otherwise
        # make a child progress record arrive after a later root record.
        if self.controller is not None:
            self.controller.append_text(text)

    def _emit_display_part(
        self,
        *,
        name: str,
        data: Any,
        part_id: str | None = None,
    ) -> None:
        """Emit one typed UI part through the same ordered controller."""
        normalized_name = str(name or "").strip()[:96]
        if not normalized_name:
            return
        part: dict[str, Any] = {
            "type": "data",
            "name": normalized_name,
            "data": self._safe_detail(data),
        }
        normalized_part_id = str(part_id or "").strip()[:192]
        if normalized_part_id:
            part["part_id"] = normalized_part_id
        if self.controller is not None:
            self.controller.add_data({
                "event": "agent_display_part",
                "part": part,
            })

    def publish_team_review_report(
        self,
        report: Mapping[str, Any],
        *,
        collaboration_id: str,
        revision: int = 0,
    ) -> None:
        """Publish a typed review receipt, not model prose or a final answer.

        Use explicit content bounds instead of the diagnostic-detail limiter:
        a report section must not silently lose its tail during live/replay.
        """
        team_id = str(collaboration_id or "").strip()[:96]
        blocks = [
            {
                "section": str(block.get("section") or "")[:160],
                "content": str(block.get("content") or ""),
            }
            for block in list(report.get("blocks") or [])[:16]
            if isinstance(block, Mapping) and str(block.get("content") or "").strip()
        ]
        if self.controller is None or not blocks:
            return
        self.controller.add_data({
            "event": "agent_display_part",
            "part": {
                "type": "data",
                "name": "team-review-report",
                "part_id": f"{team_id}:review-report:{max(0, revision)}",
                "data": {
                    "schema_version": "team.v1",
                    "scope": "review",
                    "report_source": "handoff",
                    "collaboration_id": team_id,
                    "title": str(report.get("title") or "")[:240],
                    "blocks": blocks,
                },
            },
        })

    def publish_model_projection(
        self,
        text: str,
        *,
        display_part_name: str = "team-model-projection",
        scope: str = "coordinator",
        collaboration_id: str = "",
        agent_id: str = "",
        task_id: str = "",
        phase: str = "",
        kind: str = "progress",
        attempt: int = 0,
        parent_event_id: str = "",
        projection_id: str | None = None,
    ) -> None:
        """Publish validated model text as an ordered Team display part.

        Team lifecycle ``stage`` summaries are control-plane data.  This
        explicit boundary is the only fresh Team path that can put
        natural-language process text in the conversation.  The part keeps
        the expert/task identity so parallel projections can be rendered in
        independent lanes while retaining native stream order.
        """
        normalized = str(text or "").strip()
        if not normalized:
            return
        safe_display_part_name = str(display_part_name or "team-model-projection").strip()[:96]
        if not safe_display_part_name:
            safe_display_part_name = "team-model-projection"
        safe_scope = str(scope or "coordinator").strip()[:32]
        safe_collaboration_id = str(collaboration_id or "").strip()[:96]
        safe_agent_id = str(agent_id or "").strip()[:96]
        safe_task_id = str(task_id or "").strip()[:96]
        safe_phase = str(phase or "").strip()[:64]
        safe_kind = str(kind or "progress").strip()[:64]
        namespace = "/".join(
            value
            for value in (
                f"team:{safe_collaboration_id}" if safe_collaboration_id else "",
                safe_scope,
                safe_agent_id,
                safe_task_id,
            )
            if value
        )[:192]
        self._model_projection_sequence += 1
        projection_sequence = self._model_projection_sequence
        projection = {
            "schema_version": (
                "team.v1"
                if safe_display_part_name == "team-model-projection"
                else "agent.v1"
            ),
            "run_id": self.run_id,
            "text": normalized[:1_800],
            "projection_source": "model",
            "scope": safe_scope,
            "namespace": namespace,
            "collaboration_id": safe_collaboration_id,
            "agent_id": safe_agent_id,
            "task_id": safe_task_id,
            "phase": safe_phase,
            "kind": safe_kind,
            "attempt": max(0, int(attempt or 0)),
            "parent_event_id": str(parent_event_id or "").strip()[:128],
            "sequence": projection_sequence,
        }
        safe_projection_id = str(projection_id or "").strip()[:192]
        if safe_projection_id:
            projection["projection_id"] = safe_projection_id
        identity = ":".join(
            value for value in (
                self.run_id,
                safe_display_part_name,
                safe_collaboration_id,
                safe_scope,
                safe_agent_id,
                safe_task_id,
                safe_phase,
                safe_kind,
            )
            if value
        )
        part_id = safe_projection_id or f"{identity}:p{projection_sequence}"[:192]
        if safe_projection_id:
            previous_text = self._model_projection_texts.get(part_id)
            if previous_text is not None:
                if normalized == previous_text or previous_text.startswith(normalized):
                    # The accepted structured value is often a shorter
                    # summary of the already visible model preview.  It is a
                    # control-plane completion, not a second user-facing
                    # paragraph.
                    return
                if not normalized.startswith(previous_text):
                    # A rewrite under the same semantic identity would make
                    # the visible sentence jump.  A new semantic turn must
                    # use a new projection id instead.
                    return
            self._model_projection_texts[part_id] = normalized
        occurred_at = datetime.now().astimezone().isoformat()
        self._emit_display_part(
            name=safe_display_part_name,
            data={
                **projection,
                "occurred_at": occurred_at,
                "timestamp": occurred_at,
            },
            part_id=part_id,
        )

    def publish_goal_progress_projection(
        self,
        text: str,
        *,
        phase: str = "",
        kind: str = "progress",
        projection_id: str | None = None,
    ) -> None:
        """Publish distinct, server-accepted Goal narration in transcript order."""
        normalized = " ".join(str(text or "").split())
        if not normalized or any(
            self._is_near_duplicate_progress(normalized, previous)
            for previous in self._goal_progress_projection_seen
        ):
            return
        self._goal_progress_projection_seen.add(normalized)
        self.publish_model_projection(
            normalized,
            display_part_name="agent-model-projection",
            scope="goal",
            phase=phase,
            kind=kind,
            projection_id=projection_id,
        )
        if len(self._goal_progress_projection_seen) > 256:
            self._goal_progress_projection_seen = {normalized}

    async def add_tool_call(
        self,
        tool_name: str,
        tool_call_id: str | None = None,
        parent_id: str | None = None,
    ) -> Any:
        """Create a native assistant-stream tool part at its actual call site."""
        normalized_id = str(tool_call_id or "").strip()
        if not normalized_id:
            normalized_id = f"call_{id(self)}"
        if self.controller is None:
            return _NoopToolCallController()
        add_tool_call = getattr(self.controller, "add_tool_call")
        try:
            return await add_tool_call(
                str(tool_name or "原子工具"),
                tool_call_id=normalized_id,
                parent_id=str(parent_id) if parent_id else None,
            )
        except TypeError as exc:
            # assistant-stream 0.0.32's stock RunController has no parent_id
            # argument.  Keep the native controller path compatible with both
            # it and the application's broadcaster extension.
            if "parent_id" not in str(exc):
                raise
            return await add_tool_call(
                str(tool_name or "原子工具"),
                tool_call_id=normalized_id,
            )

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
        occurred_at = datetime.now().astimezone().isoformat()
        phase = str(stage_details.get("phase") or stage or "")[:64]
        parent_action_id = str(
            stage_details.get("parent_action_id")
            or stage_details.get("parentActionId")
            or ""
        ).strip()[:192]
        task_id = str(
            stage_details.get("task_id")
            or stage_details.get("taskId")
            or ""
        ).strip()[:96]
        step_id = str(
            stage_details.get("step_id")
            or stage_details.get("stepId")
            or ""
        ).strip()[:96]
        try:
            attempt = max(0, int(stage_details.get("attempt") or 0))
        except (TypeError, ValueError):
            attempt = 0
        retry_info = stage_details.get("retry")
        if retry_info is None and "retryable" in stage_details:
            retry_info = {
                "retryable": bool(stage_details.get("retryable")),
                "attempt": attempt,
            }
        recovery_info = stage_details.get("recovery")
        if recovery_info is None and "recovered_from_runtime_error" in stage_details:
            recovery_info = bool(stage_details.get("recovered_from_runtime_error"))
        team_id = str(
            stage_details.get("team_id")
            or stage_details.get("teamId")
            or ""
        ).strip()
        if normalized_user_message and not team_id:
            # Direct/Plan retain their existing compatibility projection. A
            # fresh Team run must use ``publish_model_projection`` instead;
            # otherwise server-authored lifecycle copy is mistaken for model
            # prose in the conversation.
            stage_details.setdefault("user_message", normalized_user_message)
        if team_id:
            self._collaboration_sequence += 1
            raw_agent_id = str(
                stage_details.get("expert_id")
                or stage_details.get("agent_id")
                or stage_details.get("agentId")
                or ""
            ).strip()
            raw_task_id = str(
                stage_details.get("task_id")
                or stage_details.get("taskId")
                or ""
            ).strip()
            reviewer = str(
                stage_details.get("reviewer")
                or stage_details.get("gate")
                or ""
            ).strip()
            scope = (
                "review"
                if reviewer
                else "expert"
                if raw_agent_id or raw_task_id
                else "coordinator"
            )
            namespace = "/".join(
                value
                for value in (
                    f"team:{team_id}",
                    scope,
                    raw_agent_id,
                    raw_task_id,
                )
                if value
            )[:192]
            try:
                attempt = max(0, int(stage_details.get("attempt") or 0))
            except (TypeError, ValueError):
                attempt = 0
            event_kind = str(stage_details.get("kind") or "").strip()
            if not event_kind:
                event_kind = "tool" if stage in {"tool", "execute"} else "lifecycle"
            collaboration_event = {
                "schema_version": "team.v1",
                "run_id": self.run_id,
                "collaboration_id": team_id,
                "sequence": self._collaboration_sequence,
                "occurred_at": occurred_at,
                "namespace": namespace,
                "scope": scope,
                "agent_id": raw_agent_id,
                "task_id": raw_task_id,
                "phase": str(stage_details.get("phase") or stage)[:64],
                "kind": event_kind[:64],
                "status": str(status or "")[:32],
                "attempt": attempt,
                "summary": str(summary or "")[:1_000],
                "parent_event_id": str(
                    stage_details.get("parent_event_id")
                    or stage_details.get("parentEventId")
                    or ""
                )[:128],
                "safe_details": {
                    str(key): value
                    for key, value in stage_details.items()
                    if key not in {"collaboration_event", "safe_details"}
                },
            }
            # Keep the wire object typed at the Team boundary.  The fallback
            # is intentionally defensive for legacy import/checkpoint paths;
            # it never changes routing or publication semantics.
            try:
                from .team.contracts import CollaborationEvent

                collaboration_event = CollaborationEvent.model_validate(
                    collaboration_event
                ).model_dump(mode="json")
            except Exception:
                pass
            stage_details.setdefault("collaboration_id", team_id)
            stage_details.setdefault("sequence", self._collaboration_sequence)
            stage_details.setdefault("namespace", namespace)
            stage_details.setdefault("scope", scope)
            stage_details["collaboration_event"] = collaboration_event
        payload = {
            "event": "agent_stage",
            "event_type": "agent_stage",
            "engine": "langgraph_agent_loop",
            "run_id": self.run_id,
            "stage": stage,
            "phase": phase,
            "status": status,
            "action_id": action_id,
            "parent_action_id": parent_action_id or None,
            "task_id": task_id or None,
            "step_id": step_id or None,
            "attempt": attempt,
            "retry": retry_info,
            "recovery": recovery_info,
            "tool_call_id": tool_call_id,
            "round_id": round_id or self._round_id,
            "error_code": error_code,
            "summary": summary,
            "occurred_at": occurred_at,
            "timestamp": occurred_at,
        }
        if team_id:
            payload.update(
                {
                    "schema_version": "team.v1",
                    "collaboration_id": team_id,
                    "sequence": self._collaboration_sequence,
                    "namespace": stage_details.get("namespace"),
                    "scope": stage_details.get("scope"),
                }
            )
        if stage_details:
            payload["details"] = self._bounded_details(stage_details)
        self._stage_history.append(dict(payload))
        if self.controller is not None:
            # Stage events are the durable control-plane trace.  User-facing
            # progress is emitted below through the same ordered assistant
            # stream; the stage payload itself is metadata, not a second
            # visible message part.
            self.controller.add_data(payload)
        if normalized_user_message and not team_id:
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
