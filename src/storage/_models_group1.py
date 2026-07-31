"""SQLAlchemy model group 1."""

from __future__ import annotations

import src.storage.models as _models

for _name, _value in vars(_models).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = ['StockDaily', 'StockMeta', 'DataMaintenanceJob', 'NewsIntel', 'FundamentalSnapshot', 'AnalysisHistory', 'BuyCriteriaRecord', 'MarketMainlineReport', 'ChatConversation', 'ChatMessage']

class StockDaily(Base):
    """
    股票日线数据模型

    存储每日行情数据和计算的技术指标
    支持多股票、多日期的唯一约束
    """

    __tablename__ = "stock_daily"

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
        UniqueConstraint("code", "date", name="uix_code_date"),
        Index("ix_code_date", "code", "date"),
    )

    def __repr__(self):
        return f"<StockDaily(code={self.code}, date={self.date}, close={self.close})>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "amount": self.amount,
            "pct_chg": self.pct_chg,
            "ma5": self.ma5,
            "ma10": self.ma10,
            "ma20": self.ma20,
            "volume_ratio": self.volume_ratio,
            "data_source": self.data_source,
        }

class StockMeta(Base):
    """股票元数据模型"""

    __tablename__ = "stock_meta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, unique=True, index=True)
    name = Column(String(50), nullable=False)
    market = Column(String(10), nullable=False, index=True)
    sector = Column(String(100))
    status = Column(String(20), nullable=False, default="active", index=True)
    ipo_date = Column(Date)
    revenue_latest = Column(Float)
    net_profit_latest = Column(Float)
    operating_cf_latest = Column(Float)
    # Screening-grade trailing-twelve-month fields.  These are deliberately
    # separate from the latest cumulative report-period figures above.
    revenue_ttm = Column(Float)
    deducted_net_profit_ttm = Column(Float)
    debt_ratio = Column(Float)
    financial_fetched_at = Column(DateTime)
    report_date = Column(String(20))
    last_sync_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index("ix_stock_meta_market_status", "market", "status"),
        Index("ix_stock_meta_name", "name"),
    )

    def __repr__(self):
        return f"<StockMeta(code={self.code}, name={self.name}, market={self.market})>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "market": self.market,
            "sector": self.sector,
            "status": self.status,
            "ipo_date": self.ipo_date.isoformat() if self.ipo_date else None,
            "revenue_latest": self.revenue_latest,
            "net_profit_latest": self.net_profit_latest,
            "operating_cf_latest": self.operating_cf_latest,
            "revenue_ttm": self.revenue_ttm,
            "deducted_net_profit_ttm": self.deducted_net_profit_ttm,
            "debt_ratio": self.debt_ratio,
            "financial_fetched_at": self.financial_fetched_at.isoformat() if self.financial_fetched_at else None,
            "report_date": self.report_date,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
        }

class DataMaintenanceJob(Base):
    """Persistent audit record for automatic dataset maintenance."""

    __tablename__ = "data_maintenance_jobs"

    id = Column(String(36), primary_key=True)
    dataset = Column(String(32), nullable=False, index=True)
    scope_key = Column(String(128), nullable=False, default="all", index=True)
    target_data_time = Column(String(32), nullable=False, default="latest")
    trigger = Column(String(32), nullable=False, default="agent")
    status = Column(String(16), nullable=False, default="queued", index=True)
    progress = Column(Integer, nullable=False, default=0)
    total = Column(Integer, nullable=False, default=0)
    message = Column(Text)
    error = Column(Text)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index("ix_maintenance_dataset_status", "dataset", "status"),
        UniqueConstraint(
            "dataset",
            "scope_key",
            "target_data_time",
            name="uix_maintenance_dataset_scope_target",
        ),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "dataset": self.dataset,
            "scope_key": self.scope_key,
            "target_data_time": self.target_data_time,
            "trigger": self.trigger,
            "status": self.status,
            "progress": self.progress,
            "total": self.total,
            "message": self.message,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

class NewsIntel(Base):
    """新闻情报数据模型"""

    __tablename__ = "news_intel"

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
        UniqueConstraint("url", name="uix_news_url"),
        Index("ix_news_code_pub", "code", "published_date"),
    )

    def __repr__(self) -> str:
        return f"<NewsIntel(code={self.code}, title={self.title[:20]}...)>"

