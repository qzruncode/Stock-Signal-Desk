"""SQLAlchemy model group 2."""

from __future__ import annotations

import src.storage.models as _models

for _name, _value in vars(_models).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = ['AgentArtifact', 'AgentRunTrace', 'AgentRuntimeControl', 'AgentRun', 'AgentRunEvent', 'AgentStepExecution', 'AgentEffectOutbox', 'AgentRateLimitBucket', 'AgentResourceLease', 'AgentCircuitBreaker', 'BacktestResult', 'BacktestSummary', 'PortfolioAccount', 'PortfolioTrade', 'PortfolioCashLedger']

class AgentArtifact(Base):
    """Independent, versioned orchestration artifact payload."""

    __tablename__ = "agent_artifacts"

    id = Column(String(64), primary_key=True)
    conversation_id = Column(
        String(64),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(String(64), nullable=False, index=True)
    schema_version = Column(String(64), nullable=False)
    producer_node_id = Column(String(64), nullable=False, index=True)
    resource_type = Column(String(64), nullable=False, index=True)
    coverage_json = Column(Text, nullable=False)
    sources_json = Column(Text, nullable=False, default="[]")
    fingerprint = Column(String(64), nullable=False, index=True)
    lineage_json = Column(Text, nullable=False, default="[]")
    payload_json = Column(Text, nullable=False)
    produced_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index(
            "ix_agent_artifacts_conversation_resource_time",
            "conversation_id",
            "resource_type",
            "produced_at",
        ),
        UniqueConstraint(
            "conversation_id",
            "fingerprint",
            name="uix_agent_artifact_conversation_fingerprint",
        ),
    )

class AgentRunTrace(Base):
    """Redacted stage-level observability record for one orchestrator run."""

    __tablename__ = "agent_run_traces"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), nullable=False, index=True)
    conversation_id = Column(
        String(64),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    orchestrator_mode = Column(String(24), nullable=False, index=True)
    status = Column(String(24), nullable=False, index=True)
    error_code = Column(String(64))
    schema_version = Column(String(64))
    model_config_json = Column(Text, nullable=False, default="{}")
    stage_durations_json = Column(Text, nullable=False, default="{}")
    raw_outline_json = Column(Text)
    normalized_outline_json = Column(Text)
    raw_intents_json = Column(Text, nullable=False, default="{}")
    normalized_intents_json = Column(Text, nullable=False, default="{}")
    repairs_json = Column(Text, nullable=False, default="[]")
    verification_json = Column(Text)
    goal_state_json = Column(Text)
    latest_stage_json = Column(Text)
    compiled_plan_json = Column(Text)
    outcomes_json = Column(Text)
    coverage_json = Column(Text)
    quality_projection_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "orchestrator_mode",
            name="uix_agent_run_trace_run_mode",
        ),
        Index(
            "ix_agent_run_trace_conversation_created",
            "conversation_id",
            "created_at",
        ),
    )

class AgentRuntimeControl(Base):
    """Singleton rows used to serialize cross-worker admission decisions."""

    __tablename__ = "agent_runtime_controls"

    name = Column(String(64), primary_key=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )

