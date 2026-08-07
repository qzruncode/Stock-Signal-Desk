"""AgentRuntimeMixin method group 1."""

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

class _AgentRuntimeMixinMethods1:
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
            admission = select(AgentRuntimeControl).where(AgentRuntimeControl.name == "run_admission")
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
            existing = (
                session.execute(select(AgentRun).where(AgentRun.active_slot == conversation_id)).scalars().first()
            )
            if existing is not None:
                return {
                    "claimed": False,
                    "reason": "active",
                    "run": _run_dict(existing),
                }

            if max_active_runs is not None:
                active_count = (
                    session.execute(select(func.count(AgentRun.id)).where(AgentRun.active_slot.is_not(None))).scalar()
                    or 0
                )
                if active_count >= max_active_runs:
                    return {
                        "claimed": False,
                        "reason": "capacity",
                        "active_count": int(active_count),
                    }
            if max_owner_active_runs is not None:
                owner_active_count = (
                    session.execute(
                        select(func.count(AgentRun.id)).where(
                            AgentRun.active_slot.is_not(None),
                            AgentRun.tenant_id == tenant_id,
                            AgentRun.owner_id == owner_id,
                        )
                    ).scalar()
                    or 0
                )
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
                statement = statement.where(AgentRun.conversation_id == conversation_id).order_by(
                    AgentRun.created_at.desc()
                )
            record = session.execute(statement).scalars().first()
            return _run_dict(record) if record is not None else None
    def save_agent_run_checkpoint(
        self,
        run_id: str,
        *,
        worker_id: str,
        attempt: int,
        checkpoint: Mapping[str, Any],
    ) -> bool:
        """Persist a recoverable stage boundary for the current run owner."""
        now = datetime.now()

        def _save(session):
            statement = select(AgentRun).where(AgentRun.id == run_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if (
                record is None
                or record.status not in _ACTIVE_RUN_STATUSES
                or record.worker_id != worker_id
                or int(record.attempt or 0) != int(attempt)
            ):
                return False
            record.context_snapshot_json = _json(dict(checkpoint))
            record.updated_at = now
            return True

        return bool(self._run_write_transaction("save_agent_run_checkpoint", _save))
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
            record.lease_expires_at = now + timedelta(seconds=max(5.0, lease_seconds))
            record.updated_at = now
            session.flush()
            return _run_dict(record)

        return self._run_write_transaction("heartbeat_agent_run", _heartbeat)
    def request_agent_run_cancel(self, conversation_id: str) -> bool:
        now = datetime.now()

        def _cancel(session):
            statement = select(AgentRun).where(AgentRun.active_slot == conversation_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return False
            record.cancel_requested = True
            if record.status == "interrupted":
                record.status = "cancelled"
                record.active_slot = None
                record.error_code = "cancelled"
                record.error_detail = "cancelled while awaiting approval"
                record.context_snapshot_json = None
                record.lease_expires_at = None
                record.finished_at = now
            record.updated_at = now
            return True

        return bool(self._run_write_transaction("request_agent_run_cancel", _cancel))

    def interrupt_agent_run(
        self,
        run_id: str,
        *,
        worker_id: str,
        attempt: int,
        checkpoint: Mapping[str, Any],
    ) -> bool:
        """Park a run at a LangGraph interrupt while retaining its active slot."""
        now = datetime.now()

        def _interrupt(session):
            statement = select(AgentRun).where(AgentRun.id == run_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None:
                return False
            if record.status == "interrupted":
                return _load_json(record.context_snapshot_json, {}) == dict(checkpoint)
            if (
                record.status not in {"queued", "running", "recovering"}
                or record.worker_id != worker_id
                or int(record.attempt or 0) != int(attempt)
            ):
                return False
            record.status = "interrupted"
            record.context_snapshot_json = _json(dict(checkpoint))
            record.lease_expires_at = None
            record.heartbeat_at = now
            record.updated_at = now
            return True

        return bool(self._run_write_transaction("interrupt_agent_run", _interrupt))

    def resume_interrupted_agent_run(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: float = 45.0,
    ) -> dict[str, Any] | None:
        """Atomically consume the waiting slot for one approval decision."""
        now = datetime.now()

        def _resume(session):
            statement = select(AgentRun).where(AgentRun.id == run_id)
            if not self._is_sqlite_engine:
                statement = statement.with_for_update()
            record = session.execute(statement).scalars().first()
            if record is None or record.status != "interrupted" or record.cancel_requested:
                return None
            record.status = "running"
            record.worker_id = worker_id
            record.heartbeat_at = now
            record.lease_expires_at = now + timedelta(seconds=max(5.0, lease_seconds))
            record.updated_at = now
            session.flush()
            return _run_dict(record)

        return self._run_write_transaction("resume_interrupted_agent_run", _resume)

    def cancel_legacy_engine_runs(self) -> int:
        """Hard-cut active pre-LangGraph runs without converting checkpoints."""
        now = datetime.now()

        def _cancel(session):
            records = (
                session.execute(
                    select(AgentRun).where(AgentRun.status.in_(_ACTIVE_RUN_STATUSES))
                )
                .scalars()
                .all()
            )
            changed = 0
            for record in records:
                request = _load_json(record.request_json, {})
                checkpoint = _load_json(record.context_snapshot_json, {})
                if request.get("engine") == "langgraph" or checkpoint.get("engine") == "langgraph":
                    continue
                record.status = "cancelled"
                record.active_slot = None
                record.error_code = "legacy_engine_cutover"
                record.error_detail = "legacy_engine_cutover"
                record.context_snapshot_json = None
                record.lease_expires_at = None
                record.cancel_requested = True
                record.finished_at = now
                record.updated_at = now
                changed += 1
            return changed

        return int(self._run_write_transaction("cancel_legacy_engine_runs", _cancel))
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
            record.context_snapshot_json = None
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
        conclusions: Sequence[Mapping[str, Any]] = (),
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
        normalized_messages = [dict(message) for message in messages if isinstance(message, Mapping)]
        trace_payload = dict(trace or {})

        def _commit(session):
            run_statement = select(AgentRun).where(AgentRun.id == run_id)
            conversation_statement = select(ChatConversation).where(ChatConversation.id == conversation_id)
            if not self._is_sqlite_engine:
                run_statement = run_statement.with_for_update()
                conversation_statement = conversation_statement.with_for_update()
            run = session.execute(run_statement).scalars().first()
            conversation = session.execute(conversation_statement).scalars().first()
            if run is None or conversation is None:
                return False
            if run.status in _TERMINAL_RUN_STATUSES:
                return run.status == status
            if (
                run.status not in _ACTIVE_RUN_STATUSES
                or (worker_id is not None and run.worker_id != worker_id)
                or (attempt is not None and int(run.attempt or 0) != int(attempt))
            ):
                return False

            session.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conversation_id))
            latest_preview = ""
            for sequence, message in enumerate(normalized_messages):
                content = message.get("content")
                content_text = content if isinstance(content, str) else str(content or "")
                latest_preview = content_text or latest_preview
                created_at = message.get("created_at")
                if not isinstance(created_at, datetime):
                    try:
                        created_at = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                    except (TypeError, ValueError):
                        created_at = now
                session.add(
                    ChatMessage(
                        id=str(message.get("id") or f"{conversation_id}-{sequence}")[:64],
                        conversation_id=conversation_id,
                        role=str(message.get("role") or "user")[:16],
                        content=content_text,
                        sequence=sequence,
                        created_at=created_at,
                    )
                )
            conversation.preview_text = latest_preview[:200] if latest_preview else None
            if agent_context is not None:
                conversation.agent_context_json = _json(dict(agent_context))
            if generated_title and conversation.title_source != "manual":
                conversation.title = str(generated_title).strip()[:120] or "新对话"
                conversation.title_source = "auto"
            conversation.updated_at = now

            for artifact in artifacts:
                artifact_id = str(getattr(artifact, "artifact_id", "") or "")
                artifact_fingerprint = str(getattr(artifact, "fingerprint", "") or "")
                duplicate_fingerprint = (
                    session.execute(
                        select(AgentArtifact.id).where(
                            AgentArtifact.conversation_id == conversation_id,
                            AgentArtifact.fingerprint == artifact_fingerprint,
                        )
                    )
                    .scalars()
                    .first()
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
                session.add(
                    AgentArtifact(
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
                            coverage.model_dump_json() if hasattr(coverage, "model_dump_json") else _json(coverage)
                        ),
                        sources_json=_json(
                            [
                                source.model_dump(mode="json") if hasattr(source, "model_dump") else source
                                for source in sources
                            ]
                        ),
                        fingerprint=artifact_fingerprint,
                        lineage_json=_json(list(getattr(artifact, "lineage", ()))),
                        payload_json=_json(getattr(artifact, "payload", {})),
                        produced_at=getattr(artifact, "produced_at", now),
                        created_at=now,
                    )
                )

            self._register_financial_conclusions_in_session(
                session,
                run_id=run_id,
                conversation_id=conversation_id,
                tenant_id=run.tenant_id,
                owner_id=run.owner_id,
                conclusions=conclusions,
            )

            trace_record = (
                session.execute(
                    select(AgentRunTrace).where(
                        AgentRunTrace.run_id == run_id,
                        AgentRunTrace.orchestrator_mode == "langgraph",
                    )
                )
                .scalars()
                .first()
            )
            if trace_record is None:
                trace_record = AgentRunTrace(
                    id=f"{run_id}:langgraph"[:64],
                    run_id=run_id,
                    conversation_id=conversation_id,
                    orchestrator_mode="langgraph",
                    status=str(trace_payload.get("status") or status),
                    created_at=now,
                )
                session.add(trace_record)
            trace_record.status = str(trace_payload.get("status") or status)
            trace_record.error_code = str(trace_payload.get("error_code") or error_code or "") or None
            trace_field_map = {
                "stage_durations": "stage_durations_json",
                "outcomes": "outcomes_json",
                "coverage": "coverage_json",
                "latest_stage": "latest_stage_json",
                "compiled_plan": "compiled_plan_json",
                "quality_projection": "quality_projection_json",
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
                    elif source_key == "quality_projection":
                        from src.storage.mixins.agent_run_trace import (
                            redact_agent_trace,
                        )

                        current_projection = {}
                        try:
                            parsed_projection = json.loads(
                                trace_record.quality_projection_json
                                or "{}"
                            )
                            if isinstance(parsed_projection, Mapping):
                                current_projection = dict(
                                    parsed_projection
                                )
                        except (TypeError, ValueError):
                            current_projection = {}
                        incoming_projection = trace_payload[source_key]
                        if not isinstance(
                            incoming_projection,
                            Mapping,
                        ):
                            raise ValueError(
                                "quality_projection must be a mapping"
                            )
                        encoded_value = _json(
                            {
                                **current_projection,
                                **redact_agent_trace(
                                    incoming_projection
                                ),
                            }
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
            run.result_json = _json(
                {
                    "message_count": len(normalized_messages),
                    "artifact_count": len(artifacts),
                    "trace_status": trace_record.status,
                }
            )
            run.context_snapshot_json = None
            run.cancel_requested = status == "cancelled"
            run.lease_expires_at = None
            run.finished_at = now
            run.updated_at = now
            return True

        return bool(self._run_write_transaction("commit_agent_run_terminal", _commit))