class FundamentalSnapshot(Base):
    """基本面上下文快照（P0 write-only）。"""

    __tablename__ = "fundamental_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    query_id = Column(String(64), nullable=False, index=True)
    code = Column(String(10), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    source_chain = Column(Text)
    coverage = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index("ix_fundamental_snapshot_query_code", "query_id", "code"),
        Index("ix_fundamental_snapshot_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<FundamentalSnapshot(query_id={self.query_id}, code={self.code})>"

class AnalysisHistory(Base):
    """分析结果历史记录模型"""

    __tablename__ = "analysis_history"

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

    __table_args__ = (Index("ix_analysis_code_time", "code", "created_at"),)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "query_id": self.query_id,
            "code": self.code,
            "name": self.name,
            "report_type": self.report_type,
            "sentiment_score": self.sentiment_score,
            "operation_advice": self.operation_advice,
            "trend_prediction": self.trend_prediction,
            "analysis_summary": self.analysis_summary,
            "raw_result": self.raw_result,
            "news_content": self.news_content,
            "context_snapshot": self.context_snapshot,
            "ideal_buy": self.ideal_buy,
            "secondary_buy": self.secondary_buy,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

class BuyCriteriaRecord(Base):
    """买入判断分析结果记录"""

    __tablename__ = "buy_criteria_records"

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
        UniqueConstraint("symbol", "trade_date", name="uq_buy_criteria_symbol_date"),
        Index("ix_buy_criteria_date", "trade_date"),
    )

    def to_dict(self) -> Dict[str, Any]:
        import json as _json

        return {
            "id": self.id,
            "symbol": self.symbol,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "stock_name": self.stock_name,
            "final_decision": self.final_decision,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "not_evaluated_count": self.not_evaluated_count,
            "stopped_at": self.stopped_at,
            "summary": self.summary,
            "results": _json.loads(self.results_json),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

class MarketMainlineReport(Base):
    """市场主线结构化研判报告。"""

    __tablename__ = "market_mainline_report"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_key = Column(String(64), nullable=False, index=True)
    as_of_date = Column(String(16), nullable=False, index=True)
    mode = Column(String(16), nullable=False, default="llm", index=True)
    model_used = Column(String(128))
    overview = Column(Text)
    market_stage_label = Column(String(64))
    market_stage_description = Column(Text)
    raw_response = Column(Text)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index("ix_market_mainline_report_key_created", "report_key", "created_at"),
        Index("ix_market_mainline_report_mode_date", "mode", "as_of_date"),
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
        payload.setdefault(
            "market_stage",
            {
                "label": self.market_stage_label,
                "description": self.market_stage_description,
            },
        )
        payload.setdefault("raw_response", self.raw_response)
        payload.setdefault("raw_stream_output", self.raw_response or payload.get("full_report"))
        payload.setdefault("created_at", self.created_at.isoformat() if self.created_at else None)
        return payload

class ChatConversation(Base):
    """AI 对话会话元数据。"""

    __tablename__ = "chat_conversations"

    id = Column(String(64), primary_key=True)
    # Ownership is stored on the durable boundary instead of being inferred
    # from a browser cookie.  The current local deployment uses one
    # tenant/owner, while hosted deployments can bind these fields to their
    # authenticated principal without changing the conversation schema.
    tenant_id = Column(String(64), nullable=False, default="local", index=True)
    owner_id = Column(String(128), nullable=False, default="admin", index=True)
    title = Column(String(120), nullable=False, default="新对话")
    title_source = Column(String(16), nullable=False, default="auto", index=True)
    preview_text = Column(String(200))
    thread_state_json = Column(Text)
    agent_context_json = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (Index("ix_chat_conversations_updated", "updated_at"),)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "tenant_id": self.tenant_id,
            "owner_id": self.owner_id,
            "title_source": self.title_source,
            "preview_text": self.preview_text,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class ChatMessage(Base):
    """AI 对话消息内容。"""

    __tablename__ = "chat_messages"

    id = Column(String(64), primary_key=True)
    conversation_id = Column(String(64), ForeignKey("chat_conversations.id"), nullable=False, index=True)
    role = Column(String(16), nullable=False, index=True)
    content = Column(Text, nullable=False, default="")
    sequence = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uix_chat_message_conversation_sequence"),
        Index("ix_chat_messages_conversation_sequence", "conversation_id", "sequence"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "sequence": self.sequence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
