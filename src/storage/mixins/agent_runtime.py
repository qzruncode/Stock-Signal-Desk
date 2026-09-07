# -*- coding: utf-8 -*-
"""Durable Agent run, event, step and effect-outbox persistence.

The methods in this module are deliberately synchronous SQLAlchemy operations.
Async request handlers call them through ``asyncio.to_thread`` where a database
round trip is not already on a worker thread.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from typing import Any, Mapping, Sequence

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from src.storage.models import (
    AgentArtifact,
    AgentFinancialConclusion,
    AgentCircuitBreaker,
    AgentEffectOutbox,
    AgentRateLimitBucket,
    AgentResourceLease,
    AgentRuntimeControl,
    AgentRun,
    AgentRunEvent,
    AgentRunTrace,
    AgentStepExecution,
    ChatConversation,
    ChatMessage,
)


_ACTIVE_RUN_STATUSES = ("queued", "running", "recovering", "interrupted")
_TERMINAL_RUN_STATUSES = ("completed", "partial", "failed", "cancelled", "blocked")
_RUNNING_STEP_STATUSES = ("pending", "running", "retry_wait")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _run_dict(record: AgentRun) -> dict[str, Any]:
    return {
        "run_id": record.id,
        "conversation_id": record.conversation_id,
        "tenant_id": record.tenant_id,
        "owner_id": record.owner_id,
        "status": record.status,
        "request": _load_json(record.request_json, {}),
        "context_snapshot": _load_json(record.context_snapshot_json),
        "result": _load_json(record.result_json),
        "final_text": record.final_text,
        "error_code": record.error_code,
        "error_detail": record.error_detail,
        "worker_id": record.worker_id,
        "lease_expires_at": record.lease_expires_at,
        "heartbeat_at": record.heartbeat_at,
        "cancel_requested": bool(record.cancel_requested),
        "event_cursor": int(record.event_cursor or 0),
        "attempt": int(record.attempt or 1),
        "tool_call_count": int(record.tool_call_count or 0),
        "provider_call_count": int(record.provider_call_count or 0),
        "estimated_token_count": int(record.estimated_token_count or 0),
        "estimated_cost_micros": int(record.estimated_cost_micros or 0),
        "actual_usage": {key: value for key, value in _load_json(record.usage_json, {}).items() if key != "call_ids"} or None,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "updated_at": record.updated_at,
    }


from ._agent_runtime_methods1 import _AgentRuntimeMixinMethods1
from ._agent_runtime_methods2 import _AgentRuntimeMixinMethods2
from ._agent_runtime_methods3 import _AgentRuntimeMixinMethods3
from ._agent_runtime_methods4 import _AgentRuntimeMixinMethods4
class AgentRuntimeMixin(_AgentRuntimeMixinMethods1, _AgentRuntimeMixinMethods2, _AgentRuntimeMixinMethods3, _AgentRuntimeMixinMethods4):
        """Persistence contract used by the multi-worker Agent runtime."""


def _bind_mixin_member(_member):
    import functools
    import types

    if isinstance(_member, staticmethod):
        return staticmethod(_bind_mixin_member(_member.__func__))
    if isinstance(_member, classmethod):
        return classmethod(_bind_mixin_member(_member.__func__))
    if not isinstance(_member, types.FunctionType):
        return _member
    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _mixin in (_AgentRuntimeMixinMethods1, _AgentRuntimeMixinMethods2, _AgentRuntimeMixinMethods3, _AgentRuntimeMixinMethods4):
    for _name, _member in _mixin.__dict__.items():
        if _name not in {"__dict__", "__weakref__"}:
            setattr(AgentRuntimeMixin, _name, _bind_mixin_member(_member))


__all__ = ["AgentRuntimeMixin"]
