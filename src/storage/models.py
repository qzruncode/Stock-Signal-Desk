# -*- coding: utf-8 -*-
"""SQLAlchemy ORM model definitions for the A-share stock analysis system."""

import json
import logging
from datetime import datetime, date
from typing import Any, Dict, Optional, TYPE_CHECKING

from sqlalchemy import (
    Column, String, Float, Boolean, Date, DateTime, Integer,
    ForeignKey, Index, UniqueConstraint, Text,
)
from sqlalchemy.orm import declarative_base

if TYPE_CHECKING:
    from src.search_service import SearchResponse

logger = logging.getLogger(__name__)

Base = declarative_base()


class StockDaily(Base):
    """
    股票日线数据模型

    存储每日行情数据和计算的技术指标
    支持多股票、多日期的唯一约束
    """
    __tablename__ = 'stock_daily'

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)

    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)

    volume = Column(Float)
    amount = Column(Float)
    pct_chg = Column(Float)

    ma5 = Column(Float)
    ma10 = Column(Float)
    ma20 = Column(Float)
    volume_ratio = Column(Float)

    data_source = Column(String(50))

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint('code', 'date', name='uix_code_date'),
        Index('ix_code_date', 'code', 'date'),
    )

    def __repr__(self):
        return f"<StockDaily(code={self.code}, date={self.date}, close={self.close})>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'date': self.date,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'amount': self.amount,
            'pct_chg': self.pct_chg,
            'ma5': self.ma5,
            'ma10': self.ma10,
            'ma20': self.ma20,
            'volume_ratio': self.volume_ratio,
            'data_source': self.data_source,
        }


class StockMeta(Base):
    """股票元数据模型"""
    __tablename__ = 'stock_meta'

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, unique=True, index=True)
    name = Column(String(50), nullable=False)
    market = Column(String(10), nullable=False, index=True)
    sector = Column(String(100))
    status = Column(String(20), nullable=False, default='active', index=True)
    ipo_date = Column(Date)
    revenue_latest = Column(Float)
    net_profit_latest = Column(Float)
    operating_cf_latest = Column(Float)
    debt_ratio = Column(Float)
    financial_fetched_at = Column(DateTime)
    report_date = Column(String(20))
    last_sync_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index('ix_stock_meta_market_status', 'market', 'status'),
        Index('ix_stock_meta_name', 'name'),
    )

    def __repr__(self):
        return f"<StockMeta(code={self.code}, name={self.name}, market={self.market})>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code, 'name': self.name, 'market': self.market,
            'sector': self.sector, 'status': self.status,
            'ipo_date': self.ipo_date.isoformat() if self.ipo_date else None,
            'revenue_latest': self.revenue_latest,
            'net_profit_latest': self.net_profit_latest,
            'operating_cf_latest': self.operating_cf_latest,
            'debt_ratio': self.debt_ratio,
            'financial_fetched_at': self.financial_fetched_at.isoformat() if self.financial_fetched_at else None,
            'report_date': self.report_date,
            'last_sync_at': self.last_sync_at.isoformat() if self.last_sync_at else None,
        }


class NewsIntel(Base):
    """新闻情报数据模型"""
    __tablename__ = 'news_intel'

    id = Column(Integer, primary_key=True, autoincrement=True)
    query_id = Column(String(64), index=True)
    code = Column(String(10), nullable=False, index=True)
    name = Column(String(50))
    dimension = Column(String(32), index=True)
    query = Column(String(255))
    provider = Column(String(32), index=True)
    title = Column(String(300), nullable=False)
    snippet = Column(Text)
    url = Column(String(1000), nullable=False)
    source = Column(String(100))
    published_date = Column(DateTime, index=True)
    fetched_at = Column(DateTime, default=datetime.now, index=True)
    query_source = Column(String(32), index=True)
    requester_platform = Column(String(20))
    requester_user_id = Column(String(64))
    requester_user_name = Column(String(64))
    requester_chat_id = Column(String(64))
    requester_message_id = Column(String(64))
    requester_query = Column(String(255))

    __table_args__ = (
        UniqueConstraint('url', name='uix_news_url'),
        Index('ix_news_code_pub', 'code', 'published_date'),
    )

    def __repr__(self) -> str:
        return f"<NewsIntel(code={self.code}, title={self.title[:20]}...)>"


