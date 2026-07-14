# -*- coding: utf-8 -*-
"""Real-time stock quote endpoints.

工具业务逻辑（交易时段感知缓存、freshness 标记、多源拉取编排）已提取到
src/tools/realtime_quotes.py 统一管理。本模块仅保留 FastAPI 路由层。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from src.tools.get_realtime_quotes import (
    REALTIME_QUOTES_DESCRIPTION,
    _get_fetcher,
    get_realtime_quotes as _get_realtime_quotes,
)

router = APIRouter()


@router.get(
    "/realtime",
    summary="Get real-time stock quotes",
)
def get_realtime_quotes(
    symbol: str = Query(default="", description="股票代码（如 600519）"),
    symbols: list[str] | None = Query(default=None, description="股票代码列表"),
):
    """获取 A 股实时行情数据。

    非交易时段：优先从数据库缓存读取，首次访问时自动拉取并缓存。
    交易时段：每次实时拉取并更新缓存。
    """
    symbols_list = list(symbols) if symbols else []
    if symbol and symbol not in symbols_list:
        symbols_list.insert(0, symbol)

    return _get_realtime_quotes(symbols_list)
