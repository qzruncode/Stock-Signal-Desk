"""AgentRuntimeMixin method group 2."""

from __future__ import annotations

from src.storage.mixins.agent_runtime import (
    datetime,
    timedelta,
    hashlib,
    json,
    Any,
    Mapping,
    Sequence,
    delete,
    func,
    select,
    IntegrityError,
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
    _ACTIVE_RUN_STATUSES,
    _TERMINAL_RUN_STATUSES,
    _RUNNING_STEP_STATUSES,
    _json,
    _load_json,
    _run_dict,
 )

class _AgentRuntimeMixinMethods2:
    def append_agent_run_event(
        self,
        *,
        run_id: str,
        sequence: int,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> bool:
        return self.append_agent_run_events(
            run_id=run_id,
            start_sequence=sequence,
            events=[
                {
                    "event_type": event_type,
                    "payload": dict(payload),
                }
            ],
        )
    def append_agent_run_events(
        self,
        *,
        run_id: str,
        start_sequence: int,
        events: Sequence[Mapping[str, Any]],
    ) -> bool:
        """Atomically append one ordered event batch and advance its cursor."""
        if start_sequence < 0:
            raise ValueError("event sequence must be non-negative")
        normalized_events = [
            {
                "event_type": str(event.get("event_type") or "")[:32],
                "payload": dict(event.get("payload") or {}),
            }
            for event in events
        ]
        if not normalized_events:
            return True
        now = datetime.now()

        def _append(session):
            statement = select(AgentRun).where(AgentRun.id == run_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return False
            expected_sequence = int(record.event_cursor or 0)
            if start_sequence < expected_sequence:
                end_sequence = start_sequence + len(normalized_events)
                if end_sequence <= expected_sequence:
                    return True
            if start_sequence != expected_sequence:
                raise RuntimeError(
                    "durable Agent event sequence gap: " f"expected {expected_sequence}, received {start_sequence}"
                )
            for offset, event in enumerate(normalized_events):
                sequence = start_sequence + offset
                session.add(
                    AgentRunEvent(
                        id=f"{run_id}:{sequence}",
                        run_id=run_id,
                        sequence=sequence,
                        event_type=event["event_type"],
                        payload_json=_json(event["payload"]),
                        created_at=now,
                    )
                )
            record.event_cursor = start_sequence + len(normalized_events)
            record.updated_at = now
            return True

        try:
            return bool(self._run_write_transaction("append_agent_run_events", _append))
        except IntegrityError:
            # Event identities are deterministic. Recheck whether the whole
            # batch was already committed before treating the conflict as a
            # successful idempotent replay.
            latest = self.get_agent_run(run_id=run_id)
            return bool(latest and int(latest.get("event_cursor") or 0) >= start_sequence + len(normalized_events))
    def list_agent_run_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 10_000))
        with self.get_session() as session:
            records = (
                session.execute(
                    select(AgentRunEvent)
                    .where(
                        AgentRunEvent.run_id == run_id,
                        AgentRunEvent.sequence >= max(0, int(after_sequence)),
                    )
                    .order_by(AgentRunEvent.sequence.asc())
                    .limit(safe_limit)
                )
                .scalars()
                .all()
            )
            return [
                {
                    "sequence": int(record.sequence),
                    "event_type": record.event_type,
                    "payload": _load_json(record.payload_json, {}),
                    "created_at": record.created_at,
                }
                for record in records
            ]
    def read_agent_run_event_batch(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> dict[str, Any] | None:
        """Read run state and its next ordered event page in one DB session."""
        safe_limit = max(1, min(int(limit), 10_000))
        cursor = max(0, int(after_sequence))
        with self.get_session() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return None
            records = (
                session.execute(
                    select(AgentRunEvent)
                    .where(
                        AgentRunEvent.run_id == run_id,
                        AgentRunEvent.sequence >= cursor,
                    )
                    .order_by(AgentRunEvent.sequence.asc())
                    .limit(safe_limit)
                )
                .scalars()
                .all()
            )
            return {
                "run": _run_dict(run),
                "events": [
                    {
                        "sequence": int(record.sequence),
                        "event_type": record.event_type,
                        "payload": _load_json(record.payload_json, {}),
                        "created_at": record.created_at,
                    }
                    for record in records
                ],
            }
    def agent_run_has_tool_events(self, run_id: str) -> bool:
        with self.get_session() as session:
            count = (
                session.execute(
                    select(func.count(AgentRunEvent.id)).where(
                        AgentRunEvent.run_id == run_id,
                        AgentRunEvent.event_type.in_(
                            {
                                "tool-call-begin",
                                "tool-call-delta",
                                "tool-result",
                            }
                        ),
                    )
                ).scalar()
                or 0
            )
            return bool(count)
    def list_recoverable_agent_runs(
        self,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        cutoff = now or datetime.now()
        with self.get_session() as session:
            records = (
                session.execute(
                    select(AgentRun)
                    .where(
                        AgentRun.status.in_(_ACTIVE_RUN_STATUSES),
                        AgentRun.lease_expires_at.is_not(None),
                        AgentRun.lease_expires_at < cutoff,
                        AgentRun.cancel_requested.is_(False),
                    )
                    .order_by(AgentRun.lease_expires_at.asc())
                    .limit(max(1, min(limit, 500)))
                )
                .scalars()
                .all()
            )
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
                or (record.lease_expires_at is not None and record.lease_expires_at >= now)
            ):
                return None
            record.status = "recovering"
            record.worker_id = worker_id
            record.heartbeat_at = now
            record.lease_expires_at = now + timedelta(seconds=max(5.0, lease_seconds))
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
            if record is None or record.status not in _ACTIVE_RUN_STATUSES or record.worker_id != worker_id:
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
            statement = select(AgentStepExecution).where(AgentStepExecution.idempotency_key == idempotency_key)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is not None:
                if (
                    record.run_id != run_id
                    or record.tool_name != tool_name
                    or _load_json(record.arguments_json, {}) != dict(arguments)
                ):
                    raise RuntimeError("Agent step idempotency key was reused for a " "different execution")
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
                lease_live = record.lease_expires_at is not None and record.lease_expires_at >= now
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
            record.lease_expires_at = now + timedelta(seconds=max(5.0, lease_seconds))
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

    def renew_agent_step_lease(
        self,
        idempotency_key: str,
        *,
        worker_id: str,
        attempt: int,
        lease_seconds: float,
    ) -> bool:
        """Keep a legitimately running step owned without limiting its duration."""
        now = datetime.now()

        def _renew(session):
            statement = select(AgentStepExecution).where(
                AgentStepExecution.idempotency_key == idempotency_key
            )
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if (
                record is None
                or record.status not in _RUNNING_STEP_STATUSES
                or record.worker_id != worker_id
                or int(record.attempt or 0) != int(attempt)
            ):
                return False
            record.lease_expires_at = now + timedelta(
                seconds=max(5.0, float(lease_seconds))
            )
            record.updated_at = now
            return True

        return bool(
            self._run_write_transaction(
                "renew_agent_step_lease",
                _renew,
            )
        )

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
            statement = select(AgentStepExecution).where(AgentStepExecution.idempotency_key == idempotency_key)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return False
            if (
                (worker_id is not None and record.worker_id != worker_id)
                or (attempt is not None and int(record.attempt or 0) != int(attempt))
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
            statement = select(AgentStepExecution).where(AgentStepExecution.idempotency_key == idempotency_key)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return False
            if (
                (worker_id is not None and record.worker_id != worker_id)
                or (attempt is not None and int(record.attempt or 0) != int(attempt))
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