class FundamentalSnapshot(Base):
    """基本面上下文快照（P0 write-only）。"""
    __tablename__ = 'fundamental_snapshot'

    id = Column(Integer, primary_key=True, autoincrement=True)
    query_id = Column(String(64), nullable=False, index=True)
    code = Column(String(10), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    source_chain = Column(Text)
    coverage = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('ix_fundamental_snapshot_query_code', 'query_id', 'code'),
        Index('ix_fundamental_snapshot_created', 'created_at'),
    )

    def __repr__(self) -> str:
        return f"<FundamentalSnapshot(query_id={self.query_id}, code={self.code})>"


class AnalysisHistory(Base):
    """分析结果历史记录模型"""
    __tablename__ = 'analysis_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    query_id = Column(String(64), index=True)
    code = Column(String(10), nullable=False, index=True)
    name = Column(String(50))
    report_type = Column(String(16), index=True)
    sentiment_score = Column(Integer)
    operation_advice = Column(String(20))
    trend_prediction = Column(String(50))
    analysis_summary = Column(Text)
    raw_result = Column(Text)
    news_content = Column(Text)
    context_snapshot = Column(Text)
    ideal_buy = Column(Float)
    secondary_buy = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('ix_analysis_code_time', 'code', 'created_at'),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id, 'query_id': self.query_id,
            'code': self.code, 'name': self.name, 'report_type': self.report_type,
            'sentiment_score': self.sentiment_score,
            'operation_advice': self.operation_advice,
            'trend_prediction': self.trend_prediction,
            'analysis_summary': self.analysis_summary,
            'raw_result': self.raw_result, 'news_content': self.news_content,
            'context_snapshot': self.context_snapshot,
            'ideal_buy': self.ideal_buy, 'secondary_buy': self.secondary_buy,
            'stop_loss': self.stop_loss, 'take_profit': self.take_profit,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class BuyCriteriaRecord(Base):
    """买入判断分析结果记录"""
    __tablename__ = 'buy_criteria_records'

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    stock_name = Column(String(50))
    final_decision = Column(String(10), nullable=False)
    passed_count = Column(Integer)
    failed_count = Column(Integer)
    not_evaluated_count = Column(Integer)
    stopped_at = Column(String(30))
    summary = Column(Text)
    results_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        UniqueConstraint('symbol', 'trade_date', name='uq_buy_criteria_symbol_date'),
        Index('ix_buy_criteria_date', 'trade_date'),
    )

    def to_dict(self) -> Dict[str, Any]:
        import json as _json
        return {
            'id': self.id, 'symbol': self.symbol,
            'trade_date': self.trade_date.isoformat() if self.trade_date else None,
            'stock_name': self.stock_name,
            'final_decision': self.final_decision,
            'passed_count': self.passed_count,
            'failed_count': self.failed_count,
            'not_evaluated_count': self.not_evaluated_count,
            'stopped_at': self.stopped_at, 'summary': self.summary,
            'results': _json.loads(self.results_json),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class MarketMainlineReport(Base):
    """市场主线结构化研判报告。"""
    __tablename__ = 'market_mainline_report'

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_key = Column(String(64), nullable=False, index=True)
    as_of_date = Column(String(16), nullable=False, index=True)
    mode = Column(String(16), nullable=False, default='llm', index=True)
    model_used = Column(String(128))
    overview = Column(Text)
    market_stage_label = Column(String(64))
    market_stage_description = Column(Text)
    raw_response = Column(Text)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('ix_market_mainline_report_key_created', 'report_key', 'created_at'),
        Index('ix_market_mainline_report_mode_date', 'mode', 'as_of_date'),
    )

    def to_dict(self) -> Dict[str, Any]:
        try:
            payload = json.loads(self.payload or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if not payload.get("full_report") and self.raw_response:
            try:
                raw_payload = json.loads(self.raw_response)
                if isinstance(raw_payload, dict):
                    payload.setdefault("full_report", raw_payload.get("full_report"))
                    payload.setdefault("overview", raw_payload.get("overview"))
                    payload.setdefault("current_mainlines", raw_payload.get("current_mainlines"))
                    payload.setdefault("future_mainlines", raw_payload.get("future_mainlines"))
                    payload.setdefault("action_summary", raw_payload.get("action_summary"))
                    payload.setdefault("evidence_digest", raw_payload.get("evidence_digest"))
                    payload.setdefault("debug_input", raw_payload.get("debug_input"))
            except Exception:
                logger.debug("payload 迁移失败", exc_info=True)
        payload.setdefault("id", self.id)
        payload.setdefault("report_key", self.report_key)
        payload.setdefault("as_of_date", self.as_of_date)
        payload.setdefault("mode", self.mode)
        payload.setdefault("model_used", self.model_used)
        payload.setdefault("overview", self.overview)
        payload.setdefault("market_stage", {
            "label": self.market_stage_label,
            "description": self.market_stage_description,
        })
        payload.setdefault("raw_response", self.raw_response)
        payload.setdefault("raw_stream_output", self.raw_response or payload.get("full_report"))
        payload.setdefault("created_at", self.created_at.isoformat() if self.created_at else None)
        return payload


class ChatConversation(Base):
    """AI 对话会话元数据。"""
    __tablename__ = 'chat_conversations'

    id = Column(String(64), primary_key=True)
    title = Column(String(120), nullable=False, default='新对话')
    title_source = Column(String(16), nullable=False, default='auto', index=True)
    preview_text = Column(String(200))
    thread_state_json = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (Index('ix_chat_conversations_updated', 'updated_at'),)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id, 'title': self.title,
            'title_source': self.title_source,
            'preview_text': self.preview_text,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class ChatMessage(Base):
    """AI 对话消息内容。"""
    __tablename__ = 'chat_messages'

    id = Column(String(64), primary_key=True)
    conversation_id = Column(String(64), ForeignKey('chat_conversations.id'), nullable=False, index=True)
    role = Column(String(16), nullable=False, index=True)
    content = Column(Text, nullable=False, default='')
    sequence = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        UniqueConstraint('conversation_id', 'sequence', name='uix_chat_message_conversation_sequence'),
        Index('ix_chat_messages_conversation_sequence', 'conversation_id', 'sequence'),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id, 'conversation_id': self.conversation_id,
            'role': self.role, 'content': self.content,
            'sequence': self.sequence,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class BacktestResult(Base):
    """单条分析记录的回测结果。"""
    __tablename__ = 'backtest_results'

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_history_id = Column(Integer, ForeignKey('analysis_history.id'), nullable=False, index=True)
    code = Column(String(10), nullable=False, index=True)
    analysis_date = Column(Date, index=True)
    eval_window_days = Column(Integer, nullable=False, default=10)
    engine_version = Column(String(16), nullable=False, default='v1')
    eval_status = Column(String(16), nullable=False, default='pending')
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
        UniqueConstraint('analysis_history_id', 'eval_window_days', 'engine_version', name='uix_backtest_analysis_window_version'),
        Index('ix_backtest_code_date', 'code', 'analysis_date'),
    )


