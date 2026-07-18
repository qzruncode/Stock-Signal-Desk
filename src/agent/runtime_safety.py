# -*- coding: utf-8 -*-
"""Production safety limits for the interactive Stock Agent runtime."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque


_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ALLOWED_MESSAGE_ROLES = frozenset({"user", "assistant", "tool"})
_PRODUCTION_NAMES = frozenset({"prod", "production"})


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _truthy(name: str) -> bool:
    return str(os.getenv(name, "") or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AgentRuntimeLimits:
    max_active_runs: int
    requests_per_minute: int
    max_messages: int
    max_message_chars: int
    max_request_chars: int


def get_agent_runtime_limits() -> AgentRuntimeLimits:
    """Read bounded limits from the environment on each admission decision.

    Reading at request time keeps the Web settings/runtime .env reload behavior
    predictable.  A zero rate limit disables request throttling for local
    development; production validation requires an explicit non-zero value.
    """
    return AgentRuntimeLimits(
        max_active_runs=_env_int("AGENT_MAX_ACTIVE_RUNS", 4, minimum=1, maximum=64),
        requests_per_minute=_env_int("AGENT_REQUESTS_PER_MINUTE", 0, minimum=0, maximum=10_000),
        max_messages=_env_int("AGENT_MAX_MESSAGES", 100, minimum=1, maximum=1_000),
        max_message_chars=_env_int("AGENT_MAX_MESSAGE_CHARS", 100_000, minimum=1_000, maximum=2_000_000),
        # Tool-rich assistant-ui history can legitimately exceed 300k after a
        # complete concept inventory.  Keep a hard 1 MB envelope while the
        # per-user-message, rate and concurrency limits provide tighter abuse
        # controls.
        max_request_chars=_env_int("AGENT_MAX_REQUEST_CHARS", 1_000_000, minimum=10_000, maximum=5_000_000),
    )


class AgentRequestValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_request", status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _serialized_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))
    except (TypeError, ValueError):
        return len(str(value))


def validate_chat_request_body(body: Any) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Validate an Agent request without weakening assistant-ui history support."""
    if not isinstance(body, dict):
        raise AgentRequestValidationError("请求体必须是 JSON 对象", status_code=400)

    limits = get_agent_runtime_limits()
    if _serialized_chars(body) > limits.max_request_chars:
        raise AgentRequestValidationError(
            f"请求内容过大，最多允许 {limits.max_request_chars} 个字符",
            code="request_too_large",
            status_code=413,
        )

    conversation_id_raw = body.get("conversation_id")
    conversation_id: str | None = None
    if conversation_id_raw is not None:
        conversation_id = str(conversation_id_raw).strip()
        if not _CONVERSATION_ID.fullmatch(conversation_id):
            raise AgentRequestValidationError("conversation_id 格式不合法", status_code=400)

    resume_existing = body.get("resume_existing") is True
    raw_messages = body.get("messages", [])
    if not isinstance(raw_messages, list):
        raise AgentRequestValidationError("messages 必须是数组", status_code=400)
    if len(raw_messages) > limits.max_messages:
        raise AgentRequestValidationError(
            f"对话消息过多，单次最多允许 {limits.max_messages} 条",
            code="too_many_messages",
            status_code=413,
        )
    if not resume_existing and not raw_messages:
        raise AgentRequestValidationError("messages 不能为空")

    messages: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_messages):
        if not isinstance(raw, dict):
            raise AgentRequestValidationError(f"messages[{index}] 必须是对象")
        role = str(raw.get("role") or "").strip().lower()
        if role not in _ALLOWED_MESSAGE_ROLES:
            # In particular, never accept client-supplied system/developer
            # messages after the server-owned professional system prompt.
            raise AgentRequestValidationError(
                f"messages[{index}].role 不支持: {role or '(empty)'}",
                code="unsupported_message_role",
            )
        message_chars = _serialized_chars(raw)
        # Bound fresh user input tightly.  Assistant/tool history is generated
        # by this service and can legitimately contain a complete candidate
        # inventory or structured tool evidence larger than one user message;
        # it remains bounded by the overall request limit above.
        message_limit = (
            limits.max_message_chars
            if role == "user"
            else limits.max_request_chars
        )
        if message_chars > message_limit:
            raise AgentRequestValidationError(
                f"messages[{index}] 内容过大，最多允许 {message_limit} 个字符",
                code="message_too_large",
                status_code=413,
            )
        # assistant-ui / AI SDK tool messages carry the call id inside the
        # content part as ``toolCallId`` instead of a top-level
        # ``tool_call_id``.  The server's normalization layer validates and
        # converts that shape before the model call, so rejecting it here
        # breaks legitimate multi-turn conversations with tool cards.
        messages.append(dict(raw))

    if not resume_existing and not any(message.get("role") == "user" for message in messages):
        raise AgentRequestValidationError("新一轮对话必须包含 user 消息")
    return messages, conversation_id, resume_existing


