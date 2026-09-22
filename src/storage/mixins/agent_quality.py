# -*- coding: utf-8 -*-
"""Agent evaluation cases, deterministic scores and explicit feedback."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
from typing import Any, Mapping, Sequence
import uuid

from sqlalchemy import func, select

from src.agent.evaluation import (
    EVALUATOR_VERSION,
    score_agent_run_snapshot,
)
from src.storage.mixins.agent_run_trace import redact_agent_trace
from src.storage.models import (
    AgentArtifact,
    AgentEvaluationCase,
    AgentEvaluationResult,
    AgentRun,
    AgentRunEvent,
    AgentRunFeedback,
    AgentRunTrace,
    AgentStepExecution,
)


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _json(value: Any) -> str:
    return json.dumps(
        redact_agent_trace(value),
        ensure_ascii=False,
        default=str,
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _case_dict(record: AgentEvaluationCase) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "owner_id": record.owner_id,
        "suite": record.suite,
        "name": record.name,
        "description": record.description,
        "status": record.status,
        "version": int(record.version or 1),
        "source_run_id": record.source_run_id,
        "request_snapshot": _load_json(
            record.request_snapshot_json,
            {},
        ),
        "expectations": _load_json(record.expectations_json, {}),
        "tags": _load_json(record.tags_json, []),
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
    }


def _result_dict(record: AgentEvaluationResult) -> dict[str, Any]:
    return {
        "id": record.id,
        "case_id": record.case_id,
        "candidate_run_id": record.candidate_run_id,
        "evaluator_version": record.evaluator_version,
        "status": record.status,
        "total_score": float(record.total_score or 0.0),
        "scores": _load_json(record.scores_json, {}),
        "violations": _load_json(record.violations_json, []),
        "created_at": _iso(record.created_at),
    }


def _feedback_dict(record: AgentRunFeedback | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "run_id": record.run_id,
        "conversation_id": record.conversation_id,
        "rating": int(record.rating),
        "category": record.category,
        "comment": record.comment,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
    }



from ._agent_quality_methods1 import _AgentQualityMethods1
from ._agent_quality_methods2 import _AgentQualityMethods2


class AgentQualityMixin(
    _AgentQualityMethods1,
    _AgentQualityMethods2,
):
    """Persistence API used by quality dashboards and release gates."""


__all__ = ["AgentQualityMixin"]
