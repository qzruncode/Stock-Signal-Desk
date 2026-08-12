# -*- coding: utf-8 -*-
"""Financials package — router + endpoint thin shells.

All heavy lifting lives in sibling sub-modules.  This file only wires
FastAPI routes, handles caching, and re-exports private helpers that
external callers (``stocks.py``, tests) access via the package namespace.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from fastapi import APIRouter, Query

from ._helpers import _normalize_symbol
from ._cache import (
    CACHE_KEY,
    VALUATION_CACHE_KEY,
    FINS_STATEMENTS_CACHE_KEY,
    NEWS_CACHE_KEY,
    ANNOUNCEMENTS_CACHE_KEY,
    RISK_EVENTS_CACHE_KEY,
    SENTIMENT_CACHE_KEY,
    RESEARCH_CACHE_KEY,
    SOCIAL_SENTIMENT_CACHE_KEY,
    _cache_get,
    _cache_put,
    _daily_cache_get,
    _daily_cache_put,
    _fins_cache_get,
    _fins_cache_put,
)

from ._fetch_financials import _fetch_financials, _fetch_from_ths, _fetch_from_sina
from ._fetch_valuation import _fetch_valuation_ratios, _build_price_overdraft_signal
from ._fetch_statements_fallback import _fetch_financial_statements
from ._fetch_sentiment import _fetch_sentiment

logger = logging.getLogger(__name__)
router = APIRouter()

# Background-refresh locks (lazily initialised, same pattern as original)
_lock = threading.Lock()
_fins_lock = threading.Lock()

# ---------------------------------------------------------------------------
# /financials
# ---------------------------------------------------------------------------


@router.get("/financials", summary="获取核心财务指标")
def get_financials(
    symbol: str = Query(..., description="股票代码，如 600519"),
    periods: int = Query(6, ge=2, le=20, description="返回最近 N 个报告期数据"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取单只股票的核心财务指标（单季度数据）。

    包含盈利能力（ROE、毛利率、净利率）、成长性（营收/利润增长率）、
    偿债能力（资产负债率、流动/速动比率）、每股指标（EPS、BPS）。

    数据源：同花顺/AKShare 核心指标与东方财富单季度财报，缓存 6 小时。
    """
    from src.tools.get_financials import get_financials as tool_get_financials

    force_value = force if isinstance(force, bool) else False
    period_value = periods if isinstance(periods, int) else 6
    return tool_get_financials(_normalize_symbol(symbol), periods=period_value, use_cache=not force_value)


# ---------------------------------------------------------------------------
# /valuation-ratios
# ---------------------------------------------------------------------------