class AgentRequestRateLimiter:
    """Small single-process sliding-window limiter.

    The Agent runtime deliberately supports one ASGI worker.  A threading lock
    makes this safe across request threads without binding a global asyncio
    primitive to a particular test/server event loop.
    """

    def __init__(self) -> None:
        self._events: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check_and_record(self, key: str, *, limit: int, window_seconds: float = 60.0) -> int:
        """Return retry-after seconds, or zero when the request is admitted."""
        if limit <= 0:
            return 0
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return max(1, int(window_seconds - (now - events[0])) + 1)
            events.append(now)
        return 0

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


agent_request_rate_limiter = AgentRequestRateLimiter()


def configured_worker_count() -> int:
    """Best-effort detection for common ASGI/Gunicorn worker settings."""
    counts: list[int] = []
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        raw = str(os.getenv(name, "") or "").strip()
        if raw:
            try:
                counts.append(int(raw))
            except ValueError:
                counts.append(2)  # invalid is unsafe; fail closed below
    gunicorn_args = str(os.getenv("GUNICORN_CMD_ARGS", "") or "")
    match = re.search(r"(?:--workers(?:=|\s+)|-w\s+)(\d+)", gunicorn_args)
    if match:
        counts.append(int(match.group(1)))
    return max(counts, default=1)


def is_production_environment() -> bool:
    name = str(os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    return name in _PRODUCTION_NAMES or _truthy("DSA_PRODUCTION")


def agent_production_issues(static_dir: Path | None = None) -> list[str]:
    """Return actionable runtime issues; empty means the checked contract holds."""
    issues: list[str] = []
    if configured_worker_count() != 1:
        issues.append(
            "Agent active-run state is process-local; configure exactly one ASGI worker "
            "(WEB_CONCURRENCY=1 / UVICORN_WORKERS=1)"
        )

    if not is_production_environment():
        return issues

    from src.auth import has_stored_password, is_auth_enabled

    limits = get_agent_runtime_limits()
    if not is_auth_enabled():
        issues.append("ADMIN_AUTH_ENABLED=true is required in production")
    elif not has_stored_password():
        issues.append("an admin password must be initialized before production startup")
    if _truthy("CORS_ALLOW_ALL"):
        issues.append("CORS_ALLOW_ALL must be false in production")
    if _truthy("WEBFETCH_ALLOW_PRIVATE"):
        issues.append("WEBFETCH_ALLOW_PRIVATE must be false in production")
    if limits.requests_per_minute <= 0:
        issues.append("AGENT_REQUESTS_PER_MINUTE must be greater than zero in production")
    if static_dir is not None:
        index_path = static_dir / "index.html"
        if not index_path.is_file():
            issues.append(f"production frontend bundle is missing: {index_path}")
    try:
        from src.llm.anthropic_gateway import resolve_anthropic_gateway_config

        resolve_anthropic_gateway_config()
    except Exception as exc:  # config error type intentionally stays internal
        issues.append(f"Agent model configuration is invalid: {exc}")
    return issues


def enforce_agent_runtime_configuration(static_dir: Path | None = None) -> None:
    issues = agent_production_issues(static_dir)
    if issues:
        raise RuntimeError("Agent runtime preflight failed: " + "; ".join(issues))


__all__ = [
    "AgentRequestValidationError",
    "AgentRuntimeLimits",
    "agent_production_issues",
    "agent_request_rate_limiter",
    "configured_worker_count",
    "enforce_agent_runtime_configuration",
    "get_agent_runtime_limits",
    "is_production_environment",
    "validate_chat_request_body",
]
