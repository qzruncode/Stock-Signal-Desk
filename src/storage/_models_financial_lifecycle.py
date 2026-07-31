"""Financial conclusion lifecycle and forward-outcome models."""

from __future__ import annotations

import src.storage.models as _models

for _name, _value in vars(_models).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = [
    "AgentFinancialConclusion",
    "AgentFinancialOutcome",
]


class AgentFinancialConclusion(Base):
    """One structured, audit-safe conclusion emitted by an Agent run."""

    __tablename__ = "agent_financial_conclusions"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    owner_id = Column(String(128), nullable=False, index=True)
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
    task_id = Column(String(96), nullable=False, index=True)
    conclusion_type = Column(String(48), nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    name = Column(String(80))
    verdict = Column(String(32), nullable=False, index=True)
    contract_version = Column(String(64))
    as_of_at = Column(DateTime, nullable=False, index=True)
    baseline_trade_date = Column(Date, index=True)
    baseline_price = Column(Float)
    baseline_source = Column(String(64))
    thesis_json = Column(Text, nullable=False, default="{}")
    evidence_json = Column(Text, nullable=False, default="{}")
    evidence_fingerprint = Column(String(64), nullable=False, index=True)
    lifecycle_status = Column(
        String(24),
        nullable=False,
        default="pending",
        index=True,
    )
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
            "task_id",
            "conclusion_type",
            "symbol",
            name="uix_agent_financial_conclusion_run_task_symbol",
        ),
        Index(
            "ix_agent_financial_conclusion_owner_status",
            "tenant_id",
            "owner_id",
            "lifecycle_status",
        ),
    )


class AgentFinancialOutcome(Base):
    """Observed market outcome at one fixed trading-session horizon."""

    __tablename__ = "agent_financial_outcomes"

    id = Column(String(64), primary_key=True)
    conclusion_id = Column(
        String(64),
        ForeignKey("agent_financial_conclusions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    horizon_trading_days = Column(Integer, nullable=False, index=True)
    status = Column(String(24), nullable=False, default="pending", index=True)
    evaluated_through_date = Column(Date, index=True)
    end_price = Column(Float)
    max_high = Column(Float)
    min_low = Column(Float)
    return_pct = Column(Float)
    max_favorable_excursion_pct = Column(Float)
    max_adverse_excursion_pct = Column(Float)
    outcome_label = Column(String(32), index=True)
    directional_success = Column(Boolean)
    engine_version = Column(
        String(32),
        nullable=False,
        default="financial-outcome-1.0",
    )
    diagnostics_json = Column(Text, nullable=False, default="{}")
    evaluated_at = Column(DateTime, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )

    __table_args__ = (
        UniqueConstraint(
            "conclusion_id",
            "horizon_trading_days",
            name="uix_agent_financial_outcome_horizon",
        ),
        Index(
            "ix_agent_financial_outcome_status_horizon",
            "status",
            "horizon_trading_days",
        ),
    )
