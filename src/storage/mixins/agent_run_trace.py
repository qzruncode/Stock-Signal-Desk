# -*- coding: utf-8 -*-
"""Redacted V2 run-trace persistence."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
import re
from typing import Any, Mapping
import uuid

from sqlalchemy import delete, select

from src.storage.models import AgentRunTrace


_SENSITIVE_KEYS = frozenset({
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
})
_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]{1,64}@[\w.-]{1,190}\.[A-Za-z]{2,24}"
)
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"
)
_BEARER_PATTERN = re.compile(
    r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization)"
    r"(\s*[:=]\s*)([^\s,;\"']{6,})"
)


def _redact_string(value: str) -> str:
    redacted = _EMAIL_PATTERN.sub("[email-redacted]", value)
    redacted = _PHONE_PATTERN.sub("[phone-redacted]", redacted)
    redacted = _BEARER_PATTERN.sub("[authorization-redacted]", redacted)
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[redacted]",
        redacted,
    )
    if len(redacted) > 20_000:
        return redacted[:19_000] + "…[truncated]"
    return redacted


def redact_agent_trace(value: Any, *, depth: int = 0) -> Any:
    if depth > 16:
        return "[depth-truncated]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            normalized = text_key.replace("-", "_").lower()
            if normalized in _SENSITIVE_KEYS or any(
                normalized.endswith(f"_{suffix}")
                for suffix in _SENSITIVE_KEYS
            ):
                result[text_key] = "[redacted]"
            else:
                result[text_key] = redact_agent_trace(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [
            redact_agent_trace(item, depth=depth + 1)
            for item in value[:10_000]
        ]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def encode_agent_trace_json(
    value: Any,
    *,
    encrypt: bool = False,
) -> str:
    payload = json.dumps(
        redact_agent_trace(value),
        ensure_ascii=False,
        default=str,
    )
    if not encrypt:
        return payload
    key = (os.getenv("AGENT_TRACE_ENCRYPTION_KEY") or "").strip()
    if not key:
        return payload
    try:
        from cryptography.fernet import Fernet

        encrypted = Fernet(key.encode("ascii")).encrypt(
            payload.encode("utf-8")
        )
        return "enc:v1:" + encrypted.decode("ascii")
    except Exception as exc:
        raise RuntimeError("AGENT_TRACE_ENCRYPTION_KEY is invalid") from exc


def _json(value: Any, *, encrypt: bool = False) -> str:
    return encode_agent_trace_json(value, encrypt=encrypt)


def _raw_trace_value(value: Any) -> Any:
    store_raw = str(os.getenv("AGENT_TRACE_STORE_RAW", "")).strip().lower()
    production = str(
        os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or ""
    ).strip().lower() in {"prod", "production"}
    if store_raw in {"0", "false", "no", "off"} or (
        production and store_raw not in {"1", "true", "yes", "on"}
    ):
        serialized = json.dumps(
            redact_agent_trace(value),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return {
            "omitted": True,
            "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "reason": "raw_trace_storage_disabled",
        }
    return value


class AgentRunTraceMixin:
    def upsert_agent_run_trace(
        self,
        *,
        run_id: str,
        conversation_id: str,
        orchestrator_mode: str,
        status: str,
        error_code: str | None = None,
        schema_version: str | None = None,
        model_config: Any = None,
        stage_durations: Any = None,
        raw_outline: Any = None,
        normalized_outline: Any = None,
        raw_intents: Any = None,
        normalized_intents: Any = None,
        repairs: Any = None,
        verification: Any = None,
        latest_stage: Any = None,
        compiled_plan: Any = None,
        outcomes: Any = None,
        coverage: Any = None,
    ) -> None:
        with self.session_scope() as session:
            record = session.execute(
                select(AgentRunTrace).where(
                    AgentRunTrace.run_id == run_id,
                    AgentRunTrace.orchestrator_mode == orchestrator_mode,
                )
            ).scalars().first()
            if record is None:
                record = AgentRunTrace(
                    id=uuid.uuid4().hex,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    orchestrator_mode=orchestrator_mode,
                    status=status,
                )
                session.add(record)
            record.status = status
            record.error_code = error_code
            if schema_version is not None:
                record.schema_version = schema_version
            if model_config is not None:
                record.model_config_json = _json(model_config)
            if stage_durations is not None:
                record.stage_durations_json = _json(stage_durations)
            if raw_outline is not None:
                record.raw_outline_json = _json(
                    _raw_trace_value(raw_outline),
                    encrypt=True,
                )
            if normalized_outline is not None:
                record.normalized_outline_json = _json(
                    normalized_outline,
                    encrypt=True,
                )
            if raw_intents is not None:
                record.raw_intents_json = _json(
                    _raw_trace_value(raw_intents),
                    encrypt=True,
                )
            if normalized_intents is not None:
                record.normalized_intents_json = _json(
                    normalized_intents,
                    encrypt=True,
                )
            if repairs is not None:
                record.repairs_json = _json(repairs)
            if verification is not None:
                record.verification_json = _json(
                    verification,
                    encrypt=True,
                )
            if latest_stage is not None:
                record.latest_stage_json = _json(latest_stage)
            if compiled_plan is not None:
                record.compiled_plan_json = _json(compiled_plan, encrypt=True)
            if outcomes is not None:
                record.outcomes_json = _json(outcomes, encrypt=True)
            if coverage is not None:
                record.coverage_json = _json(coverage)
            record.updated_at = datetime.now()

    def get_latest_agent_run_trace(
        self,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        with self.session_scope() as session:
            record = session.execute(
                select(AgentRunTrace)
                .where(AgentRunTrace.conversation_id == conversation_id)
                .order_by(
                    AgentRunTrace.created_at.desc(),
                    AgentRunTrace.updated_at.desc(),
                )
            ).scalars().first()
            if record is None:
                return None
            latest_stage = None
            if record.latest_stage_json:
                try:
                    parsed = json.loads(record.latest_stage_json)
                    if isinstance(parsed, dict):
                        latest_stage = parsed
                except (TypeError, ValueError):
                    latest_stage = None
            return {
                "run_id": record.run_id,
                "status": record.status,
                "error_code": record.error_code,
                "latest_stage": latest_stage,
                "updated_at": (
                    record.updated_at.isoformat()
                    if record.updated_at is not None
                    else None
                ),
            }

    def prune_agent_run_traces(
        self,
        conversation_id: str,
        keep_run_ids: list[str] | tuple[str, ...],
    ) -> int:
        keep = tuple(dict.fromkeys(
            str(value).strip()
            for value in keep_run_ids
            if str(value).strip()
        ))
        with self.session_scope() as session:
            statement = delete(AgentRunTrace).where(
                AgentRunTrace.conversation_id == conversation_id
            )
            if keep:
                statement = statement.where(AgentRunTrace.run_id.not_in(keep))
            result = session.execute(statement)
            return result.rowcount or 0


__all__ = [
    "AgentRunTraceMixin",
    "encode_agent_trace_json",
    "redact_agent_trace",
]
