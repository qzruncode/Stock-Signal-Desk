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

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from src.storage.models import (
    AgentArtifact,
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


_ACTIVE_RUN_STATUSES = ("queued", "running", "recovering")
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
        "created_at": record.created_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "updated_at": record.updated_at,
    }


class AgentRuntimeMixin:
    """Persistence contract used by the multi-worker Agent runtime."""

    def claim_agent_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        request_payload: Mapping[str, Any],
        worker_id: str,
        tenant_id: str = "local",
        owner_id: str = "admin",
        lease_seconds: float = 45.0,
        max_active_runs: int | None = None,
        max_owner_active_runs: int | None = None,
    ) -> dict[str, Any]:
        """Atomically claim the active slot for a conversation.

        Returns ``{"claimed": False, "reason": ...}`` for a normal conflict.
        The unique ``active_slot`` column is the cross-process race arbiter.
        """
        now = datetime.now()
        lease_expires_at = now + timedelta(seconds=max(5.0, lease_seconds))

        def _claim(session):
            admission = select(AgentRuntimeControl).where(
                AgentRuntimeControl.name == "run_admission"
            )
            if not self._is_sqlite_engine:
                admission = admission.with_for_update()
            control = session.execute(admission).scalars().first()
            if control is None:
                # The versioned migration seeds this row. Keeping a defensive
                # insert makes local databases created by older test fixtures
                # self-healing; a concurrent insert is retried by the caller.
                control = AgentRuntimeControl(name="run_admission")
                session.add(control)
                session.flush()
            control.updated_at = now

            # A second request for the same conversation is a resumable
            # conflict, not a capacity failure. Resolve this first so callers
            # receive the stable 409 path even when the shared pool is full.
            existing = session.execute(
                select(AgentRun).where(AgentRun.active_slot == conversation_id)
            ).scalars().first()
            if existing is not None:
                return {
                    "claimed": False,
                    "reason": "active",
                    "run": _run_dict(existing),
                }

            if max_active_runs is not None:
                active_count = session.execute(
                    select(func.count(AgentRun.id)).where(
                        AgentRun.active_slot.is_not(None)
                    )
                ).scalar() or 0
                if active_count >= max_active_runs:
                    return {
                        "claimed": False,
                        "reason": "capacity",
                        "active_count": int(active_count),
                    }
            if max_owner_active_runs is not None:
                owner_active_count = session.execute(
                    select(func.count(AgentRun.id)).where(
                        AgentRun.active_slot.is_not(None),
                        AgentRun.tenant_id == tenant_id,
                        AgentRun.owner_id == owner_id,
                    )
                ).scalar() or 0
                if owner_active_count >= max_owner_active_runs:
                    return {
                        "claimed": False,
                        "reason": "owner_capacity",
                        "active_count": int(owner_active_count),
                    }

            record = AgentRun(
                id=run_id,
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
                active_slot=conversation_id,
                status="running",
                request_json=_json(dict(request_payload)),
                worker_id=worker_id,
                lease_expires_at=lease_expires_at,
                heartbeat_at=now,
                started_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            return {"claimed": True, "run": _run_dict(record)}

        try:
            return self._run_write_transaction("claim_agent_run", _claim)
        except IntegrityError:
            # A second worker may have passed the pre-check.  The unique slot
            # is authoritative; report a normal conflict to the API.
            return {"claimed": False, "reason": "active"}

    def get_agent_run(
        self,
        *,
        run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not run_id and not conversation_id:
            raise ValueError("run_id or conversation_id is required")
        with self.get_session() as session:
            statement = select(AgentRun)
            if run_id:
                statement = statement.where(AgentRun.id == run_id)
            else:
                statement = statement.where(
                    AgentRun.conversation_id == conversation_id
                ).order_by(AgentRun.created_at.desc())
            record = session.execute(statement).scalars().first()
            return _run_dict(record) if record is not None else None

    def heartbeat_agent_run(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: float = 45.0,
    ) -> dict[str, Any] | None:
        now = datetime.now()

        def _heartbeat(session):
            statement = select(AgentRun).where(AgentRun.id == run_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None or record.status not in _ACTIVE_RUN_STATUSES:
                return None
            # Only the owning worker may extend a live lease.  A recovered run
            # explicitly changes owner through ``reclaim_agent_run``.
            if record.worker_id and record.worker_id != worker_id:
                return _run_dict(record)
            record.worker_id = worker_id
            record.heartbeat_at = now
            record.lease_expires_at = now + timedelta(
                seconds=max(5.0, lease_seconds)
            )
            record.updated_at = now
            session.flush()
            return _run_dict(record)

        return self._run_write_transaction("heartbeat_agent_run", _heartbeat)

    def request_agent_run_cancel(self, conversation_id: str) -> bool:
        now = datetime.now()

        def _cancel(session):
            statement = select(AgentRun).where(
                AgentRun.active_slot == conversation_id
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return False
            record.cancel_requested = True
            record.updated_at = now
            return True

        return bool(self._run_write_transaction("request_agent_run_cancel", _cancel))

    def finish_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        final_text: str | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        result: Any = None,
    ) -> bool:
        if status not in _TERMINAL_RUN_STATUSES:
            raise ValueError(f"invalid terminal Agent run status: {status}")
        now = datetime.now()

        def _finish(session):
            statement = select(AgentRun).where(AgentRun.id == run_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return False
            # A terminal record is immutable except that a more complete retry
            # may fill previously absent terminal fields.
            if record.status in _TERMINAL_RUN_STATUSES:
                if final_text and not record.final_text:
                    record.final_text = final_text
                if result is not None and not record.result_json:
                    record.result_json = _json(result)
                return True
            record.status = status
            record.active_slot = None
            record.final_text = final_text
            record.error_code = error_code
            record.error_detail = error_detail
            record.result_json = _json(result) if result is not None else None
            record.cancel_requested = status == "cancelled"
            record.lease_expires_at = None
            record.finished_at = now
            record.updated_at = now
            return True

        return bool(self._run_write_transaction("finish_agent_run", _finish))

    def commit_agent_run_terminal(
        self,
        *,
        run_id: str,
        conversation_id: str,
        status: str,
        messages: Sequence[Mapping[str, Any]],
        final_text: str | None,
        agent_context: Mapping[str, Any] | None,
        artifacts: Sequence[Any] = (),
        trace: Mapping[str, Any] | None = None,
        generated_title: str | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        """Atomically publish transcript, context, artifacts, trace and run state."""
        if status not in _TERMINAL_RUN_STATUSES:
            raise ValueError(f"invalid terminal Agent run status: {status}")
        now = datetime.now()
        normalized_messages = [
            dict(message)
            for message in messages
            if isinstance(message, Mapping)
        ]
        trace_payload = dict(trace or {})

        def _commit(session):
            run_statement = select(AgentRun).where(AgentRun.id == run_id)
            conversation_statement = select(ChatConversation).where(
                ChatConversation.id == conversation_id
            )
            if not self._is_sqlite_engine:
                run_statement = run_statement.with_for_update()
                conversation_statement = (
                    conversation_statement.with_for_update()
                )
            run = session.execute(run_statement).scalars().first()
            conversation = session.execute(
                conversation_statement
            ).scalars().first()
            if run is None or conversation is None:
                return False
            if run.status in _TERMINAL_RUN_STATUSES:
                return run.status == status
            if (
                run.status not in _ACTIVE_RUN_STATUSES
                or (
                    worker_id is not None
                    and run.worker_id != worker_id
                )
                or (
                    attempt is not None
                    and int(run.attempt or 0) != int(attempt)
                )
            ):
                return False

            session.execute(
                delete(ChatMessage).where(
                    ChatMessage.conversation_id == conversation_id
                )
            )
            latest_preview = ""
            for sequence, message in enumerate(normalized_messages):
                content = message.get("content")
                content_text = content if isinstance(content, str) else str(content or "")
                latest_preview = content_text or latest_preview
                created_at = message.get("created_at")
                if not isinstance(created_at, datetime):
                    try:
                        created_at = datetime.fromisoformat(
                            str(created_at).replace("Z", "+00:00")
                        )
                    except (TypeError, ValueError):
                        created_at = now
                session.add(ChatMessage(
                    id=str(
                        message.get("id")
                        or f"{conversation_id}-{sequence}"
                    )[:64],
                    conversation_id=conversation_id,
                    role=str(message.get("role") or "user")[:16],
                    content=content_text,
                    sequence=sequence,
                    created_at=created_at,
                ))
            conversation.preview_text = latest_preview[:200] if latest_preview else None
            if agent_context is not None:
                conversation.agent_context_json = _json(dict(agent_context))
            if (
                generated_title
                and conversation.title_source != "manual"
            ):
                conversation.title = str(generated_title).strip()[:120] or "新对话"
                conversation.title_source = "auto"
            conversation.updated_at = now

            for artifact in artifacts:
                artifact_id = str(getattr(artifact, "artifact_id", "") or "")
                artifact_fingerprint = str(
                    getattr(artifact, "fingerprint", "") or ""
                )
                duplicate_fingerprint = (
                    session.execute(
                        select(AgentArtifact.id).where(
                            AgentArtifact.conversation_id == conversation_id,
                            AgentArtifact.fingerprint == artifact_fingerprint,
                        )
                    ).scalars().first()
                    if artifact_fingerprint
                    else None
                )
                if (
                    not artifact_id
                    or session.get(AgentArtifact, artifact_id) is not None
                    or duplicate_fingerprint is not None
                ):
                    continue
                coverage = getattr(artifact, "coverage", None)
                sources = getattr(artifact, "sources", ())
                session.add(AgentArtifact(
                    id=artifact_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    schema_version=str(getattr(artifact, "schema_version", "")),
                    producer_node_id=str(getattr(artifact, "producer_node_id", "")),
                    resource_type=str(
                        getattr(
                            getattr(artifact, "resource_type", None),
                            "value",
                            getattr(artifact, "resource_type", ""),
                        )
                    ),
                    coverage_json=(
                        coverage.model_dump_json()
                        if hasattr(coverage, "model_dump_json")
                        else _json(coverage)
                    ),
                    sources_json=_json([
                        source.model_dump(mode="json")
                        if hasattr(source, "model_dump")
                        else source
                        for source in sources
                    ]),
                    fingerprint=artifact_fingerprint,
                    lineage_json=_json(list(getattr(artifact, "lineage", ()))),
                    payload_json=_json(getattr(artifact, "payload", {})),
                    produced_at=getattr(artifact, "produced_at", now),
                    created_at=now,
                ))

            trace_record = session.execute(
                select(AgentRunTrace).where(
                    AgentRunTrace.run_id == run_id,
                    AgentRunTrace.orchestrator_mode == "unified",
                )
            ).scalars().first()
            if trace_record is None:
                trace_record = AgentRunTrace(
                    id=f"{run_id}:unified"[:64],
                    run_id=run_id,
                    conversation_id=conversation_id,
                    orchestrator_mode="unified",
                    status=str(trace_payload.get("status") or status),
                    created_at=now,
                )
                session.add(trace_record)
            trace_record.status = str(trace_payload.get("status") or status)
            trace_record.error_code = (
                str(trace_payload.get("error_code") or error_code or "") or None
            )
            trace_field_map = {
                "stage_durations": "stage_durations_json",
                "outcomes": "outcomes_json",
                "coverage": "coverage_json",
                "latest_stage": "latest_stage_json",
                "compiled_plan": "compiled_plan_json",
            }
            for source_key, target_field in trace_field_map.items():
                if source_key in trace_payload:
                    if source_key in {"outcomes", "compiled_plan"}:
                        from src.storage.mixins.agent_run_trace import (
                            encode_agent_trace_json,
                        )
                        encoded_value = encode_agent_trace_json(
                            trace_payload[source_key],
                            encrypt=True,
                        )
                    else:
                        encoded_value = _json(trace_payload[source_key])
                    setattr(
                        trace_record,
                        target_field,
                        encoded_value,
                    )
            trace_record.updated_at = now

            run.status = status
            run.active_slot = None
            run.final_text = final_text
            run.error_code = error_code
            run.error_detail = error_detail
            run.result_json = _json({
                "message_count": len(normalized_messages),
                "artifact_count": len(artifacts),
                "trace_status": trace_record.status,
            })
            run.cancel_requested = status == "cancelled"
            run.lease_expires_at = None
            run.finished_at = now
            run.updated_at = now
            return True

        return bool(self._run_write_transaction("commit_agent_run_terminal", _commit))

    def append_agent_run_event(
        self,
        *,
        run_id: str,
        sequence: int,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> bool:
        if sequence < 0:
            raise ValueError("event sequence must be non-negative")
        now = datetime.now()

        def _append(session):
            statement = select(AgentRun).where(AgentRun.id == run_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return False
            event_id = f"{run_id}:{sequence}"
            if session.get(AgentRunEvent, event_id) is not None:
                return True
            expected_sequence = int(record.event_cursor or 0)
            if sequence != expected_sequence:
                raise RuntimeError(
                    "durable Agent event sequence gap: "
                    f"expected {expected_sequence}, received {sequence}"
                )
            session.add(AgentRunEvent(
                id=event_id,
                run_id=run_id,
                sequence=sequence,
                event_type=event_type[:32],
                payload_json=_json(dict(payload)),
                created_at=now,
            ))
            record.event_cursor = max(int(record.event_cursor or 0), sequence + 1)
            record.updated_at = now
            return True

        try:
            return bool(self._run_write_transaction("append_agent_run_event", _append))
        except IntegrityError:
            # Event identity is deterministic, so a duplicate insert is the
            # successful replay of the same emission.
            return True

    def list_agent_run_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 10_000))
        with self.get_session() as session:
            records = session.execute(
                select(AgentRunEvent)
                .where(
                    AgentRunEvent.run_id == run_id,
                    AgentRunEvent.sequence >= max(0, int(after_sequence)),
                )
                .order_by(AgentRunEvent.sequence.asc())
                .limit(safe_limit)
            ).scalars().all()
            return [
                {
                    "sequence": int(record.sequence),
                    "event_type": record.event_type,
                    "payload": _load_json(record.payload_json, {}),
                    "created_at": record.created_at,
                }
                for record in records
            ]

    def agent_run_has_tool_events(self, run_id: str) -> bool:
        with self.get_session() as session:
            count = session.execute(
                select(func.count(AgentRunEvent.id)).where(
                    AgentRunEvent.run_id == run_id,
                    AgentRunEvent.event_type.in_({
                        "tool-call-begin",
                        "tool-call-delta",
                        "tool-result",
                    }),
                )
            ).scalar() or 0
            return bool(count)

    def list_recoverable_agent_runs(
        self,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        cutoff = now or datetime.now()
        with self.get_session() as session:
            records = session.execute(
                select(AgentRun)
                .where(
                    AgentRun.status.in_(_ACTIVE_RUN_STATUSES),
                    AgentRun.lease_expires_at.is_not(None),
                    AgentRun.lease_expires_at < cutoff,
                    AgentRun.cancel_requested.is_(False),
                )
                .order_by(AgentRun.lease_expires_at.asc())
                .limit(max(1, min(limit, 500)))
            ).scalars().all()
            return [_run_dict(record) for record in records]

    def reclaim_agent_run(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: float = 45.0,
    ) -> dict[str, Any] | None:
        now = datetime.now()

        def _reclaim(session):
            statement = select(AgentRun).where(AgentRun.id == run_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if (
                record is None
                or record.status not in _ACTIVE_RUN_STATUSES
                or record.cancel_requested
                or (
                    record.lease_expires_at is not None
                    and record.lease_expires_at >= now
                )
            ):
                return None
            record.status = "recovering"
            record.worker_id = worker_id
            record.heartbeat_at = now
            record.lease_expires_at = now + timedelta(
                seconds=max(5.0, lease_seconds)
            )
            record.attempt = int(record.attempt or 1) + 1
            record.updated_at = now
            session.flush()
            return _run_dict(record)

        return self._run_write_transaction("reclaim_agent_run", _reclaim)

    def release_agent_run_lease(
        self,
        run_id: str,
        *,
        worker_id: str,
    ) -> bool:
        """Release ownership during graceful worker shutdown for fast recovery."""
        now = datetime.now()

        def _release(session):
            statement = select(AgentRun).where(AgentRun.id == run_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if (
                record is None
                or record.status not in _ACTIVE_RUN_STATUSES
                or record.worker_id != worker_id
            ):
                return False
            record.status = "recovering"
            record.heartbeat_at = now
            record.lease_expires_at = now - timedelta(seconds=1)
            record.updated_at = now
            return True

        return bool(self._run_write_transaction("release_agent_run_lease", _release))

    def claim_agent_step(
        self,
        *,
        idempotency_key: str,
        run_id: str,
        conversation_id: str,
        task_id: str,
        step_id: str,
        tool_name: str,
        effect: str,
        arguments: Mapping[str, Any],
        worker_id: str,
        lease_seconds: float,
        max_attempts: int,
    ) -> dict[str, Any]:
        """Claim or recover an idempotent step.

        The caller receives one of ``execute``, ``reuse``, ``wait`` or
        ``exhausted`` and therefore never has to infer safety from a timeout.
        """
        now = datetime.now()

        def _claim(session):
            statement = select(AgentStepExecution).where(
                AgentStepExecution.idempotency_key == idempotency_key
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is not None:
                if (
                    record.run_id != run_id
                    or record.tool_name != tool_name
                    or _load_json(record.arguments_json, {}) != dict(arguments)
                ):
                    raise RuntimeError(
                        "Agent step idempotency key was reused for a "
                        "different execution"
                    )
                if record.status == "completed":
                    record.reuse_count = int(record.reuse_count or 0) + 1
                    record.updated_at = now
                    return {
                        "action": "reuse",
                        "result": _load_json(record.result_json),
                        "attempt": int(record.attempt or 0),
                        "reuse_count": int(record.reuse_count),
                    }
                if record.status == "failed":
                    return {
                        "action": "exhausted",
                        "attempt": int(record.attempt or 0),
                        "error_code": record.error_code,
                        "error_detail": record.error_detail,
                    }
                lease_live = (
                    record.lease_expires_at is not None
                    and record.lease_expires_at >= now
                )
                if record.status in _RUNNING_STEP_STATUSES and lease_live:
                    return {
                        "action": "wait",
                        "attempt": int(record.attempt or 0),
                        "lease_expires_at": record.lease_expires_at,
                    }
                if int(record.attempt or 0) >= int(record.max_attempts or 1):
                    return {
                        "action": "exhausted",
                        "attempt": int(record.attempt or 0),
                        "error_code": record.error_code,
                        "error_detail": record.error_detail,
                    }
            else:
                record = AgentStepExecution(
                    idempotency_key=idempotency_key,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    task_id=task_id,
                    step_id=step_id,
                    tool_name=tool_name,
                    effect=effect,
                    arguments_json=_json(dict(arguments)),
                    max_attempts=max(1, int(max_attempts)),
                    created_at=now,
                )
                session.add(record)

            record.status = "running"
            record.worker_id = worker_id
            record.lease_expires_at = now + timedelta(
                seconds=max(5.0, lease_seconds)
            )
            record.attempt = int(record.attempt or 0) + 1
            record.started_at = record.started_at or now
            record.updated_at = now
            session.flush()
            return {
                "action": "execute",
                "attempt": int(record.attempt),
                "max_attempts": int(record.max_attempts),
            }

        try:
            return self._run_write_transaction("claim_agent_step", _claim)
        except IntegrityError:
            return {"action": "wait", "attempt": 0}

    def finish_agent_step(
        self,
        idempotency_key: str,
        *,
        result: Any,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        now = datetime.now()

        def _finish(session):
            statement = select(AgentStepExecution).where(
                AgentStepExecution.idempotency_key == idempotency_key
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return False
            if (
                (worker_id is not None and record.worker_id != worker_id)
                or (
                    attempt is not None
                    and int(record.attempt or 0) != int(attempt)
                )
                or record.status != "running"
            ):
                return False
            record.status = "completed"
            record.result_json = _json(result)
            record.error_code = None
            record.error_detail = None
            record.lease_expires_at = None
            record.finished_at = now
            record.updated_at = now
            return True

        return bool(self._run_write_transaction("finish_agent_step", _finish))

    def fail_agent_step(
        self,
        idempotency_key: str,
        *,
        error_code: str,
        error_detail: str,
        retryable: bool,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        now = datetime.now()

        def _fail(session):
            statement = select(AgentStepExecution).where(
                AgentStepExecution.idempotency_key == idempotency_key
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return False
            if (
                (worker_id is not None and record.worker_id != worker_id)
                or (
                    attempt is not None
                    and int(record.attempt or 0) != int(attempt)
                )
                or record.status != "running"
            ):
                return False
            exhausted = int(record.attempt or 0) >= int(record.max_attempts or 1)
            record.status = "retry_wait" if retryable and not exhausted else "failed"
            record.error_code = error_code[:64]
            record.error_detail = error_detail
            record.lease_expires_at = None
            if record.status == "failed":
                record.finished_at = now
            record.updated_at = now
            return True

        return bool(self._run_write_transaction("fail_agent_step", _fail))

    def upsert_effect_outbox(
        self,
        *,
        idempotency_key: str,
        run_id: str,
        tool_name: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        now = datetime.now()

        def _upsert(session):
            statement = select(AgentEffectOutbox).where(
                AgentEffectOutbox.idempotency_key == idempotency_key
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                record = AgentEffectOutbox(
                    idempotency_key=idempotency_key,
                    run_id=run_id,
                    tool_name=tool_name,
                    payload_json=_json(dict(payload)),
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                session.flush()
            elif (
                record.run_id != run_id
                or record.tool_name != tool_name
                or _load_json(record.payload_json, {}) != dict(payload)
            ):
                raise RuntimeError(
                    "Agent effect idempotency key was reused for a "
                    "different dispatch"
                )
            return {
                "status": record.status,
                "result": _load_json(record.result_json),
                "provider_reference": record.provider_reference,
                "attempt": int(record.attempt or 0),
            }

        try:
            return self._run_write_transaction("upsert_effect_outbox", _upsert)
        except IntegrityError:
            with self.get_session() as session:
                record = session.get(AgentEffectOutbox, idempotency_key)
                return {
                    "status": record.status if record else "pending",
                    "result": _load_json(record.result_json) if record else None,
                    "provider_reference": record.provider_reference if record else None,
                    "attempt": int(record.attempt or 0) if record else 0,
                }

    def complete_effect_outbox(
        self,
        idempotency_key: str,
        *,
        result: Any,
        provider_reference: str | None = None,
    ) -> bool:
        now = datetime.now()

        def _complete(session):
            statement = select(AgentEffectOutbox).where(
                AgentEffectOutbox.idempotency_key == idempotency_key
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return False
            record.status = "completed"
            record.result_json = _json(result)
            record.provider_reference = provider_reference
            record.attempt = int(record.attempt or 0) + 1
            record.updated_at = now
            return True

        return bool(self._run_write_transaction("complete_effect_outbox", _complete))

    def check_agent_rate_limit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float = 60.0,
    ) -> int:
        """Shared fixed-window admission limit; returns Retry-After seconds."""
        if limit <= 0:
            return 0
        now = datetime.now()
        safe_window = max(1.0, float(window_seconds))
        window_number = int(now.timestamp() // safe_window)
        window_started_at = datetime.fromtimestamp(window_number * safe_window)
        expires_at = window_started_at + timedelta(seconds=safe_window)
        key_hash = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
        bucket_id = f"{key_hash}:{window_number}"

        def _record(session):
            statement = select(AgentRateLimitBucket).where(
                AgentRateLimitBucket.id == bucket_id
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                record = AgentRateLimitBucket(
                    id=bucket_id,
                    key_hash=key_hash,
                    window_started_at=window_started_at,
                    request_count=1,
                    expires_at=expires_at,
                    updated_at=now,
                )
                session.add(record)
                return 0
            if int(record.request_count or 0) >= limit:
                return max(1, int((record.expires_at - now).total_seconds()) + 1)
            record.request_count = int(record.request_count or 0) + 1
            record.updated_at = now
            return 0

        try:
            return int(self._run_write_transaction("check_agent_rate_limit", _record))
        except IntegrityError:
            # Concurrent first request created the bucket.  Re-entering records
            # this request against the now-existing shared row.
            return int(self._run_write_transaction("check_agent_rate_limit_retry", _record))

    def reserve_agent_run_budget(
        self,
        run_id: str,
        *,
        tool_calls: int = 0,
        provider_calls: int = 0,
        estimated_tokens: int = 0,
        estimated_cost_micros: int = 0,
        max_tool_calls: int | None = None,
        max_provider_calls: int | None = None,
        max_estimated_tokens: int | None = None,
        max_estimated_cost_micros: int | None = None,
    ) -> dict[str, Any]:
        """Atomically reserve run-scoped call/token/cost capacity."""
        increments = {
            "tool_call_count": max(0, int(tool_calls)),
            "provider_call_count": max(0, int(provider_calls)),
            "estimated_token_count": max(0, int(estimated_tokens)),
            "estimated_cost_micros": max(0, int(estimated_cost_micros)),
        }
        limits = {
            "tool_call_count": max_tool_calls,
            "provider_call_count": max_provider_calls,
            "estimated_token_count": max_estimated_tokens,
            "estimated_cost_micros": max_estimated_cost_micros,
        }

        def _reserve(session):
            statement = select(AgentRun).where(AgentRun.id == run_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return {"allowed": False, "reason": "run_not_found"}
            next_values: dict[str, int] = {}
            for field_name, increment in increments.items():
                next_value = int(getattr(record, field_name) or 0) + increment
                limit = limits[field_name]
                if limit is not None and next_value > int(limit):
                    return {
                        "allowed": False,
                        "reason": field_name,
                        "current": int(getattr(record, field_name) or 0),
                        "requested": increment,
                        "limit": int(limit),
                    }
                next_values[field_name] = next_value
            for field_name, value in next_values.items():
                setattr(record, field_name, value)
            record.updated_at = datetime.now()
            return {"allowed": True, **next_values}

        return self._run_write_transaction("reserve_agent_run_budget", _reserve)

    def try_acquire_agent_resource(
        self,
        *,
        resource_name: str,
        lease_owner: str,
        slots: int,
        lease_seconds: float,
        run_id: str | None = None,
        step_id: str | None = None,
    ) -> str | None:
        """Try once to claim one globally shared resource slot."""
        normalized_resource = str(resource_name).strip()[:160]
        if not normalized_resource:
            raise ValueError("resource_name is required")
        safe_slots = max(1, min(int(slots), 256))
        now = datetime.now()
        expires = now + timedelta(seconds=max(5.0, float(lease_seconds)))

        def _acquire(session):
            for slot_index in range(safe_slots):
                lease_id = f"{normalized_resource}:{slot_index}"
                statement = select(AgentResourceLease).where(
                    AgentResourceLease.id == lease_id
                )
                if not self._is_sqlite_engine:
                    statement = statement.with_for_update()
                record = session.execute(statement).scalars().first()
                if record is None:
                    record = AgentResourceLease(
                        id=lease_id,
                        resource_name=normalized_resource,
                        slot_index=slot_index,
                        lease_owner=lease_owner,
                        run_id=run_id,
                        step_id=step_id,
                        lease_expires_at=expires,
                        updated_at=now,
                    )
                    session.add(record)
                    session.flush()
                    return lease_id
                if (
                    record.lease_owner == lease_owner
                    or record.lease_expires_at is None
                    or record.lease_expires_at < now
                ):
                    record.lease_owner = lease_owner
                    record.run_id = run_id
                    record.step_id = step_id
                    record.lease_expires_at = expires
                    record.updated_at = now
                    return lease_id
            return None

        try:
            return self._run_write_transaction("try_acquire_agent_resource", _acquire)
        except IntegrityError:
            return None

    def release_agent_resource(
        self,
        lease_id: str,
        *,
        lease_owner: str,
    ) -> bool:
        now = datetime.now()

        def _release(session):
            statement = select(AgentResourceLease).where(
                AgentResourceLease.id == lease_id
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None or record.lease_owner != lease_owner:
                return False
            record.lease_owner = None
            record.run_id = None
            record.step_id = None
            record.lease_expires_at = None
            record.updated_at = now
            return True

        return bool(self._run_write_transaction("release_agent_resource", _release))

    def agent_circuit_before_request(
        self,
        resource_name: str,
        *,
        worker_id: str,
    ) -> dict[str, Any]:
        now = datetime.now()
        normalized_resource = str(resource_name).strip()[:160]

        def _before(session):
            statement = select(AgentCircuitBreaker).where(
                AgentCircuitBreaker.resource_name == normalized_resource
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None or record.state == "closed":
                return {"allowed": True, "state": "closed"}
            if record.state == "open":
                if record.opened_until is not None and record.opened_until > now:
                    return {
                        "allowed": False,
                        "state": "open",
                        "retry_after_seconds": max(
                            1,
                            int((record.opened_until - now).total_seconds()) + 1,
                        ),
                    }
                record.state = "half_open"
                record.probe_owner = worker_id
                record.updated_at = now
                return {"allowed": True, "state": "half_open"}
            if record.state == "half_open" and record.probe_owner != worker_id:
                return {"allowed": False, "state": "half_open"}
            return {"allowed": True, "state": record.state}

        return self._run_write_transaction("agent_circuit_before_request", _before)

    def record_agent_circuit_success(self, resource_name: str) -> None:
        now = datetime.now()
        normalized_resource = str(resource_name).strip()[:160]

        def _success(session):
            statement = select(AgentCircuitBreaker).where(
                AgentCircuitBreaker.resource_name == normalized_resource
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return
            record.state = "closed"
            record.failure_count = 0
            record.opened_until = None
            record.probe_owner = None
            record.last_error = None
            record.updated_at = now

        self._run_write_transaction("record_agent_circuit_success", _success)

    def record_agent_circuit_failure(
        self,
        resource_name: str,
        *,
        error: str,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
    ) -> dict[str, Any]:
        now = datetime.now()
        normalized_resource = str(resource_name).strip()[:160]
        threshold = max(1, int(failure_threshold))

        def _failure(session):
            statement = select(AgentCircuitBreaker).where(
                AgentCircuitBreaker.resource_name == normalized_resource
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                record = AgentCircuitBreaker(
                    resource_name=normalized_resource,
                    state="closed",
                    failure_count=0,
                    updated_at=now,
                )
                session.add(record)
            record.failure_count = int(record.failure_count or 0) + 1
            if record.state == "half_open" or record.failure_count >= threshold:
                record.state = "open"
                record.opened_until = now + timedelta(
                    seconds=max(1.0, float(cooldown_seconds))
                )
                record.probe_owner = None
            record.last_error = str(error)[:2000]
            record.updated_at = now
            return {
                "state": record.state,
                "failure_count": int(record.failure_count),
                "opened_until": record.opened_until,
            }

        try:
            return self._run_write_transaction(
                "record_agent_circuit_failure",
                _failure,
            )
        except IntegrityError:
            return self._run_write_transaction(
                "record_agent_circuit_failure_retry",
                _failure,
            )

    def agent_runtime_metrics(self) -> dict[str, Any]:
        now = datetime.now()
        with self.get_session() as session:
            status_rows = session.execute(
                select(AgentRun.status, func.count(AgentRun.id)).group_by(
                    AgentRun.status
                )
            ).all()
            step_rows = session.execute(
                select(
                    AgentStepExecution.tool_name,
                    AgentStepExecution.status,
                    func.count(AgentStepExecution.idempotency_key),
                ).group_by(
                    AgentStepExecution.tool_name,
                    AgentStepExecution.status,
                )
            ).all()
            expired = session.execute(
                select(func.count(AgentRun.id)).where(
                    AgentRun.active_slot.is_not(None),
                    AgentRun.lease_expires_at < now,
                )
            ).scalar() or 0
            recent_runs = session.execute(
                select(AgentRun).where(
                    AgentRun.created_at >= now - timedelta(hours=24)
                ).order_by(AgentRun.created_at.desc()).limit(10_000)
            ).scalars().all()
            terminal_recent = [
                record
                for record in recent_runs
                if record.status in _TERMINAL_RUN_STATUSES
            ]
            durations_ms = sorted(
                int(
                    (record.finished_at - record.started_at).total_seconds()
                    * 1000
                )
                for record in terminal_recent
                if record.started_at is not None
                and record.finished_at is not None
            )

            def percentile(values: list[int], ratio: float) -> int | None:
                if not values:
                    return None
                index = min(
                    len(values) - 1,
                    max(0, int(round((len(values) - 1) * ratio))),
                )
                return values[index]

            successes = sum(
                record.status == "completed"
                for record in terminal_recent
            )
            open_circuits = session.execute(
                select(func.count(AgentCircuitBreaker.resource_name)).where(
                    AgentCircuitBreaker.state != "closed"
                )
            ).scalar() or 0
            active_resource_leases = session.execute(
                select(func.count(AgentResourceLease.id)).where(
                    AgentResourceLease.lease_owner.is_not(None),
                    AgentResourceLease.lease_expires_at >= now,
                )
            ).scalar() or 0
            step_reuses = session.execute(
                select(func.sum(AgentStepExecution.reuse_count))
            ).scalar() or 0
            recovery_attempts = sum(
                max(0, int(record.attempt or 1) - 1)
                for record in recent_runs
            )
            return {
                "runs": {status: int(count) for status, count in status_rows},
                "steps": [
                    {
                        "tool_name": tool_name,
                        "status": status,
                        "count": int(count),
                    }
                    for tool_name, status, count in step_rows
                ],
                "expired_run_leases": int(expired),
                "active_resource_leases": int(active_resource_leases),
                "open_circuits": int(open_circuits),
                "step_idempotency_reuses": int(step_reuses),
                "recovery_attempts_24h": int(recovery_attempts),
                "slo_24h": {
                    "terminal_runs": len(terminal_recent),
                    "successful_runs": successes,
                    "success_rate": (
                        round(successes / len(terminal_recent), 6)
                        if terminal_recent
                        else None
                    ),
                    "duration_ms_p50": percentile(durations_ms, 0.50),
                    "duration_ms_p95": percentile(durations_ms, 0.95),
                },
            }

    def prune_agent_runtime_data(
        self,
        *,
        finished_before: datetime,
        trace_before: datetime | None = None,
        limit: int = 500,
    ) -> dict[str, int]:
        """Delete bounded terminal runtime history; cascades events and steps."""
        with self.session_scope() as session:
            run_ids = session.execute(
                select(AgentRun.id)
                .where(
                    AgentRun.status.in_(_TERMINAL_RUN_STATUSES),
                    AgentRun.finished_at < finished_before,
                )
                .order_by(AgentRun.finished_at.asc())
                .limit(max(1, min(limit, 5000)))
            ).scalars().all()
            trace_cutoff = trace_before or finished_before
            safe_limit = max(1, min(limit, 5000))
            trace_ids = session.execute(
                select(AgentRunTrace.id)
                .where(AgentRunTrace.created_at < trace_cutoff)
                .order_by(AgentRunTrace.created_at.asc())
                .limit(safe_limit)
            ).scalars().all()
            traces = (
                session.execute(
                    delete(AgentRunTrace).where(
                        AgentRunTrace.id.in_(trace_ids)
                    )
                ).rowcount or 0
                if trace_ids
                else 0
            )
            # Artifacts are semantic conversation state and may be referenced
            # by later turns. They follow conversation deletion, not trace TTL.
            artifacts = 0
            bucket_ids = session.execute(
                select(AgentRateLimitBucket.id)
                .where(AgentRateLimitBucket.expires_at < datetime.now())
                .order_by(AgentRateLimitBucket.expires_at.asc())
                .limit(safe_limit)
            ).scalars().all()
            rate_buckets = (
                session.execute(
                    delete(AgentRateLimitBucket).where(
                        AgentRateLimitBucket.id.in_(bucket_ids)
                    )
                ).rowcount or 0
                if bucket_ids
                else 0
            )
            if not run_ids:
                return {
                    "runs": 0,
                    "traces": int(traces),
                    "artifacts": int(artifacts),
                    "rate_limit_buckets": int(rate_buckets),
                }
            # Explicit deletes keep behavior consistent when a legacy SQLite
            # connection was created before foreign_keys=ON was introduced.
            session.execute(delete(AgentRunEvent).where(AgentRunEvent.run_id.in_(run_ids)))
            session.execute(delete(AgentStepExecution).where(AgentStepExecution.run_id.in_(run_ids)))
            session.execute(delete(AgentEffectOutbox).where(AgentEffectOutbox.run_id.in_(run_ids)))
            result = session.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
            return {
                "runs": int(result.rowcount or 0),
                "traces": int(traces),
                "artifacts": int(artifacts),
                "rate_limit_buckets": int(rate_buckets),
            }


__all__ = ["AgentRuntimeMixin"]
