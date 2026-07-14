# -*- coding: utf-8 -*-
"""K-line data thin route.

工具业务逻辑（多源回退、熔断、限速、缓存、StockDaily 双写、freshness 标记、
响应组装）已提取到 src/tools/kline.py 统一管理。本模块仅保留 FastAPI 路由层
（Query 参数校验 + 委托）。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, HTTPException

from src.tools._kline import DEFAULT_COUNT
from src.tools.get_history_data import get_history_data
from src.tools.get_kline import get_kline

router = APIRouter()


@router.get("", summary="获取日线K线数据")
def get_kline_route(
    symbol: str = Query(..., description="股票代码"),
    count: int = Query(DEFAULT_COUNT, ge=1, le=1000, description="返回条数"),
    use_cache: bool = Query(True, description="是否使用缓存"),
):
    """获取日线K线数据（前复权）。优先本地 StockDaily，其次缓存，最后外部 API。"""
    return get_kline(symbol=symbol, count=count, use_cache=use_cache)


@router.get("/history", summary="获取日线K线数据（按日期范围）")
def get_history_data_route(
    symbol: str = Query(..., description="股票代码"),
    start_date: str = Query(..., description="起始日期 YYYYMMDD"),
    end_date: str = Query(..., description="结束日期 YYYYMMDD"),
    use_cache: bool = Query(True, description="是否使用缓存"),
):
    """获取指定日期范围的日线K线数据（前复权）。"""
    try:
        datetime.strptime(start_date, "%Y%m%d")
        datetime.strptime(end_date, "%Y%m%d")
    except ValueError:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_date", "message": "日期格式错误，应为 YYYYMMDD",
        })

    return get_history_data(
        symbol=symbol, start_date=start_date, end_date=end_date, use_cache=use_cache,
    )
