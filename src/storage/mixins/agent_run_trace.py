# -*- coding: utf-8 -*-
"""Redacted V2 run-trace persistence."""

from __future__ import annotations

from datetime import datetime
import json
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
    if isinstance(value, str) and len(value) > 20_000:
        return value[:19_000] + "…[truncated]"
    return value


def _json(value: Any) -> str:
    return json.dumps(
        redact_agent_trace(value),
        ensure_ascii=False,
        default=str,
    )


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
                record.raw_outline_json = _json(raw_outline)
            if normalized_outline is not None:
                record.normalized_outline_json = _json(normalized_outline)
            if raw_intents is not None:
                record.raw_intents_json = _json(raw_intents)
            if normalized_intents is not None:
                record.normalized_intents_json = _json(normalized_intents)
            if repairs is not None:
                record.repairs_json = _json(repairs)
            if latest_stage is not None:
                record.latest_stage_json = _json(latest_stage)
            if compiled_plan is not None:
                record.compiled_plan_json = _json(compiled_plan)
            if outcomes is not None:
                record.outcomes_json = _json(outcomes)
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


__all__ = ["AgentRunTraceMixin", "redact_agent_trace"]
