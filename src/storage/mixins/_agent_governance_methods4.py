"""Method group extracted from agent_governance."""

from __future__ import annotations

import src.storage.mixins.agent_governance as _base

for _name, _value in vars(_base).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


class _AgentGovernanceMethods4:
    def list_agent_approval_receipts(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.get_session() as session:
            records = (
                session.execute(
                    select(AgentApprovalReceipt)
                    .where(
                        AgentApprovalReceipt.tenant_id == tenant_id,
                        AgentApprovalReceipt.owner_id == owner_id,
                    )
                    .order_by(
                        AgentApprovalReceipt.issued_at.desc()
                    )
                    .limit(max(1, min(limit, 1000)))
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": record.id,
                    "conversation_id": record.conversation_id,
                    "source_run_id": record.source_run_id,
                    "approved_run_id": record.approved_run_id,
                    "task_id": record.task_id,
                    "capability": record.capability,
                    "effect": record.effect,
                    "action_fingerprint": (
                        record.action_fingerprint
                    ),
                    "action_snapshot": _load_json(
                        record.action_snapshot_json,
                        {},
                    ),
                    "status": record.status,
                    "issued_at": _iso(record.issued_at),
                    "expires_at": _iso(record.expires_at),
                    "first_used_at": _iso(record.first_used_at),
                    "last_used_at": _iso(record.last_used_at),
                    "use_count": int(record.use_count or 0),
                }
                for record in records
            ]

    def list_agent_audit_events(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.get_session() as session:
            statement = select(AgentAuditEvent).where(
                AgentAuditEvent.tenant_id == tenant_id,
                AgentAuditEvent.owner_id == owner_id,
            )
            if event_type:
                statement = statement.where(
                    AgentAuditEvent.event_type == event_type
                )
            records = (
                session.execute(
                    statement.order_by(
                        AgentAuditEvent.created_at.desc()
                    ).limit(max(1, min(limit, 1000)))
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": record.id,
                    "run_id": record.run_id,
                    "conversation_id": record.conversation_id,
                    "event_type": record.event_type,
                    "resource_type": record.resource_type,
                    "resource_id": record.resource_id,
                    "outcome": record.outcome,
                    "metadata": _load_json(
                        record.metadata_json,
                        {},
                    ),
                    "previous_hash": record.previous_hash,
                    "event_hash": record.event_hash,
                    "created_at": _iso(record.created_at),
                }
                for record in records
            ]


__all__ = ["_AgentGovernanceMethods4"]
