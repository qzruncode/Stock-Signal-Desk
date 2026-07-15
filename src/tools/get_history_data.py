# -*- coding: utf-8 -*-
"""``get_history_data`` tool."""

from typing import Any

from src.tools._kline import KLINE_HISTORY_DESCRIPTION, get_history_data
from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_symbol


def _execute(symbol: str, start_date: str, end_date: str, use_cache: bool = True) -> dict[str, Any]:
    return get_history_data(resolve_symbol(symbol), start_date=start_date, end_date=end_date, use_cache=use_cache)


TOOL = ToolSpec(
    name="get_history_data",
    description=KLINE_HISTORY_DESCRIPTION,
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "start_date": {"type": "string", "pattern": "^[0-9]{8}$", "description": "起始日期 YYYYMMDD"},
        "end_date": {"type": "string", "pattern": "^[0-9]{8}$", "description": "结束日期 YYYYMMDD"},
        "use_cache": {"type": "boolean", "default": True, "description": "是否使用本地缓存"},
    }, ["symbol", "start_date", "end_date"]),
    executor=_execute,
    category="data",
)

__all__ = ["KLINE_HISTORY_DESCRIPTION", "get_history_data", "TOOL"]