class AgentRun(Base):
    """Durable lifecycle record for one Agent request.

    ``active_slot`` is equal to ``conversation_id`` while the run is queued or
    running and becomes NULL at a terminal state.  A unique constraint on this
    nullable column gives every supported database an atomic, cross-process
    "one active run per conversation" guard without relying on a process-local
    dictionary.
    """

    __tablename__ = "agent_runs"

    id = Column(String(64), primary_key=True)
    conversation_id = Column(
        String(64),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(String(64), nullable=False, default="local", index=True)
    owner_id = Column(String(128), nullable=False, default="admin", index=True)
    active_slot = Column(String(64), unique=True)
    status = Column(String(24), nullable=False, default="queued", index=True)
    request_json = Column(Text, nullable=False, default="{}")
    context_snapshot_json = Column(Text)
    result_json = Column(Text)
    final_text = Column(Text)
    error_code = Column(String(64))
    error_detail = Column(Text)
    worker_id = Column(String(128), index=True)
    lease_expires_at = Column(DateTime, index=True)
    heartbeat_at = Column(DateTime, index=True)
    cancel_requested = Column(Boolean, nullable=False, default=False, index=True)
    event_cursor = Column(Integer, nullable=False, default=0)
    attempt = Column(Integer, nullable=False, default=1)
    tool_call_count = Column(Integer, nullable=False, default=0)
    provider_call_count = Column(Integer, nullable=False, default=0)
    estimated_token_count = Column(Integer, nullable=False, default=0)
    estimated_cost_micros = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    started_at = Column(DateTime)
    finished_at = Column(DateTime, index=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        index=True,
    )

    __table_args__ = (
        Index("ix_agent_runs_tenant_owner_created", "tenant_id", "owner_id", "created_at"),
        Index("ix_agent_runs_status_lease", "status", "lease_expires_at"),
    )

class AgentRunEvent(Base):
    """Ordered, replayable assistant-stream event for a durable Agent run."""

    __tablename__ = "agent_run_events"

    id = Column(String(96), primary_key=True)
    run_id = Column(
        String(64),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(32), nullable=False, index=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uix_agent_run_event_sequence"),
        Index("ix_agent_run_events_run_sequence", "run_id", "sequence"),
    )

class AgentStepExecution(Base):
    """Idempotent execution ledger for one compiled workflow call."""

    __tablename__ = "agent_step_executions"

    idempotency_key = Column(String(96), primary_key=True)
    run_id = Column(
        String(64),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id = Column(String(64), nullable=False, index=True)
    task_id = Column(String(96), nullable=False, index=True)
    step_id = Column(String(96), nullable=False)
    tool_name = Column(String(128), nullable=False, index=True)
    effect = Column(String(24), nullable=False, default="read", index=True)
    status = Column(String(24), nullable=False, default="pending", index=True)
    arguments_json = Column(Text, nullable=False, default="{}")
    result_json = Column(Text)
    error_code = Column(String(64))
    error_detail = Column(Text)
    worker_id = Column(String(128), index=True)
    lease_expires_at = Column(DateTime, index=True)
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=1)
    reuse_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        index=True,
    )

    __table_args__ = (
        Index("ix_agent_steps_run_status", "run_id", "status"),
        Index("ix_agent_steps_tool_status", "tool_name", "status"),
    )

class AgentEffectOutbox(Base):
    """Transactional dispatch ledger for non-read effects.

    Effect adapters can persist a dispatch request and its idempotency key
    before talking to an external system.  Recovered workers then continue the
    same record instead of issuing an unrelated duplicate request.
    """

    __tablename__ = "agent_effect_outbox"

    idempotency_key = Column(String(96), primary_key=True)
    run_id = Column(
        String(64),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tool_name = Column(String(128), nullable=False, index=True)
    payload_json = Column(Text, nullable=False)
    status = Column(String(24), nullable=False, default="pending", index=True)
    provider_reference = Column(String(256))
    result_json = Column(Text)
    error_detail = Column(Text)
    attempt = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        index=True,
    )

    __table_args__ = (Index("ix_agent_effect_outbox_status_created", "status", "created_at"),)

class AgentRateLimitBucket(Base):
    """Database-backed admission counter shared by all API workers."""

    __tablename__ = "agent_rate_limit_buckets"

    id = Column(String(128), primary_key=True)
    key_hash = Column(String(64), nullable=False, index=True)
    window_started_at = Column(DateTime, nullable=False, index=True)
    request_count = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=False, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = (Index("ix_agent_rate_limit_expiry", "expires_at"),)

class AgentResourceLease(Base):
    """One database-coordinated concurrency slot."""

    __tablename__ = "agent_resource_leases"

    id = Column(String(192), primary_key=True)
    resource_name = Column(String(160), nullable=False, index=True)
    slot_index = Column(Integer, nullable=False)
    lease_owner = Column(String(128), index=True)
    run_id = Column(String(64), index=True)
    step_id = Column(String(96))
    lease_expires_at = Column(DateTime, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "resource_name",
            "slot_index",
            name="uix_agent_resource_slot",
        ),
        Index("ix_agent_resource_lease_expiry", "resource_name", "lease_expires_at"),
    )

class AgentCircuitBreaker(Base):
    """Shared circuit state for a tool or provider dependency."""

    __tablename__ = "agent_circuit_breakers"

    resource_name = Column(String(160), primary_key=True)
    state = Column(String(16), nullable=False, default="closed", index=True)
    failure_count = Column(Integer, nullable=False, default=0)
    opened_until = Column(DateTime, index=True)
    probe_owner = Column(String(128))
    last_error = Column(Text)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)

