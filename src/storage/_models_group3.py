"""SQLAlchemy model group 3."""

from __future__ import annotations

import src.storage.models as _models

for _name, _value in vars(_models).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = ['PortfolioCorporateAction', 'PortfolioPosition', 'PortfolioPositionLot', 'PortfolioDailySnapshot', 'PortfolioFxRate', 'ConversationMessage', 'LLMUsage', 'AlertRuleRecord', 'AlertTriggerRecord', 'AlertNotificationRecord', 'BatchRun', 'BatchSchedule', 'QuoteSnapshot', 'KlineSnapshot', 'RssCache', 'ToolCache', 'MacroIndexDaily', 'BondYieldDaily', 'MacroIndicator', 'WatchlistGroup', 'AgentPromptTemplate', 'WatchlistGroupNameConflict']

class PortfolioCorporateAction(Base):
    """Corporate actions."""

    __tablename__ = "portfolio_corporate_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default="cn")
    currency = Column(String(8), nullable=False, default="CNY")
    effective_date = Column(Date, nullable=False, index=True)
    action_type = Column(String(24), nullable=False)
    cash_dividend_per_share = Column(Float)
    split_ratio = Column(Float)
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (Index("ix_portfolio_ca_account_date", "account_id", "effective_date"),)

class PortfolioPosition(Base):
    """Latest replayed position snapshot."""

    __tablename__ = "portfolio_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default="fifo")
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default="cn")
    currency = Column(String(8), nullable=False, default="CNY")
    quantity = Column(Float, nullable=False, default=0.0)
    avg_cost = Column(Float, nullable=False, default=0.0)
    total_cost = Column(Float, nullable=False, default=0.0)
    last_price = Column(Float, nullable=False, default=0.0)
    market_value_base = Column(Float, nullable=False, default=0.0)
    unrealized_pnl_base = Column(Float, nullable=False, default=0.0)
    valuation_currency = Column(String(8), nullable=False, default="CNY")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "symbol",
            "market",
            "currency",
            "cost_method",
            name="uix_portfolio_position_account_symbol_market_currency",
        ),
    )

class PortfolioPositionLot(Base):
    """Lot-level remaining quantities."""

    __tablename__ = "portfolio_position_lots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default="fifo")
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default="cn")
    currency = Column(String(8), nullable=False, default="CNY")
    open_date = Column(Date, nullable=False, index=True)
    remaining_quantity = Column(Float, nullable=False, default=0.0)
    unit_cost = Column(Float, nullable=False, default=0.0)
    source_trade_id = Column(Integer, ForeignKey("portfolio_trades.id"))
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (Index("ix_portfolio_lot_account_symbol", "account_id", "symbol"),)

class PortfolioDailySnapshot(Base):
    """Daily account snapshot."""

    __tablename__ = "portfolio_daily_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default="fifo")
    base_currency = Column(String(8), nullable=False, default="CNY")
    total_cash = Column(Float, nullable=False, default=0.0)
    total_market_value = Column(Float, nullable=False, default=0.0)
    total_equity = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=False, default=0.0)
    fee_total = Column(Float, nullable=False, default=0.0)
    tax_total = Column(Float, nullable=False, default=0.0)
    fx_stale = Column(Boolean, nullable=False, default=False)
    payload = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "account_id", "snapshot_date", "cost_method", name="uix_portfolio_snapshot_account_date_method"
        ),
    )

class PortfolioFxRate(Base):
    """Cached FX rates."""

    __tablename__ = "portfolio_fx_rates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_currency = Column(String(8), nullable=False, index=True)
    to_currency = Column(String(8), nullable=False, index=True)
    rate_date = Column(Date, nullable=False, index=True)
    rate = Column(Float, nullable=False)
    source = Column(String(32), nullable=False, default="manual")
    is_stale = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (UniqueConstraint("from_currency", "to_currency", "rate_date", name="uix_portfolio_fx_pair_date"),)

class ConversationMessage(Base):
    """Agent 对话历史记录表"""

    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), index=True, nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, index=True)

