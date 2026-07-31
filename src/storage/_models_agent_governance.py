"""Internal capability authorization, approval receipts and audit events."""

from __future__ import annotations

import src.storage.models as _models

for _name, _value in vars(_models).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = [
    "AgentApprovalReceipt",
    "AgentAuditEvent",
    "AgentCapabilityGrant",
    "AgentCapabilityRelease",
    "AgentUserMemory",
]


class AgentCapabilityGrant(Base):
    """Per-owner override for one built-in capability."""

    __tablename__ = "agent_capability_grants"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    owner_id = Column(String(128), nullable=False, index=True)
    capability = Column(String(96), nullable=False, index=True)
    decision = Column(String(16), nullable=False, index=True)
    reason = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "owner_id",
            "capability",
            name="uix_agent_capability_grant_owner",
        ),
    )


class AgentApprovalReceipt(Base):
    """Run-scoped proof that a reviewed side effect was explicitly approved."""

    __tablename__ = "agent_approval_receipts"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    owner_id = Column(String(128), nullable=False, index=True)
    conversation_id = Column(String(64), nullable=False, index=True)
    source_run_id = Column(String(64), nullable=False, index=True)
    approved_run_id = Column(String(64), nullable=False, index=True)
    task_id = Column(String(96), nullable=False)
    capability = Column(String(96), nullable=False, index=True)
    effect = Column(String(24), nullable=False, index=True)
    action_fingerprint = Column(String(64), nullable=False, index=True)
    action_snapshot_json = Column(Text, nullable=False, default="{}")
    status = Column(String(24), nullable=False, default="issued", index=True)
    issued_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    first_used_at = Column(DateTime)
    last_used_at = Column(DateTime)
    first_step_id = Column(String(96))
    use_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "approved_run_id",
            "task_id",
            "action_fingerprint",
            name="uix_agent_approval_receipt_run_task_action",
        ),
        Index(
            "ix_agent_approval_receipt_owner_status",
            "tenant_id",
            "owner_id",
            "status",
        ),
    )


class AgentAuditEvent(Base):
    """Append-only, hash-chained governance event."""

    __tablename__ = "agent_audit_events"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    owner_id = Column(String(128), nullable=False, index=True)
    run_id = Column(String(64), index=True)
    conversation_id = Column(String(64), index=True)
    event_type = Column(String(64), nullable=False, index=True)
    resource_type = Column(String(48), nullable=False, index=True)
    resource_id = Column(String(128), nullable=False, index=True)
    outcome = Column(String(24), nullable=False, index=True)
    metadata_json = Column(Text, nullable=False, default="{}")
    previous_hash = Column(String(64))
    event_hash = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)

    __table_args__ = (
        Index(
            "ix_agent_audit_owner_created",
            "tenant_id",
            "owner_id",
            "created_at",
        ),
    )


class AgentCapabilityRelease(Base):
    """Versioned snapshot of the executable built-in capability registry."""

    __tablename__ = "agent_capability_releases"

    id = Column(String(64), primary_key=True)
    release_version = Column(String(96), nullable=False, unique=True, index=True)
    registry_fingerprint = Column(String(64), nullable=False, index=True)
    manifest_json = Column(Text, nullable=False)
    status = Column(String(24), nullable=False, default="draft", index=True)
    evaluation_suite = Column(String(96))
    minimum_pass_rate = Column(Float, nullable=False, default=1.0)
    evaluation_summary_json = Column(Text, nullable=False, default="{}")
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    activated_at = Column(DateTime, index=True)
    retired_at = Column(DateTime, index=True)


class AgentUserMemory(Base):
    """User-authored memory; the Agent never creates these implicitly."""

    __tablename__ = "agent_user_memories"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    owner_id = Column(String(128), nullable=False, index=True)
    scope = Column(String(24), nullable=False, default="global", index=True)
    conversation_id = Column(String(64), index=True)
    kind = Column(String(32), nullable=False, default="preference", index=True)
    memory_key = Column(String(96), nullable=False)
    content = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    source = Column(String(24), nullable=False, default="explicit")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "owner_id",
            "scope",
            "conversation_id",
            "memory_key",
            name="uix_agent_user_memory_scope_key",
        ),
        Index(
            "ix_agent_user_memory_owner_enabled",
            "tenant_id",
            "owner_id",
            "enabled",
        ),
    )
