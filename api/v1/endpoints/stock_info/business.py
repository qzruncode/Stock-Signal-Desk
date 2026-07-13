# -*- coding: utf-8 -*-
"""Stock business analysis endpoint — routing and orchestration only."""

from __future__ import annotations

from fastapi import Query

from api.v1.endpoints.stock_info import router
from api.v1.endpoints.stock_info.profile import _normalize_symbol
from api.v1.endpoints.stock_info._cache import _business_cache_get, _business_cache_put
from api.v1.endpoints.stock_info._data import (
    _fetch_business_intro,
    _fetch_business_composition,
    _fetch_profit_forecast,
    _fetch_financial_summary,
    _fetch_recent_events,
)
from api.v1.endpoints.stock_info._llm_parse import (
    _generate_llm_business_analysis,
)


@router.get("/business", summary="获取个股业务分析数据")
def get_stock_business(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """Get stock business analysis with LLM insights. Returns cached data if available."""
    normalized = _normalize_symbol(symbol)

    if not force:
        cached = _business_cache_get(normalized)
        if cached:
            return cached

    intro = _fetch_business_intro(normalized)
    composition = _fetch_business_composition(normalized)
    profit_forecast = _fetch_profit_forecast(normalized)
    financial_summary = _fetch_financial_summary(normalized)
    events = _fetch_recent_events(normalized)

    result = _generate_llm_business_analysis(
        symbol=normalized,
        intro=intro,
        composition=composition,
        profit_forecast=profit_forecast,
        financial_summary=financial_summary,
        events=events,
    )

    _business_cache_put(normalized, result)
    return result