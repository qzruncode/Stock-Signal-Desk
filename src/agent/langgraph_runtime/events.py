"""Presentation adapter from generic graph activity to assistant-stream chunks."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Mapping, Sequence


_CLIENT_STAGE_HISTORY_MAX_EVENTS = 120
_CLIENT_STAGE_HISTORY_MAX_BYTES = 160_000
_CLIENT_STAGE_DETAIL_MAX_BYTES = 24_000


def _client_stage_value(value: Any, *, depth: int = 0) -> Any:
    """Project one stage detail for conversation hydration.

    The durable event table keeps the full, ordered audit trail.  This
    projection exists solely for the browser's timeline, where replaying many
    verbose historical details can otherwise turn one long conversation into a
    multi-megabyte React tree.
    """
    if depth >= 7:
        return "[详情已折叠]"
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


class GraphEventBridge:
    # Stage events are a presentation channel.  The complete tool result and
    # audit trace remain in the durable run state; sending an unbounded copy
    # of them through every browser event can freeze the chat renderer.
    _MAX_DETAIL_BYTES = 24_000
    _MAX_DETAIL_KEYS = 24

    def __init__(self, controller: Any | None, *, run_id: str) -> None:
        self.controller = controller
        self.run_id = run_id
        self._stage_history: list[dict[str, Any]] = []
        self._round_id: str | None = None

    def set_round(self, round_id: str | int | None) -> None:
        self._round_id = str(round_id) if round_id not in (None, "") else None

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
    ) -> dict[str, Any]:
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
        if details:
            payload["details"] = self._bounded_details(details)
        self._stage_history.append(dict(payload))
        if self.controller is not None:
            self.controller.add_data(payload)
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

    def text(self, text: str) -> None:
        if self.controller is not None and text:
            self.controller.append_text(text)

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
