# -*- coding: utf-8 -*-
"""Authorization and audit controls for built-in Agent capabilities."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from typing import Any, Mapping, Sequence
import uuid

from sqlalchemy import or_, select

from src.storage.mixins.agent_run_trace import redact_agent_trace
from src.storage.models import (
    AgentApprovalReceipt,
    AgentAuditEvent,
    AgentCapabilityGrant,
    AgentCapabilityRelease,
    AgentEvaluationCase,
    AgentEvaluationResult,
    AgentRun,
    AgentUserMemory,
)


_DEFAULT_DENIED_EFFECTS = frozenset({"external", "trade"})


def _json(value: Any) -> str:
    return json.dumps(
        redact_agent_trace(value),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None



from ._agent_governance_methods1 import _AgentGovernanceMethods1
from ._agent_governance_methods2 import _AgentGovernanceMethods2
from ._agent_governance_methods3 import _AgentGovernanceMethods3
from ._agent_governance_methods4 import _AgentGovernanceMethods4


class AgentGovernanceMixin(
    _AgentGovernanceMethods1,
    _AgentGovernanceMethods2,
    _AgentGovernanceMethods3,
    _AgentGovernanceMethods4,
):
    """Database-enforced grants, approvals and append-only audit events."""


__all__ = ["AgentGovernanceMixin"]
