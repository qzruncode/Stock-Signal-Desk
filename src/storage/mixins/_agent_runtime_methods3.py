"""AgentRuntimeMixin method group 3."""

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

class _AgentRuntimeMixinMethods3:
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
            statement = select(AgentEffectOutbox).where(AgentEffectOutbox.idempotency_key == idempotency_key)
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
                raise RuntimeError("Agent effect idempotency key was reused for a " "different dispatch")
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
            statement = select(AgentEffectOutbox).where(AgentEffectOutbox.idempotency_key == idempotency_key)
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
            statement = select(AgentRateLimitBucket).where(AgentRateLimitBucket.id == bucket_id)
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
                statement = select(AgentResourceLease).where(AgentResourceLease.id == lease_id)
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
            statement = select(AgentResourceLease).where(AgentResourceLease.id == lease_id)
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
            statement = select(AgentCircuitBreaker).where(AgentCircuitBreaker.resource_name == normalized_resource)
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
            statement = select(AgentCircuitBreaker).where(AgentCircuitBreaker.resource_name == normalized_resource)
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
            statement = select(AgentCircuitBreaker).where(AgentCircuitBreaker.resource_name == normalized_resource)
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
                record.opened_until = now + timedelta(seconds=max(1.0, float(cooldown_seconds)))
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