class BacktestSummary(Base):
    """回测汇总指标。"""
    __tablename__ = 'backtest_summaries'

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(16), nullable=False, index=True)
    code = Column(String(16), index=True)
    eval_window_days = Column(Integer, nullable=False, default=10)
    engine_version = Column(String(16), nullable=False, default='v1')
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
        UniqueConstraint('scope', 'code', 'eval_window_days', 'engine_version', name='uix_backtest_summary_scope_code_window_version'),
    )


class PortfolioAccount(Base):
    """Portfolio account metadata."""
    __tablename__ = 'portfolio_accounts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String(64), index=True)
    name = Column(String(64), nullable=False)
    broker = Column(String(64))
    market = Column(String(8), nullable=False, default='cn', index=True)
    base_currency = Column(String(8), nullable=False, default='CNY')
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (Index('ix_portfolio_account_owner_active', 'owner_id', 'is_active'),)


class PortfolioTrade(Base):
    """Executed trade events."""
    __tablename__ = 'portfolio_trades'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    trade_uid = Column(String(128))
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
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
        UniqueConstraint('account_id', 'trade_uid', name='uix_portfolio_trade_uid'),
        UniqueConstraint('account_id', 'dedup_hash', name='uix_portfolio_trade_dedup_hash'),
        Index('ix_portfolio_trade_account_date', 'account_id', 'trade_date'),
    )