class LLMUsage(Base):
    """Token-usage audit log."""

    __tablename__ = "llm_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    call_type = Column(String(32), nullable=False, index=True)
    model = Column(String(128), nullable=False)
    stock_code = Column(String(16), nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    called_at = Column(DateTime, default=datetime.now, index=True)

class AlertRuleRecord(Base):
    """Persisted alert rule."""

    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(64), nullable=False, default="local", index=True)
    owner_id = Column(String(128), nullable=False, default="admin", index=True)
    state_json = Column(Text, nullable=False, default="{}")
    next_check_at = Column(DateTime)
    name = Column(String(64), nullable=False)
    target_scope = Column(String(32), nullable=False, default="single_symbol", index=True)
    target = Column(String(64), nullable=False, index=True)
    alert_type = Column(String(32), nullable=False, index=True)
    parameters = Column(Text, nullable=False, default="{}")
    severity = Column(String(16), nullable=False, default="warning", index=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    source = Column(String(16), nullable=False, default="api", index=True)
    cooldown_policy = Column(Text)
    notification_policy = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (
        Index("ix_alert_rule_type_target", "alert_type", "target"),
        Index("ix_alert_rules_next_check", "next_check_at"),
        Index("ix_alert_rules_owner_source", "tenant_id", "owner_id", "source"),
    )

class AlertTriggerRecord(Base):
    """Alert trigger history."""

    __tablename__ = "alert_triggers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fingerprint = Column(String(64))
    rule_id = Column(Integer, index=True)
    target = Column(String(64), nullable=False, index=True)
    observed_value = Column(Float)
    threshold = Column(Float)
    reason = Column(Text)
    data_source = Column(String(64))
    data_timestamp = Column(DateTime, index=True)
    triggered_at = Column(DateTime, default=datetime.now, index=True)
    status = Column(String(16), nullable=False, default="triggered", index=True)
    diagnostics = Column(Text)

    __table_args__ = (
        Index("ix_alert_trigger_rule_time", "rule_id", "triggered_at"),
        Index("ix_alert_trigger_fingerprint", "fingerprint", unique=True),
    )

class AlertNotificationRecord(Base):
    """Notification attempt for alert triggers."""

    __tablename__ = "alert_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trigger_id = Column(Integer, index=True)
    channel = Column(String(32), nullable=False, index=True)
    attempt = Column(Integer, nullable=False, default=1)
    success = Column(Boolean, nullable=False, default=False, index=True)
    error_code = Column(String(64))
    retryable = Column(Boolean, nullable=False, default=False)
    latency_ms = Column(Integer)
    diagnostics = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (Index("ix_alert_notification_trigger_channel", "trigger_id", "channel"),)

class BatchRun(Base):
    """批量跑批记录"""

    __tablename__ = "batch_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, unique=True, index=True)
    triggered_by = Column(String(32), nullable=False, default="manual")
    template_id = Column(String(64))
    template_name = Column(String(100))
    stock_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    fail_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime)
    report_path = Column(Text)
    results_json = Column(Text, default="[]")
    stock_codes_json = Column(Text, default="[]")
    status = Column(String(32), nullable=False, default="completed")
    analysis_mode = Column(String(32), default="template")

    __table_args__ = (Index("ix_batch_runs_started", "started_at"),)

class BatchSchedule(Base):
    """批量跑批定时配置"""

    __tablename__ = "batch_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    enabled = Column(Boolean, nullable=False, default=False)
    times_json = Column(Text, nullable=False, default="[]")
    template_id = Column(String(64))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class QuoteSnapshot(Base):
    """实时行情缓存快照"""

    __tablename__ = "quote_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), nullable=False, unique=True, index=True)
    data = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class KlineSnapshot(Base):
    """K线数据缓存快照"""

    __tablename__ = "kline_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), nullable=False, unique=True, index=True)
    data = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class RssCache(Base):
    """RSS feed / namespace blob 缓存（key→JSON text）。

    与 kline_snapshot 解耦：RSS 缓存键长且体积大（namespace blob ~3.3MB），
    混在 kline 表里排查困难，独立成表便于运维与将来按 TTL 清理。
    """

    __tablename__ = "rss_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_key = Column(String(255), nullable=False, unique=True, index=True)
    data = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class ToolCache(Base):
    """Persistent cache for typed tool/AKShare results.

    Payloads are trusted application-generated serialized values (including
    pandas DataFrames), so a binary column is used instead of lossy JSON.
    """

    __tablename__ = "tool_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_key = Column(String(255), nullable=False, unique=True, index=True)
    payload = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class MacroIndexDaily(Base):
    """大盘指数日线数据"""

    __tablename__ = "macro_index_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    index_code = Column(String(10), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)
    pct_chg = Column(Float)
    change_amount = Column(Float)
    data_source = Column(String(50))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("index_code", "date", name="uix_macro_index_date"),
        Index("ix_macro_index_date", "index_code", "date"),
    )

class BondYieldDaily(Base):
    """国债收益率日线数据"""

    __tablename__ = "bond_yield_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    country = Column(String(5), nullable=False, index=True)
    term = Column(String(5), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    yield_value = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("country", "term", "date", name="uix_bond_country_term_date"),
        Index("ix_bond_country_term_date", "country", "term", "date"),
    )

class MacroIndicator(Base):
    """宏观经济指标数据"""

    __tablename__ = "macro_indicator"

    id = Column(Integer, primary_key=True, autoincrement=True)
    indicator = Column(String(20), nullable=False, index=True)
    period = Column(String(20), nullable=False, index=True)
    value = Column(Float)
    yoy = Column(Float)
    mom = Column(Float)
    extra_json = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("indicator", "period", name="uix_macro_indicator_period"),
        Index("ix_macro_indicator_indicator_period", "indicator", "period"),
    )

class WatchlistGroup(Base):
    """自选股自定义分组"""

    __tablename__ = "watchlist_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False, unique=True, index=True)
    codes_json = Column(Text, nullable=False, default="[]")
    source = Column(String(16), nullable=False, default="manual")
    sort_order = Column(Integer, nullable=False, default=0, index=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        try:
            codes = json.loads(self.codes_json or "[]")
        except Exception:
            codes = []
        if not isinstance(codes, list):
            codes = []
        return {
            "id": str(self.id),
            "name": self.name,
            "codes": [str(c) for c in codes],
            "source": self.source,
            "sortOrder": self.sort_order,
        }

class AgentPromptTemplate(Base):
    """AI 助手 system prompt 模板（可在设置页配置）。"""

    __tablename__ = "agent_prompt_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    content = Column(Text, nullable=False, default="")
    is_active = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "content": self.content,
            "is_active": bool(self.is_active),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class WatchlistGroupNameConflict(Exception):
    """分组名称与已有分组冲突。"""
