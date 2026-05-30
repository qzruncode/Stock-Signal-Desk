# -*- coding: utf-8 -*-
"""Real-time stock quote endpoints."""

from __future__ import annotations

import logging
from typing import Optional, List

from fastapi import APIRouter, Query

from data_provider.akshare_fetcher import AkshareFetcher

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/realtime",
    summary="Get real-time stock quotes",
)
def get_realtime_quotes(
    symbol: str = Query(default="", description="股票代码（如 600519）"),
    symbols: Optional[List[str]] = Query(default=None, description="股票代码列表"),
    market: str = Query(default="A股", description="市场（A股 / 港股 / 美股）"),
):
    """获取股票实时行情数据。

    支持单只或多只股票查询。
    A 股使用 akshare.stock_zh_a_spot_em() 全量拉取后过滤。
    """
    symbols_list = symbols or []
    if symbol and symbol not in symbols_list:
        symbols_list.insert(0, symbol)

    if not symbols_list:
        return {"items": [], "total": 0}

    fetcher = AkshareFetcher()
    results = []

    for sym in symbols_list:
        quote = fetcher.get_realtime_quote(sym)
        if quote and quote.has_basic_data():
            results.append(quote.to_dict())

    return {
        "items": results,
        "total": len(results),
    }