class PortfolioCashLedger(Base):
    """Cash in/out events."""
    __tablename__ = 'portfolio_cash_ledger'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    event_date = Column(Date, nullable=False, index=True)
    direction = Column(String(8), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default='CNY')
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (Index('ix_portfolio_cash_account_date', 'account_id', 'event_date'),)


class PortfolioCorporateAction(Base):
    """Corporate actions."""
    __tablename__ = 'portfolio_corporate_actions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
    effective_date = Column(Date, nullable=False, index=True)
    action_type = Column(String(24), nullable=False)
    cash_dividend_per_share = Column(Float)
    split_ratio = Column(Float)
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (Index('ix_portfolio_ca_account_date', 'account_id', 'effective_date'),)


class PortfolioPosition(Base):
    """Latest replayed position snapshot."""
    __tablename__ = 'portfolio_positions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default='fifo')
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
    quantity = Column(Float, nullable=False, default=0.0)
    avg_cost = Column(Float, nullable=False, default=0.0)
    total_cost = Column(Float, nullable=False, default=0.0)
    last_price = Column(Float, nullable=False, default=0.0)
    market_value_base = Column(Float, nullable=False, default=0.0)
    unrealized_pnl_base = Column(Float, nullable=False, default=0.0)
    valuation_currency = Column(String(8), nullable=False, default='CNY')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (
        UniqueConstraint('account_id', 'symbol', 'market', 'currency', 'cost_method',
                         name='uix_portfolio_position_account_symbol_market_currency'),
    )


class PortfolioPositionLot(Base):
    """Lot-level remaining quantities."""
    __tablename__ = 'portfolio_position_lots'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default='fifo')
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
    open_date = Column(Date, nullable=False, index=True)
    remaining_quantity = Column(Float, nullable=False, default=0.0)
    unit_cost = Column(Float, nullable=False, default=0.0)
    source_trade_id = Column(Integer, ForeignKey('portfolio_trades.id'))
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (Index('ix_portfolio_lot_account_symbol', 'account_id', 'symbol'),)


class PortfolioDailySnapshot(Base):
    """Daily account snapshot."""
    __tablename__ = 'portfolio_daily_snapshots'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default='fifo')
    base_currency = Column(String(8), nullable=False, default='CNY')
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
        UniqueConstraint('account_id', 'snapshot_date', 'cost_method',
                         name='uix_portfolio_snapshot_account_date_method'),
    )


class PortfolioFxRate(Base):
    """Cached FX rates."""
    __tablename__ = 'portfolio_fx_rates'

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_currency = Column(String(8), nullable=False, index=True)
    to_currency = Column(String(8), nullable=False, index=True)
    rate_date = Column(Date, nullable=False, index=True)
    rate = Column(Float, nullable=False)
    source = Column(String(32), nullable=False, default='manual')
    is_stale = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint('from_currency', 'to_currency', 'rate_date',
                         name='uix_portfolio_fx_pair_date'),
    )


class ConversationMessage(Base):
    """Agent 对话历史记录表"""
    __tablename__ = 'conversation_messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), index=True, nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, index=True)


class LLMUsage(Base):
    """Token-usage audit log."""
    __tablename__ = 'llm_usage'

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
    __tablename__ = 'alert_rules'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False)
    target_scope = Column(String(32), nullable=False, default='single_symbol', index=True)
    target = Column(String(64), nullable=False, index=True)
    alert_type = Column(String(32), nullable=False, index=True)
    parameters = Column(Text, nullable=False, default='{}')
    severity = Column(String(16), nullable=False, default='warning', index=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    source = Column(String(16), nullable=False, default='api', index=True)
    cooldown_policy = Column(Text)
    notification_policy = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (Index('ix_alert_rule_type_target', 'alert_type', 'target'),)


class AlertTriggerRecord(Base):
    """Alert trigger history."""
    __tablename__ = 'alert_triggers'

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, index=True)
    target = Column(String(64), nullable=False, index=True)
    observed_value = Column(Float)
    threshold = Column(Float)
    reason = Column(Text)
    data_source = Column(String(64))
    data_timestamp = Column(DateTime, index=True)
    triggered_at = Column(DateTime, default=datetime.now, index=True)
    status = Column(String(16), nullable=False, default='triggered', index=True)
    diagnostics = Column(Text)

    __table_args__ = (Index('ix_alert_trigger_rule_time', 'rule_id', 'triggered_at'),)


