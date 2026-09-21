"""Structured, redacted runtime failures for Agent execution records.

Tool failures are recoverable observations, not reasons to hide the run.  This
module keeps the error envelope independent from the graph and HTTP layers so
the same receipt can be attached to Direct, Plan, Team, and Review runs.
"""

from __future__ import annotations

from datetime import datetime
import re
import traceback
from typing import Any, Mapping
import uuid

from pydantic import BaseModel, ConfigDict, Field


_SENSITIVE_KEYS = frozenset(
    {"api_key", "apikey", "authorization", "cookie", "password", "secret", "token"}
)
_EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w.-]{1,190}\.[A-Za-z]{2,24}")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)([^\s,;\"']{6,})"
)


def _fallback_redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 16:
        return "[depth-truncated]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).replace("-", "_").lower()
            if normalized in _SENSITIVE_KEYS or any(normalized.endswith(f"_{suffix}") for suffix in _SENSITIVE_KEYS):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _fallback_redact(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_fallback_redact(item, depth=depth + 1) for item in value[:10_000]]
    if isinstance(value, str):
        redacted = _EMAIL_PATTERN.sub("[email-redacted]", value)
        redacted = _PHONE_PATTERN.sub("[phone-redacted]", redacted)
        redacted = _BEARER_PATTERN.sub("[authorization-redacted]", redacted)
        return _SECRET_ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group(1)}{match.group(2)}[redacted]",
            redacted,
        )
    return value


def _redact(value: Any) -> Any:
    """Reuse storage redaction without importing the whole storage package."""
    try:
        from src.storage.mixins.agent_run_trace import redact_agent_trace

        return redact_agent_trace(value)
    except Exception:
        # Runtime receipts must remain importable for isolated worker/tests
        # even when the optional database/LangChain stack is unavailable.
        return _fallback_redact(value)


def _safe_text(value: Any, limit: int = 1_200) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    redacted = _redact(text)
    return str(redacted or "")[:limit]


def _safe_traceback(error: BaseException, limit: int = 12_000) -> str:
    try:
        rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    except Exception:
        rendered = f"{type(error).__name__}: {error}"
    return _safe_text(rendered, limit)


