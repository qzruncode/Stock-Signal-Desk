"""Data-only SQLAlchemy schema extracted from the existing provider contracts."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    Text,
    LargeBinary,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


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

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

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
    parent_net_profit_ttm = Column(Float)
    deducted_net_profit_ttm = Column(Float)
    debt_ratio = Column(Float)
    financial_fetched_at = Column(DateTime)
    report_date = Column(String(20))
    last_sync_at = Column(DateTime, default=utcnow)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

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
            "parent_net_profit_ttm": self.parent_net_profit_ttm,
            "deducted_net_profit_ttm": self.deducted_net_profit_ttm,
            "debt_ratio": self.debt_ratio,
            "financial_fetched_at": self.financial_fetched_at.isoformat()
            if self.financial_fetched_at
            else None,
            "report_date": self.report_date,
            "last_sync_at": self.last_sync_at.isoformat()
            if self.last_sync_at
            else None,
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
    fetched_at = Column(DateTime, default=utcnow, index=True)
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


class QuoteSnapshot(Base):
    """实时行情缓存快照"""

    __tablename__ = "quote_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), nullable=False, unique=True, index=True)
    data = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class KlineSnapshot(Base):
    """K线数据缓存快照"""

    __tablename__ = "kline_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), nullable=False, unique=True, index=True)
    data = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class RssCache(Base):
    """RSS feed / namespace blob 缓存（key→JSON text）。

    与 kline_snapshot 解耦：RSS 缓存键长且体积大（namespace blob ~3.3MB），
    混在 kline 表里排查困难，独立成表便于运维与将来按 TTL 清理。
    """

    __tablename__ = "rss_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_key = Column(String(255), nullable=False, unique=True, index=True)
    data = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ToolCache(Base):
    """Persistent cache for typed tool/AKShare results.

    Payloads are trusted application-generated serialized values (including
    pandas DataFrames), so a binary column is used instead of lossy JSON.
    """

    __tablename__ = "tool_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_key = Column(String(255), nullable=False, unique=True, index=True)
    payload = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


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
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

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
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

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
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("indicator", "period", name="uix_macro_indicator_period"),
        Index("ix_macro_indicator_indicator_period", "indicator", "period"),
    )