class AlertNotificationRecord(Base):
    """Notification attempt for alert triggers."""
    __tablename__ = 'alert_notifications'

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

    __table_args__ = (Index('ix_alert_notification_trigger_channel', 'trigger_id', 'channel'),)


class BatchRun(Base):
    """批量跑批记录"""
    __tablename__ = 'batch_runs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, unique=True, index=True)
    triggered_by = Column(String(32), nullable=False, default='manual')
    template_id = Column(String(64))
    template_name = Column(String(100))
    stock_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    fail_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime)
    report_path = Column(Text)
    results_json = Column(Text, default='[]')
    stock_codes_json = Column(Text, default='[]')
    status = Column(String(32), nullable=False, default='completed')
    analysis_mode = Column(String(32), default='template')

    __table_args__ = (Index('ix_batch_runs_started', 'started_at'),)


class BatchSchedule(Base):
    """批量跑批定时配置"""
    __tablename__ = 'batch_schedules'

    id = Column(Integer, primary_key=True, autoincrement=True)
    enabled = Column(Boolean, nullable=False, default=False)
    times_json = Column(Text, nullable=False, default='[]')
    template_id = Column(String(64))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class QuoteSnapshot(Base):
    """实时行情缓存快照"""
    __tablename__ = 'quote_snapshot'

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), nullable=False, unique=True, index=True)
    data = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class KlineSnapshot(Base):
    """K线数据缓存快照"""
    __tablename__ = 'kline_snapshot'

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), nullable=False, unique=True, index=True)
    data = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class MacroIndexDaily(Base):
    """大盘指数日线数据"""
    __tablename__ = 'macro_index_daily'

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
        UniqueConstraint('index_code', 'date', name='uix_macro_index_date'),
        Index('ix_macro_index_date', 'index_code', 'date'),
    )


class BondYieldDaily(Base):
    """国债收益率日线数据"""
    __tablename__ = 'bond_yield_daily'

    id = Column(Integer, primary_key=True, autoincrement=True)
    country = Column(String(5), nullable=False, index=True)
    term = Column(String(5), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    yield_value = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint('country', 'term', 'date', name='uix_bond_country_term_date'),
        Index('ix_bond_country_term_date', 'country', 'term', 'date'),
    )


class MacroIndicator(Base):
    """宏观经济指标数据"""
    __tablename__ = 'macro_indicator'

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
        UniqueConstraint('indicator', 'period', name='uix_macro_indicator_period'),
        Index('ix_macro_indicator_indicator_period', 'indicator', 'period'),
    )


class WatchlistGroup(Base):
    """自选股自定义分组"""
    __tablename__ = 'watchlist_groups'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False, unique=True, index=True)
    codes_json = Column(Text, nullable=False, default='[]')
    source = Column(String(16), nullable=False, default='manual')
    sort_order = Column(Integer, nullable=False, default=0, index=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        try:
            codes = json.loads(self.codes_json or '[]')
        except Exception:
            codes = []
        if not isinstance(codes, list):
            codes = []
        return {
            'id': str(self.id),
            'name': self.name,
            'codes': [str(c) for c in codes],
            'source': self.source,
            'sortOrder': self.sort_order,
        }


class AgentPromptTemplate(Base):
    """AI 助手 system prompt 模板（可在设置页配置）。"""
    __tablename__ = 'agent_prompt_templates'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    content = Column(Text, nullable=False, default='')
    is_active = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'content': self.content,
            'is_active': bool(self.is_active),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class WatchlistGroupNameConflict(Exception):
    """分组名称与已有分组冲突。"""


