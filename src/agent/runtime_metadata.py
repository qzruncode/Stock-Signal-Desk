"""Small, generic projections for one Agent run's execution conditions.

This module deliberately contains no persistence and no domain fields.  It
keeps the request/trace writers aligned on the same safe vocabulary while
leaving the existing AgentRun, event, step, and trace records as the durable
authorities.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, fields, is_dataclass
from functools import lru_cache
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence


RUNTIME_METADATA_SCHEMA_VERSION = "agent-run-runtime.v1"
_FINGERPRINT_REQUEST_FIELDS = (
    "agent_mode",
    "planning_mode",
    "history_mode",
    "stream_presentation",
)
_MODEL_CONFIG_FIELDS = (
    "model",
    "custom_llm_provider",
    "provider",
    "context_window",
    "max_tokens",
    "temperature",
    "top_p",
)


def sha256_text(value: Any) -> str:
    """Return a stable digest without retaining the source text."""
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth >= 10:
        return "[depth-truncated]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth=depth + 1) for item in value[:10_000]]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump(mode="json"), depth=depth + 1)
    return str(value)


def _serialized_character_count(value: Any) -> int:
    try:
        return len(
            json.dumps(
                _json_safe(value),
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError):
        return len(str(value or ""))


def _message_fingerprint_projection(message: Mapping[str, Any]) -> dict[str, Any]:
    """Remove transport ids/timestamps while preserving task semantics."""
    projected: dict[str, Any] = {
        "role": str(message.get("role") or "").strip().lower(),
        "content": _json_safe(message.get("content")),
    }
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, (list, tuple)):
        projected["tool_calls"] = [
            {
                "name": str(call.get("name") or "")[:160],
                "args": _json_safe(call.get("args") or {}),
            }
            for call in tool_calls
            if isinstance(call, Mapping)
        ]
    return projected


def task_fingerprint(
    messages: Sequence[Mapping[str, Any]],
    request_body: Mapping[str, Any],
) -> str:
    """Fingerprint the logical request, excluding unstable run identifiers."""
    payload = {
        "messages": [
            _message_fingerprint_projection(message)
            for message in messages
            if isinstance(message, Mapping)
        ],
        "request": {
            key: _json_safe(request_body.get(key))
            for key in _FINGERPRINT_REQUEST_FIELDS
            if request_body.get(key) is not None
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256_text(encoded)


def safe_model_config(llm_config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project provider configuration without credentials or headers."""
    source = llm_config if isinstance(llm_config, Mapping) else {}
    projected: dict[str, Any] = {}
    for key in _MODEL_CONFIG_FIELDS:
        value = source.get(key)
        if value is not None and value != "":
            projected[key] = _json_safe(value)
    provider = projected.get("custom_llm_provider") or projected.get("provider")
    if provider:
        projected["provider"] = str(provider)
    projected.pop("custom_llm_provider", None)
    return projected


def _limits_projection(limits: Any | None) -> dict[str, Any]:
    if limits is None:
        return {}
    if is_dataclass(limits):
        source = asdict(limits)
    else:
        source = {
            field.name: getattr(limits, field.name, None)
            for field in fields(limits)
        } if hasattr(limits, "__dataclass_fields__") else {}
    return {
        str(key): _json_safe(value)
        for key, value in source.items()
        if value is not None
    }


@lru_cache(maxsize=1)
def code_version() -> dict[str, str]:
    """Read deployment-provided code identity without storing repository data."""
    commit = next(
        (
            str(os.getenv(name) or "").strip()
            for name in ("AGENT_CODE_COMMIT", "AGENT_CODE_VERSION", "GIT_COMMIT", "COMMIT_SHA")
            if str(os.getenv(name) or "").strip()
        ),
        "",
    )
    if not commit:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True,
                check=False,
                text=True,
                timeout=0.5,
            )
            commit = str(result.stdout or "").strip()
        except (OSError, subprocess.SubprocessError):
            commit = ""
    return {
        "commit": (commit or "unknown")[:160],
        "application": str(os.getenv("AGENT_APP_VERSION") or "1.0.0")[:64],
    }


def prompt_metadata(
    content: str,
    *,
    record: Any | None = None,
    is_fallback: bool = False,
) -> dict[str, Any]:
    updated_at = getattr(record, "updated_at", None) if record is not None else None
    version = updated_at.isoformat() if hasattr(updated_at, "isoformat") else None
    return {
        "template_id": getattr(record, "id", None),
        "template_name": str(getattr(record, "name", "") or "")[:160] or None,
        "version": version or ("source-default" if is_fallback else "unknown"),
        "sha256": sha256_text(content),
        "character_count": len(str(content or "")),
        "is_fallback": bool(is_fallback),
    }


def build_run_runtime_metadata(
    *,
    messages: Sequence[Mapping[str, Any]],
    request_body: Mapping[str, Any],
    llm_config: Mapping[str, Any] | None,
    limits: Any | None = None,
    tool_catalog_version: str | None = None,
) -> dict[str, Any]:
    model_config = safe_model_config(llm_config)
    return {
        "schema_version": RUNTIME_METADATA_SCHEMA_VERSION,
        "task_fingerprint": task_fingerprint(messages, request_body),
        "request_mode": "resume" if request_body.get("resume_existing") else "new",
        "requested_agent_mode": str(request_body.get("agent_mode") or "auto")[:32],
        "actual_agent_mode": None,
        "planning_mode": str(request_body.get("planning_mode") or "auto")[:32],
        "history_mode": str(request_body.get("history_mode") or "auto")[:32],
        "model": model_config.get("model"),
        "provider": model_config.get("provider"),
        "context_window": model_config.get("context_window"),
        "tool_catalog_version": str(tool_catalog_version or "unknown")[:160],
        "versions": {
            "runtime": "langgraph_agent_loop",
            "schema": RUNTIME_METADATA_SCHEMA_VERSION,
            "code": code_version(),
        },
        "budget": _limits_projection(limits),
        "context": {
            "input_message_count": len([item for item in messages if isinstance(item, Mapping)]),
            "input_character_count": _serialized_character_count(messages),
        },
        "attempt": 1,
    }


def merge_runtime_metadata(
    base: Mapping[str, Any] | None,
    updates: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Deep-merge small metadata envelopes while preserving prior facts."""
    merged = dict(base or {})
    for key, value in (updates or {}).items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = merge_runtime_metadata(merged[key], value)
        else:
            merged[key] = _json_safe(value)
    return merged


__all__ = [
    "RUNTIME_METADATA_SCHEMA_VERSION",
    "build_run_runtime_metadata",
    "code_version",
    "merge_runtime_metadata",
    "prompt_metadata",
    "safe_model_config",
    "sha256_text",
    "task_fingerprint",
]