class RuntimeErrorReceipt(BaseModel):
    """Bounded error provenance safe to persist and inspect in Run Explorer."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="runtime-error.v1", max_length=32)
    error_id: str = Field(default_factory=lambda: f"err_{uuid.uuid4().hex}", max_length=96)
    run_id: str = Field(default="", max_length=96)
    conversation_id: str = Field(default="", max_length=96)
    collaboration_id: str = Field(default="", max_length=96)
    scope: str = Field(default="coordinator", max_length=32)
    agent_id: str = Field(default="", max_length=96)
    task_id: str = Field(default="", max_length=96)
    node: str = Field(default="", max_length=128)
    phase: str = Field(default="", max_length=64)
    tool_name: str = Field(default="", max_length=128)
    tool_call_id: str = Field(default="", max_length=192)
    action_id: str = Field(default="", max_length=192)
    attempt: int = Field(default=1, ge=0, le=64)
    failure_kind: str = Field(default="runtime", max_length=48)
    error_code: str = Field(default="runtime_error", max_length=128)
    exception_type: str = Field(default="RuntimeError", max_length=160)
    message: str = Field(default="", max_length=1_200)
    sanitized_traceback: str = Field(default="", max_length=12_000)
    retryable: bool = False
    fallback_eligible: bool = False
    fallback_status: str = Field(default="not_attempted", max_length=32)
    fallback_call_ids: list[str] = Field(default_factory=list, max_length=16)
    parent_event_id: str = Field(default="", max_length=128)
    terminal_impact: str = Field(default="recoverable", max_length=32)
    occurred_at: str = Field(
        default_factory=lambda: datetime.now().astimezone().isoformat(),
        max_length=64,
    )
    details: dict[str, Any] = Field(default_factory=dict, max_length=24)


def build_runtime_error_receipt(
    error: BaseException,
    *,
    run_id: str = "",
    conversation_id: str = "",
    collaboration_id: str = "",
    scope: str = "coordinator",
    agent_id: str = "",
    task_id: str = "",
    node: str = "",
    phase: str = "",
    tool_name: str = "",
    tool_call_id: str = "",
    action_id: str = "",
    attempt: int = 1,
    failure_kind: str = "runtime",
    error_code: str = "runtime_error",
    retryable: bool = False,
    fallback_eligible: bool = False,
    fallback_status: str = "not_attempted",
    fallback_call_ids: list[str] | None = None,
    parent_event_id: str = "",
    terminal_impact: str = "recoverable",
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one bounded JSON-safe receipt without exposing raw secrets."""

    receipt = RuntimeErrorReceipt(
        run_id=_safe_text(run_id, 96),
        conversation_id=_safe_text(conversation_id, 96),
        collaboration_id=_safe_text(collaboration_id, 96),
        scope=_safe_text(scope, 32) or "coordinator",
        agent_id=_safe_text(agent_id, 96),
        task_id=_safe_text(task_id, 96),
        node=_safe_text(node, 128),
        phase=_safe_text(phase, 64),
        tool_name=_safe_text(tool_name, 128),
        tool_call_id=_safe_text(tool_call_id, 192),
        action_id=_safe_text(action_id, 192),
        attempt=max(0, min(64, int(attempt or 0))),
        failure_kind=_safe_text(failure_kind, 48) or "runtime",
        error_code=_safe_text(error_code, 128) or "runtime_error",
        exception_type=_safe_text(type(error).__name__, 160) or "RuntimeError",
        message=_safe_text(error, 1_200),
        sanitized_traceback=_safe_traceback(error),
        retryable=bool(retryable),
        fallback_eligible=bool(fallback_eligible),
        fallback_status=_safe_text(fallback_status, 32) or "not_attempted",
        fallback_call_ids=[_safe_text(value, 192) for value in (fallback_call_ids or []) if _safe_text(value, 192)][:16],
        parent_event_id=_safe_text(parent_event_id, 128),
        terminal_impact=_safe_text(terminal_impact, 32) or "recoverable",
        details=dict(_redact(dict(details or {}))),
    )
    payload = receipt.model_dump(mode="json")
    # Runtime errors share the existing reducer contract used by durable
    # records.  A stable id keeps separate retry attempts instead of letting
    # the latest attempt overwrite the previous receipt.
    payload["id"] = payload["error_id"]
    return payload


def emit_runtime_error(
    events: Any,
    error: BaseException,
    *,
    summary: str = "运行时异常已记录",
    error_code: str = "runtime_error",
    failure_kind: str = "runtime",
    retryable: bool = False,
    fallback_eligible: bool = False,
    fallback_status: str = "not_attempted",
    terminal_impact: str = "recoverable",
    **context: Any,
) -> dict[str, Any]:
    """Persist and project one runtime receipt through the existing event bridge."""

    receipt = build_runtime_error_receipt(
        error,
        error_code=error_code,
        failure_kind=failure_kind,
        retryable=retryable,
        fallback_eligible=fallback_eligible,
        fallback_status=fallback_status,
        terminal_impact=terminal_impact,
        **context,
    )
    if events is not None:
        details = {
            "kind": "runtime_error",
            "runtime_error": receipt,
            **{
                key: value
                for key, value in context.items()
                if key not in {"details"}
            },
        }
        if isinstance(context.get("details"), Mapping):
            details["details"] = dict(_redact(dict(context["details"])))
        events.stage(
            "runtime_error",
            "failed",
            _safe_text(summary, 1_000) or "运行时异常已记录",
            action_id=receipt.get("action_id") or None,
            tool_call_id=receipt.get("tool_call_id") or None,
            error_code=receipt.get("error_code") or None,
            details=details,
        )
    return receipt


__all__ = [
    "RuntimeErrorReceipt",
    "build_runtime_error_receipt",
    "emit_runtime_error",
]