@router.get("/valuation-ratios", summary="获取估值指标")
def get_valuation_ratios(
    symbol: str = Query(..., description="股票代码，如 600519"),
    with_history: bool = Query(True, description="是否包含历史 PE 分位数"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取当前、历史及行业相对估值，口径与 Agent 工具一致。"""
    from src.tools.get_valuation_ratios import get_valuation_ratios as tool_get_valuation_ratios

    history_value = with_history if isinstance(with_history, bool) else True
    force_value = force if isinstance(force, bool) else False
    return tool_get_valuation_ratios(
        _normalize_symbol(symbol),
        with_history=history_value,
        use_cache=not force_value,
    )


# ---------------------------------------------------------------------------
# /price-overdraft-signal
# ---------------------------------------------------------------------------


@router.get("/price-overdraft-signal", summary="获取股价透支判定信号")
def get_price_overdraft_signal(
    symbol: str = Query(..., description="股票代码，如 600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取预期校准后的股价透支判定信号。"""
    valuation = get_valuation_ratios(symbol=symbol, with_history=True, force=force)
    return {
        "symbol": valuation.get("symbol"),
        "trade_date": valuation.get("trade_date"),
        "price_overdraft_signal": valuation.get("price_overdraft_signal") or _build_price_overdraft_signal(valuation),
        "source_chain": valuation.get("source_chain", []),
        "errors": valuation.get("errors", []),
        "_fetched_at": valuation.get("_fetched_at"),
        "_cached": valuation.get("_cached", False),
    }


# ---------------------------------------------------------------------------
# /shareholder-structure
# ---------------------------------------------------------------------------


@router.get("/shareholder-structure", summary="获取股东结构")
def get_shareholder_structure(
    symbol: str = Query(..., description="股票代码，如 600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取报告期明确且与 Agent 工具同口径的股东结构。"""
    from src.tools.get_shareholder_structure import get_shareholder_structure as tool_get_shareholders

    force_value = force if isinstance(force, bool) else False
    return tool_get_shareholders(_normalize_symbol(symbol), use_cache=not force_value)


# ---------------------------------------------------------------------------
# /financials/statements
# ---------------------------------------------------------------------------


@router.get("/financials/statements", summary="获取三大财务报表")
def get_financial_statements(
    symbol: str = Query(..., description="股票代码，如 600519"),
    periods: int = Query(6, ge=2, le=20, description="返回最近 N 个报告期数据"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取单只股票的三大财务报表（单季度数据）。

    包含：
    - 资产负债表：总资产、总负债、股东权益、货币资金、应收账款、存货、固定资产、
      短期借款、长期借款、应付账款、资产负债率、权益乘数
    - 利润表：营业总收入、营业总成本、营业利润、利润总额、净利润、扣非净利润、
      基本每股收益、稀释每股收益、毛利率、净利率
    - 现金流量表：经营活动现金流净额、投资活动现金流净额、筹资活动现金流净额、
      自由现金流、经营现金流/净利润（利润含金量）

    数据源：东方财富财务分析公开接口，缓存 6 小时。
    """
    from src.tools._financial_statements import get_financial_statements as tool_get_financial_statements

    force_value = force if isinstance(force, bool) else False
    period_value = periods if isinstance(periods, int) else 6
    return tool_get_financial_statements(
        _normalize_symbol(symbol),
        periods=period_value,
        use_cache=not force_value,
    )


# ---------------------------------------------------------------------------
# /news
# ---------------------------------------------------------------------------


@router.get("/news", summary="搜索相关新闻")
def search_news(
    symbol: str = Query(..., description="股票代码 或 关键词"),
    days: int = Query(30, ge=1, le=365, description="查询最近N天的新闻"),
    source: str = Query("all", description="来源: all | eastmoney | news | research"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """搜索指定股票的相关新闻。

    返回新闻标题、摘要、发布时间、来源、链接、分类。

    数据源:
      - 东方财富个股新闻 (翻页获取，最多50条)
      - 东方财富个股研报 (stock_research_report_em)

    按天缓存。
    """
    symbol = _normalize_symbol(symbol)
    if source == "research":
        from src.tools.get_research_report import get_research_report as tool_get_research_report

        force_value = force if isinstance(force, bool) else False
        return tool_get_research_report(
            symbol,
            days=days,
            limit=50,
            use_cache=not force_value,
        )
    from src.tools.search_news import search_news as tool_search_news

    force_value = force if isinstance(force, bool) else False
    return tool_search_news(symbol, days=days, limit=50, use_cache=not force_value)


# ---------------------------------------------------------------------------
# /announcements
# ---------------------------------------------------------------------------


@router.get("/announcements", summary="获取公司公告")
def get_announcements(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(90, ge=1, le=365, description="查询最近N天"),
    type: str = Query("all", description="公告类型: all | 业绩 | 分红 | 增持 | 减持 | 高管变动"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取上市公司正式公告。

    返回公告标题、公告日期、公告类型、链接。

    数据源: RSSHub 交易所披露路由。

    按天缓存。
    """
    from src.tools.get_announcements import get_announcements as tool_get_announcements

    force_value = force if isinstance(force, bool) else False
    days_value = days if isinstance(days, int) else 90
    type_value = type if isinstance(type, str) else "all"
    return tool_get_announcements(
        _normalize_symbol(symbol),
        days=days_value,
        type=type_value,
        limit=100,
        use_cache=not force_value,
    )


# ---------------------------------------------------------------------------
# /risk-events
# ---------------------------------------------------------------------------


@router.get("/risk-events", summary="获取风险事件")
def get_risk_events(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(90, ge=1, le=365, description="查询最近N天"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
    include_structured: bool = Query(True, description="合并结构化公司风险事项"),
):
    """聚合相关新闻和公司公告中的风险事件。"""
    from src.tools.get_risk_events import get_risk_events as tool_get_risk_events

    days_value = days if isinstance(days, int) else 90
    include_value = include_structured if isinstance(include_structured, bool) else True
    force_value = force if isinstance(force, bool) else False
    return tool_get_risk_events(
        _normalize_symbol(symbol),
        days=days_value,
        limit=50,
        include_structured=include_value,
        use_cache=not force_value,
    )


# ---------------------------------------------------------------------------
# /sentiment
# ---------------------------------------------------------------------------


@router.get("/sentiment", summary="获取舆情分析证据")
def get_sentiment(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(90, ge=1, le=90, description="分析最近N天"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取供模型研判舆情的资讯和研报证据，不在程序中做词典投票。"""
    symbol = _normalize_symbol(symbol)
    days_value = days if isinstance(days, int) else 90
    force_value = force if isinstance(force, bool) else False
    cache_part = f"d{days_value}"
    if not force_value:
        cached = _daily_cache_get(SENTIMENT_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_sentiment(symbol, days_value)
    _daily_cache_put(SENTIMENT_CACHE_KEY, symbol, data, cache_part)
    return data


# ---------------------------------------------------------------------------
# /research-report
# ---------------------------------------------------------------------------


@router.get("/research-report", summary="获取券商研报")
def get_research_report(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(1095, ge=1, le=1095, description="查询最近N天"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取券商对公司的最新研究报告摘要。

    返回券商名称、评级、目标价、盈利预测、研报标题。

    数据源: RSSHub 东方财富研报 + ulapia 研报。

    按天缓存。
    """
    from src.tools.get_research_report import get_research_report as tool_get_research_report

    force_value = force if isinstance(force, bool) else False
    days_value = days if isinstance(days, int) else 1095
    return tool_get_research_report(
        _normalize_symbol(symbol),
        days=days_value,
        limit=100,
        use_cache=not force_value,
    )


# ---------------------------------------------------------------------------
# /social-sentiment
# ---------------------------------------------------------------------------


@router.get("/social-sentiment", summary="获取社交媒体分析证据")
def get_social_sentiment(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(90, ge=1, le=90, description="查询最近N天"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取公开讨论样本、热度和来源，语义情绪由模型结合上下文研判。"""
    from src.tools.get_social_sentiment import get_social_sentiment as tool_get_social_sentiment

    force_value = force if isinstance(force, bool) else False
    days_value = days if isinstance(days, int) else 90
    return tool_get_social_sentiment(
        _normalize_symbol(symbol),
        days=days_value,
        limit=100,
        max_pages=3,
        use_cache=not force_value,
    )


# ---------------------------------------------------------------------------
# Backward-compatible re-exports (external callers use getattr / direct import)
# ---------------------------------------------------------------------------

from ._helpers import (
    _fetch_rsshub_entries,
    _resolve_post_publish_time,
)
from ._fetch_statements import _fetch_from_ths_triple
