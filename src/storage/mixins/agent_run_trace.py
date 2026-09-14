# -*- coding: utf-8 -*-
"""Redacted trace persistence for the generic Agent loop.

Legacy trace columns remain in the database so historical runs can be shown
read-only, but this writer deliberately has no parameters that can populate
the retired planner/verification payloads.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
import re
from typing import Any, Mapping
import uuid

from sqlalchemy import delete, select

from src.storage.models import AgentRun, AgentRunTrace


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
    }
)
_EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w.-]{1,190}\.[A-Za-z]{2,24}")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization)" r"(\s*[:=]\s*)([^\s,;\"']{6,})"
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
            if normalized in _SENSITIVE_KEYS or any(normalized.endswith(f"_{suffix}") for suffix in _SENSITIVE_KEYS):
                result[text_key] = "[redacted]"
            else:
                result[text_key] = redact_agent_trace(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_agent_trace(item, depth=depth + 1) for item in value[:10_000]]
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

        encrypted = Fernet(key.encode("ascii")).encrypt(payload.encode("utf-8"))
        return "enc:v1:" + encrypted.decode("ascii")
    except Exception as exc:
        raise RuntimeError("AGENT_TRACE_ENCRYPTION_KEY is invalid") from exc


def _json(value: Any, *, encrypt: bool = False) -> str:
    return encode_agent_trace_json(value, encrypt=encrypt)


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
        latest_stage: Any = None,
        quality_projection: Any = None,
    ) -> None:
        with self.session_scope() as session:
            record = (
                session.execute(
                    select(AgentRunTrace).where(
                        AgentRunTrace.run_id == run_id,
                        AgentRunTrace.orchestrator_mode == orchestrator_mode,
                    )
                )
                .scalars()
                .first()
            )
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
            if latest_stage is not None:
                record.latest_stage_json = _json(latest_stage)
            if quality_projection is not None:
                current_projection: dict[str, Any] = {}
                try:
                    parsed = json.loads(
                        record.quality_projection_json or "{}"
                    )
                    if isinstance(parsed, dict):
                        current_projection = parsed
                except (TypeError, ValueError):
                    current_projection = {}
                if not isinstance(quality_projection, Mapping):
                    raise ValueError(
                        "quality_projection must be a mapping"
                    )
                record.quality_projection_json = _json(
                    {
                        **current_projection,
                        **redact_agent_trace(quality_projection),
                    }
                )
            record.updated_at = datetime.now()

    def get_latest_agent_run_trace(
        self,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        with self.session_scope() as session:
            record = (
                session.execute(
                    select(AgentRunTrace)
                    .where(AgentRunTrace.conversation_id == conversation_id)
                    .order_by(
                        AgentRunTrace.created_at.desc(),
                        AgentRunTrace.updated_at.desc(),
                    )
                )
                .scalars()
                .first()
            )
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
            execution_trace = None
            quality_projection = None
            if record.quality_projection_json:
                try:
                    projection = json.loads(record.quality_projection_json)
                    if isinstance(projection, dict):
                        quality_projection = projection
                        candidate = projection.get("execution_trace")
                        if isinstance(candidate, dict):
                            execution_trace = candidate
                except (TypeError, ValueError):
                    execution_trace = None
            return {
                "run_id": record.run_id,
                "status": record.status,
                "error_code": record.error_code,
                "latest_stage": latest_stage,
                "execution_trace": execution_trace,
                # Kept internal for the conversation presentation layer to
                # derive bounded previews from historical canonical results.
                "quality_projection": quality_projection,
                "updated_at": (record.updated_at.isoformat() if record.updated_at is not None else None),
            }

    def list_agent_run_trace_history(
        self,
        conversation_id: str,
        *,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        """Return one compact trace envelope for each run in a conversation.

        A conversation contains multiple durable turns, while the old
        conversation endpoint exposed only the newest ``AgentRunTrace``.  The
        resulting UI could render the latest execution process, but had no
        trace to attach to earlier assistant messages.  Join the trace with
        its run here so the presentation layer can bind each process to the
        run's canonical ``final_text`` without importing the legacy thread
        snapshot.
        """
        safe_limit = max(1, min(int(limit), 64))
        with self.session_scope() as session:
            rows = session.execute(
                select(AgentRunTrace, AgentRun)
                .join(AgentRun, AgentRun.id == AgentRunTrace.run_id)
                .where(
                    AgentRunTrace.conversation_id == conversation_id,
                    AgentRunTrace.orchestrator_mode == "langgraph_agent_loop",
                )
                .order_by(
                    AgentRun.created_at.asc(),
                    AgentRunTrace.created_at.asc(),
                    AgentRunTrace.updated_at.asc(),
                )
                .limit(safe_limit)
            ).all()

            history: list[dict[str, Any]] = []
            for trace_record, run_record in rows:
                latest_stage = None
                if trace_record.latest_stage_json:
                    try:
                        parsed = json.loads(trace_record.latest_stage_json)
                        if isinstance(parsed, dict):
                            latest_stage = parsed
                    except (TypeError, ValueError):
                        latest_stage = None

                execution_trace = None
                quality_projection = None
                if trace_record.quality_projection_json:
                    try:
                        projection = json.loads(trace_record.quality_projection_json)
                        if isinstance(projection, dict):
                            quality_projection = projection
                            candidate = projection.get("execution_trace")
                            if isinstance(candidate, dict):
                                execution_trace = candidate
                    except (TypeError, ValueError):
                        execution_trace = None

                history.append(
                    {
                        "run_id": trace_record.run_id,
                        "status": trace_record.status,
                        "error_code": trace_record.error_code,
                        "latest_stage": latest_stage,
                        "execution_trace": execution_trace,
                        "quality_projection": quality_projection,
                        "final_text": run_record.final_text or "",
                        "created_at": (
                            run_record.created_at.isoformat()
                            if run_record.created_at is not None
                            else None
                        ),
                        "updated_at": (
                            trace_record.updated_at.isoformat()
                            if trace_record.updated_at is not None
                            else None
                        ),
                    }
                )
            return history

    def prune_agent_run_traces(
        self,
        conversation_id: str,
        keep_run_ids: list[str] | tuple[str, ...],
    ) -> int:
        keep = tuple(dict.fromkeys(str(value).strip() for value in keep_run_ids if str(value).strip()))
        with self.session_scope() as session:
            statement = delete(AgentRunTrace).where(AgentRunTrace.conversation_id == conversation_id)
            if keep:
                statement = statement.where(AgentRunTrace.run_id.not_in(keep))
            result = session.execute(statement)
            return result.rowcount or 0


__all__ = [
    "AgentRunTraceMixin",
    "encode_agent_trace_json",
    "redact_agent_trace",
]
