"""Method group extracted from agent_governance."""

from __future__ import annotations

import src.storage.mixins.agent_governance as _base

for _name, _value in vars(_base).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


class _AgentGovernanceMethods3:
    def list_agent_capability_grants(
        self,
        *,
        tenant_id: str,
        owner_id: str,
    ) -> list[dict[str, Any]]:
        with self.get_session() as session:
            records = (
                session.execute(
                    select(AgentCapabilityGrant)
                    .where(
                        AgentCapabilityGrant.tenant_id == tenant_id,
                        AgentCapabilityGrant.owner_id == owner_id,
                    )
                    .order_by(AgentCapabilityGrant.capability.asc())
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": record.id,
                    "capability": record.capability,
                    "decision": record.decision,
                    "reason": record.reason,
                    "created_at": _iso(record.created_at),
                    "updated_at": _iso(record.updated_at),
                }
                for record in records
            ]

    def upsert_agent_capability_grant(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        capability: str,
        decision: str,
        reason: str | None,
    ) -> dict[str, Any]:
        if decision not in {"allow", "deny"}:
            raise ValueError("decision must be allow or deny")
        now = datetime.now()
        with self.session_scope() as session:
            record = (
                session.execute(
                    select(AgentCapabilityGrant).where(
                        AgentCapabilityGrant.tenant_id == tenant_id,
                        AgentCapabilityGrant.owner_id == owner_id,
                        AgentCapabilityGrant.capability == capability,
                    )
                )
                .scalars()
                .first()
            )
            if record is None:
                record = AgentCapabilityGrant(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    capability=capability,
                    created_at=now,
                )
                session.add(record)
            record.decision = decision
            record.reason = reason
            record.updated_at = now
            self._append_agent_audit_event_in_session(
                session,
                tenant_id=tenant_id,
                owner_id=owner_id,
                event_type="capability_grant_changed",
                resource_type="capability",
                resource_id=capability,
                outcome=decision,
                metadata={"reason": reason},
            )
            session.flush()
            return {
                "id": record.id,
                "capability": record.capability,
                "decision": record.decision,
                "reason": record.reason,
                "updated_at": _iso(record.updated_at),
            }

    def prepare_agent_run_authorization(
        self,
        *,
        run_id: str,
        conversation_id: str,
        task_descriptors: Sequence[Mapping[str, Any]],
        reviewed_action_fingerprints: Sequence[str],
        source_run_id: str | None,
        registry_manifest: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve grants and mint run-scoped approval receipts."""

        reviewed = frozenset(
            str(value)
            for value in reviewed_action_fingerprints
            if str(value)
        )
        with self.session_scope() as session:
            run = session.get(AgentRun, run_id)
            if (
                run is None
                or run.conversation_id != conversation_id
            ):
                return {
                    "authorized_tasks": {},
                    "approved_action_fingerprints": [],
                    "receipt_by_task": {},
                }
            release_state = (
                self._ensure_capability_release_in_session(
                    session,
                    manifest=registry_manifest,
                    owner_id=run.owner_id,
                )
                if registry_manifest is not None
                else {"authorized": True}
            )
            release_matches = bool(release_state["authorized"])
            if not release_matches:
                self._append_agent_audit_event_in_session(
                    session,
                    tenant_id=run.tenant_id,
                    owner_id=run.owner_id,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    event_type="capability_release_mismatch",
                    resource_type="capability_release",
                    resource_id=str(
                        release_state.get("release_id") or ""
                    ),
                    outcome="denied",
                    metadata=release_state,
                )
            capabilities = {
                str(item.get("capability") or "")
                for item in task_descriptors
            }
            grants = (
                session.execute(
                    select(AgentCapabilityGrant).where(
                        AgentCapabilityGrant.tenant_id
                        == run.tenant_id,
                        AgentCapabilityGrant.owner_id == run.owner_id,
                        AgentCapabilityGrant.capability.in_(
                            capabilities
                        ),
                    )
                )
                .scalars()
                .all()
                if capabilities
                else []
            )
            grant_by_capability = {
                grant.capability: grant.decision
                for grant in grants
            }
            source_run = (
                session.get(AgentRun, source_run_id)
                if source_run_id
                else None
            )
            source_valid = bool(
                source_run
                and source_run.conversation_id == conversation_id
                and source_run.tenant_id == run.tenant_id
                and source_run.owner_id == run.owner_id
                and source_run.status == "blocked"
            )
            authorized_tasks: dict[str, bool] = {}
            approved: set[str] = set()
            receipt_by_task: dict[str, str] = {}
            now = datetime.now()
            for descriptor in task_descriptors:
                task_id = str(descriptor.get("task_id") or "")
                capability = str(
                    descriptor.get("capability") or ""
                )
                effect = str(descriptor.get("effect") or "read")
                explicit_decision = grant_by_capability.get(
                    capability
                )
                grant_authorized = (
                    explicit_decision == "allow"
                    if explicit_decision is not None
                    else effect not in _DEFAULT_DENIED_EFFECTS
                )
                # A capability-release mismatch must stop effectful work, but
                # it must not take every built-in read capability offline.
                # Registry drift is surfaced through release metadata/audit;
                # read-only queries continue under the current typed runtime.
                authorized = grant_authorized and (
                    release_matches or effect == "read"
                )
                authorized_tasks[task_id] = authorized
                if not authorized:
                    self._append_agent_audit_event_in_session(
                        session,
                        tenant_id=run.tenant_id,
                        owner_id=run.owner_id,
                        run_id=run_id,
                        conversation_id=conversation_id,
                        event_type="capability_authorization",
                        resource_type="capability",
                        resource_id=capability,
                        outcome="denied",
                        metadata={
                            "task_id": task_id,
                            "effect": effect,
                            "policy": (
                                "release_mismatch"
                                if not release_matches
                                and effect != "read"
                                else (
                                    "explicit"
                                    if explicit_decision
                                    else "default"
                                )
                            ),
                        },
                    )
                    continue
                if not bool(
                    descriptor.get("requires_approval")
                ):
                    continue
                fingerprint = str(
                    descriptor.get("action_fingerprint") or ""
                )
                confirmation = str(
                    descriptor.get("confirmation") or ""
                )
                if (
                    confirmation != "explicit"
                    or fingerprint not in reviewed
                    or not source_valid
                ):
                    continue
                existing = (
                    session.execute(
                        select(AgentApprovalReceipt).where(
                            AgentApprovalReceipt.approved_run_id
                            == run_id,
                            AgentApprovalReceipt.task_id == task_id,
                            AgentApprovalReceipt.action_fingerprint
                            == fingerprint,
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing is None:
                    existing = AgentApprovalReceipt(
                        id=uuid.uuid4().hex,
                        tenant_id=run.tenant_id,
                        owner_id=run.owner_id,
                        conversation_id=conversation_id,
                        source_run_id=str(source_run_id),
                        approved_run_id=run_id,
                        task_id=task_id,
                        capability=capability,
                        effect=effect,
                        action_fingerprint=fingerprint,
                        action_snapshot_json=_json(
                            descriptor.get("action_snapshot") or {}
                        ),
                        status="issued",
                        issued_at=now,
                        expires_at=now + timedelta(minutes=30),
                    )
                    session.add(existing)
                    self._append_agent_audit_event_in_session(
                        session,
                        tenant_id=run.tenant_id,
                        owner_id=run.owner_id,
                        run_id=run_id,
                        conversation_id=conversation_id,
                        event_type="approval_receipt_issued",
                        resource_type="approval_receipt",
                        resource_id=existing.id,
                        outcome="issued",
                        metadata={
                            "source_run_id": source_run_id,
                            "task_id": task_id,
                            "capability": capability,
                            "effect": effect,
                            "action_fingerprint": fingerprint,
                        },
                    )
                if (
                    existing.status in {"issued", "used"}
                    and existing.expires_at >= now
                ):
                    approved.add(fingerprint)
                    receipt_by_task[task_id] = existing.id
            return {
                "authorized_tasks": authorized_tasks,
                "approved_action_fingerprints": sorted(approved),
                "receipt_by_task": receipt_by_task,
                "release": release_state,
            }

    def authorize_agent_effect_step(
        self,
        *,
        run_id: str,
        task_id: str,
        step_id: str,
        receipt_id: str,
    ) -> bool:
        now = datetime.now()
        with self.session_scope() as session:
            receipt = session.get(AgentApprovalReceipt, receipt_id)
            if (
                receipt is None
                or receipt.approved_run_id != run_id
                or receipt.task_id != task_id
                or receipt.status not in {"issued", "used"}
                or receipt.expires_at < now
            ):
                if receipt is not None and receipt.expires_at < now:
                    receipt.status = "expired"
                return False
            receipt.status = "used"
            receipt.first_used_at = receipt.first_used_at or now
            receipt.last_used_at = now
            receipt.first_step_id = receipt.first_step_id or step_id
            receipt.use_count = int(receipt.use_count or 0) + 1
            self._append_agent_audit_event_in_session(
                session,
                tenant_id=receipt.tenant_id,
                owner_id=receipt.owner_id,
                run_id=run_id,
                conversation_id=receipt.conversation_id,
                event_type="approved_effect_step",
                resource_type="approval_receipt",
                resource_id=receipt.id,
                outcome="authorized",
                metadata={
                    "task_id": task_id,
                    "step_id": step_id,
                    "capability": receipt.capability,
                },
            )
            return True



__all__ = ["_AgentGovernanceMethods3"]
