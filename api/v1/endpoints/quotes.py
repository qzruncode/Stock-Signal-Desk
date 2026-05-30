# -*- coding: utf-8 -*-
"""Real-time stock quote endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from data_provider.akshare_fetcher import AkshareFetcher

logger = logging.getLogger(__name__)

router = APIRouter()

_fetcher: AkshareFetcher | None = None


def _get_fetcher() -> AkshareFetcher:
    global _fetcher
    if _fetcher is None:
        from src.config import get_config
        from src.patches.eastmoney_patch import eastmoney_patch
        if get_config().enable_eastmoney_patch:
            eastmoney_patch()
        _fetcher = AkshareFetcher()
    return _fetcher


@router.get(
    "/realtime",
    summary="Get real-time stock quotes",
)
def get_realtime_quotes(
    symbol: str = Query(default="", description="股票代码（如 600519）"),
    symbols: list[str] | None = Query(default=None, description="股票代码列表"),
    market: str = Query(default="A股", description="市场（A股 / 港股 / 美股），默认 A股"),
):
    """获取股票实时行情数据。

    使用 akshare.stock_zh_a_spot_em() 全量拉取后过滤。
    """
    symbols_list = list(symbols) if symbols else []
    if symbol and symbol not in symbols_list:
        symbols_list.insert(0, symbol)

    if not symbols_list:
        return {"items": [], "total": 0}

    fetcher = _get_fetcher()
    results = []

    for sym in symbols_list:
        quote = fetcher.get_realtime_quote(sym)
        if quote and quote.has_basic_data():
            results.append(quote.to_dict())

    return {"items": results, "total": len(results)}
