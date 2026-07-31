"""Agent quality, evaluation and user-feedback persistence models."""

from __future__ import annotations

import src.storage.models as _models

for _name, _value in vars(_models).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = [
    "AgentEvaluationCase",
    "AgentEvaluationResult",
    "AgentRunFeedback",
]


class AgentEvaluationCase(Base):
    """Versioned, human-owned release-gate case captured from a real run."""

    __tablename__ = "agent_evaluation_cases"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=False, default="local", index=True)
    owner_id = Column(String(128), nullable=False, default="admin", index=True)
    suite = Column(String(96), nullable=False, default="default", index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text)
    status = Column(String(24), nullable=False, default="active", index=True)
    version = Column(Integer, nullable=False, default=1)
    source_run_id = Column(
        String(64),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        index=True,
    )
    request_snapshot_json = Column(Text, nullable=False, default="{}")
    evidence_snapshot_json = Column(Text, nullable=False, default="{}")
    expectations_json = Column(Text, nullable=False, default="{}")
    tags_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
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
            "suite",
            "name",
            "version",
            name="uix_agent_eval_case_identity",
        ),
        Index(
            "ix_agent_eval_case_owner_status",
            "tenant_id",
            "owner_id",
            "status",
        ),
    )


class AgentEvaluationResult(Base):
    """Deterministic score of one candidate run against one evaluation case."""

    __tablename__ = "agent_evaluation_results"

    id = Column(String(64), primary_key=True)
    case_id = Column(
        String(64),
        ForeignKey("agent_evaluation_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_run_id = Column(
        String(64),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        index=True,
    )
    evaluator_version = Column(String(32), nullable=False, default="1.0")
    status = Column(String(24), nullable=False, index=True)
    total_score = Column(Float, nullable=False, default=0.0, index=True)
    scores_json = Column(Text, nullable=False, default="{}")
    violations_json = Column(Text, nullable=False, default="[]")
    snapshot_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)

    __table_args__ = (
        Index(
            "ix_agent_eval_result_case_created",
            "case_id",
            "created_at",
        ),
        Index(
            "ix_agent_eval_result_run_created",
            "candidate_run_id",
            "created_at",
        ),
    )


class AgentRunFeedback(Base):
    """Explicit user assessment attached to the exact durable Agent run."""

    __tablename__ = "agent_run_feedback"

    id = Column(String(64), primary_key=True)
    run_id = Column(
        String(64),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id = Column(
        String(64),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(String(64), nullable=False, default="local", index=True)
    owner_id = Column(String(128), nullable=False, default="admin", index=True)
    rating = Column(Integer, nullable=False)
    category = Column(String(64), index=True)
    comment = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "tenant_id",
            "owner_id",
            name="uix_agent_feedback_run_owner",
        ),
        Index(
            "ix_agent_feedback_owner_created",
            "tenant_id",
            "owner_id",
            "created_at",
        ),
    )