class BacktestResult(Base):
    """单条分析记录的回测结果。"""

    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_history_id = Column(Integer, ForeignKey("analysis_history.id"), nullable=False, index=True)
    code = Column(String(10), nullable=False, index=True)
    analysis_date = Column(Date, index=True)
    eval_window_days = Column(Integer, nullable=False, default=10)
    engine_version = Column(String(16), nullable=False, default="v1")
    eval_status = Column(String(16), nullable=False, default="pending")
    evaluated_at = Column(DateTime, default=datetime.now, index=True)
    operation_advice = Column(String(20))
    position_recommendation = Column(String(8))
    start_price = Column(Float)
    end_close = Column(Float)
    max_high = Column(Float)
    min_low = Column(Float)
    stock_return_pct = Column(Float)
    direction_expected = Column(String(16))
    direction_correct = Column(Boolean, nullable=True)
    outcome = Column(String(16))
    stop_loss = Column(Float)
    take_profit = Column(Float)
    hit_stop_loss = Column(Boolean)
    hit_take_profit = Column(Boolean)
    first_hit = Column(String(16))
    first_hit_date = Column(Date)
    first_hit_trading_days = Column(Integer)
    simulated_entry_price = Column(Float)
    simulated_exit_price = Column(Float)
    simulated_exit_reason = Column(String(24))
    simulated_return_pct = Column(Float)

    __table_args__ = (
        UniqueConstraint(
            "analysis_history_id", "eval_window_days", "engine_version", name="uix_backtest_analysis_window_version"
        ),
        Index("ix_backtest_code_date", "code", "analysis_date"),
    )

class BacktestSummary(Base):
    """回测汇总指标。"""

    __tablename__ = "backtest_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(16), nullable=False, index=True)
    code = Column(String(16), index=True)
    eval_window_days = Column(Integer, nullable=False, default=10)
    engine_version = Column(String(16), nullable=False, default="v1")
    computed_at = Column(DateTime, default=datetime.now, index=True)
    total_evaluations = Column(Integer, default=0)
    completed_count = Column(Integer, default=0)
    insufficient_count = Column(Integer, default=0)
    long_count = Column(Integer, default=0)
    cash_count = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    loss_count = Column(Integer, default=0)
    neutral_count = Column(Integer, default=0)
    direction_accuracy_pct = Column(Float)
    win_rate_pct = Column(Float)
    neutral_rate_pct = Column(Float)
    avg_stock_return_pct = Column(Float)
    avg_simulated_return_pct = Column(Float)
    stop_loss_trigger_rate = Column(Float)
    take_profit_trigger_rate = Column(Float)
    ambiguous_rate = Column(Float)
    avg_days_to_first_hit = Column(Float)
    advice_breakdown_json = Column(Text)
    diagnostics_json = Column(Text)

    __table_args__ = (
        UniqueConstraint(
            "scope", "code", "eval_window_days", "engine_version", name="uix_backtest_summary_scope_code_window_version"
        ),
    )

class PortfolioAccount(Base):
    """Portfolio account metadata."""

    __tablename__ = "portfolio_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String(64), index=True)
    name = Column(String(64), nullable=False)
    broker = Column(String(64))
    market = Column(String(8), nullable=False, default="cn", index=True)
    base_currency = Column(String(8), nullable=False, default="CNY")
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (Index("ix_portfolio_account_owner_active", "owner_id", "is_active"),)

class PortfolioTrade(Base):
    """Executed trade events."""

    __tablename__ = "portfolio_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    trade_uid = Column(String(128))
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default="cn")
    currency = Column(String(8), nullable=False, default="CNY")
    trade_date = Column(Date, nullable=False, index=True)
    side = Column(String(8), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    fee = Column(Float, default=0.0)
    tax = Column(Float, default=0.0)
    note = Column(String(255))
    dedup_hash = Column(String(64), index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        UniqueConstraint("account_id", "trade_uid", name="uix_portfolio_trade_uid"),
        UniqueConstraint("account_id", "dedup_hash", name="uix_portfolio_trade_dedup_hash"),
        Index("ix_portfolio_trade_account_date", "account_id", "trade_date"),
    )

class PortfolioCashLedger(Base):
    """Cash in/out events."""

    __tablename__ = "portfolio_cash_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    event_date = Column(Date, nullable=False, index=True)
    direction = Column(String(8), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="CNY")
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (Index("ix_portfolio_cash_account_date", "account_id", "event_date"),)
