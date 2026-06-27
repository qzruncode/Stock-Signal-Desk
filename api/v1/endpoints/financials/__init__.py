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
    SHAREHOLDER_CACHE_KEY,
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
from ._fetch_shareholders import _fetch_shareholder_structure
from ._fetch_statements_fallback import _fetch_financial_statements
from ._fetch_news import _fetch_news, _fetch_rss_stock_news, _fetch_direct_news_sources, _fetch_direct_sentiment_sources
from ._fetch_announcements import _fetch_announcements, _fetch_rss_announcements
from ._fetch_risk_events import _build_risk_events
from ._fetch_sentiment import _fetch_sentiment
from ._fetch_research import _fetch_research_reports, _fetch_rss_research_reports
from ._fetch_social_sentiment import _fetch_social_sentiment

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
    periods: int = Query(12, ge=1, le=40, description="返回最近 N 个报告期数据（默认12=3年）"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取单只股票的核心财务指标（单季度数据）。

    包含盈利能力（ROE、毛利率、净利率）、成长性（营收/利润增长率）、
    偿债能力（资产负债率、流动/速动比率）、每股指标（EPS、BPS）。

    数据源（按优先级）：
    1. 同花顺 (stock_financial_abstract_ths)
    2. 新浪财经 (stock_financial_abstract)

    按天缓存。
    """
    symbol = _normalize_symbol(symbol)

    if not force:
        cached = _cache_get(symbol)
        if cached:
            cached['_cached'] = True

            if _lock.acquire(blocking=False):
                def _bg_refresh():
                    try:
                        _cache_put(symbol, _fetch_financials(symbol, periods))
                    finally:
                        _lock.release()
                threading.Thread(target=_bg_refresh, daemon=True).start()

            return cached

    data = _fetch_financials(symbol, periods)
    _cache_put(symbol, data)
    return data


# ---------------------------------------------------------------------------
# /valuation-ratios
# ---------------------------------------------------------------------------

@router.get("/valuation-ratios", summary="获取估值指标")
def get_valuation_ratios(
    symbol: str = Query(..., description="股票代码，如 600519"),
    with_history: bool = Query(True, description="是否包含历史 PE 分位数"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取当前及历史估值指标。"""
    symbol = _normalize_symbol(symbol)
    cache_part = "hist" if with_history else "latest"
    if not force:
        cached = _daily_cache_get(VALUATION_CACHE_KEY, symbol, cache_part)
        if cached:
            if not cached.get("price_overdraft_signal"):
                cached["price_overdraft_signal"] = _build_price_overdraft_signal(cached)
            cached["_cached"] = True
            return cached
    data = _fetch_valuation_ratios(symbol, with_history=with_history)
    _daily_cache_put(VALUATION_CACHE_KEY, symbol, data, cache_part)
    return data


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
    """获取股东人数、前十大股东、机构持股、重要股东增减持与实际控制人。"""
    symbol = _normalize_symbol(symbol)
    if not force:
        cached = _daily_cache_get(SHAREHOLDER_CACHE_KEY, symbol)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_shareholder_structure(symbol)
    _daily_cache_put(SHAREHOLDER_CACHE_KEY, symbol, data)
    return data


# ---------------------------------------------------------------------------
# /financials/statements
# ---------------------------------------------------------------------------

@router.get("/financials/statements", summary="获取三大财务报表")
def get_financial_statements(
    symbol: str = Query(..., description="股票代码，如 600519"),
    periods: int = Query(12, ge=4, le=40, description="返回最近 N 个报告期数据（默认12=3年单季度）"),
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

    数据源：东方财富 (stock_*_by_report_em)，按天缓存。
    """
    symbol = _normalize_symbol(symbol)

    if not force:
        cached = _fins_cache_get(symbol, periods)
        if cached:
            cached['_cached'] = True

            # Background refresh — keep cache warm, same pattern as stock_info and financials
            if _fins_lock.acquire(blocking=False):
                def _bg_refresh():
                    try:
                        _fins_cache_put(symbol, periods, _fetch_financial_statements(symbol, periods))
                    finally:
                        _fins_lock.release()
                threading.Thread(target=_bg_refresh, daemon=True).start()

            return cached

    data = _fetch_financial_statements(symbol, periods)
    _fins_cache_put(symbol, periods, data)
    return data


# ---------------------------------------------------------------------------
# /news
# ---------------------------------------------------------------------------

@router.get("/news", summary="搜索相关新闻")
def search_news(
    symbol: str = Query(..., description="股票代码 或 关键词"),
    days: int = Query(90, ge=1, le=90, description="查询最近N天的新闻"),
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
    cache_part = f"d{days}:{source}"
    if not force:
        cached = _daily_cache_get(NEWS_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_news(symbol, days, source)
    _daily_cache_put(NEWS_CACHE_KEY, symbol, data, cache_part)
    return data


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
    symbol = _normalize_symbol(symbol)
    cache_part = f"d{days}:{type}"
    if not force:
        cached = _daily_cache_get(ANNOUNCEMENTS_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_announcements(symbol, days, type)
    _daily_cache_put(ANNOUNCEMENTS_CACHE_KEY, symbol, data, cache_part)
    return data


# ---------------------------------------------------------------------------
# /risk-events
# ---------------------------------------------------------------------------

@router.get("/risk-events", summary="获取风险事件")
def get_risk_events(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(90, ge=1, le=365, description="查询最近N天"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """聚合相关新闻和公司公告中的风险事件。"""
    symbol = _normalize_symbol(symbol)
    cache_part = f"d{days}"
    if not force:
        cached = _daily_cache_get(RISK_EVENTS_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _build_risk_events(symbol, days)
    _daily_cache_put(RISK_EVENTS_CACHE_KEY, symbol, data, cache_part)
    return data


# ---------------------------------------------------------------------------
# /sentiment
# ---------------------------------------------------------------------------

@router.get("/sentiment", summary="获取舆情情绪")
def get_sentiment(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(90, ge=1, le=90, description="分析最近N天"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """分析市场对某股票的情绪倾向。

    返回舆情分数（-100到+100）、正/负/中性新闻条数、
    讨论热度趋势、关键词、逐条情绪标注。

    数据源: RSSHub 聚合财经资讯 + 个股研报
    方法: 中文分词 + 金融情绪词典匹配

    按天缓存。
    """
    symbol = _normalize_symbol(symbol)
    cache_part = f"d{days}"
    if not force:
        cached = _daily_cache_get(SENTIMENT_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_sentiment(symbol, days)
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
    symbol = _normalize_symbol(symbol)
    cache_part = f"d{days}"
    if not force:
        cached = _daily_cache_get(RESEARCH_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_research_reports(symbol, days)
    _daily_cache_put(RESEARCH_CACHE_KEY, symbol, data, cache_part)
    return data


# ---------------------------------------------------------------------------
# /social-sentiment
# ---------------------------------------------------------------------------

@router.get("/social-sentiment", summary="获取社交媒体情绪")
def get_social_sentiment(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(90, ge=1, le=90, description="查询最近N天"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取社交媒体讨论热度和情绪。

    返回:
      - 舆情分数 (-100~+100)
      - 讨论帖子数量/日趋势
      - 千股千评评分趋势
      - 正/负/中性比例
      - 逐条帖子

    数据源: RSSHub 东方财富关键词搜索。

    按天缓存。
    """
    symbol = _normalize_symbol(symbol)
    cache_part = f"d{days}"
    if not force:
        cached = _daily_cache_get(SOCIAL_SENTIMENT_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_social_sentiment(symbol, days)
    _daily_cache_put(SOCIAL_SENTIMENT_CACHE_KEY, symbol, data, cache_part)
    return data


# ---------------------------------------------------------------------------
# Backward-compatible re-exports (external callers use getattr / direct import)
# ---------------------------------------------------------------------------

from ._helpers import (
    _fetch_rsshub_entries,
    _build_structured_analysis,
    _resolve_post_publish_time,
)
from ._fetch_statements import _fetch_from_ths_triple
from ._fetch_risk_events import (
    _classify_risk_event,
    _extract_risk_summary,
    _match_risk_keywords,
)
