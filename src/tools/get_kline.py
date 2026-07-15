# -*- coding: utf-8 -*-
"""``get_kline`` tool."""

from typing import Any

from src.tools._kline import KLINE_DESCRIPTION, get_kline
from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_symbol


def _execute(symbol: str, count: int = 60, use_cache: bool = True) -> dict[str, Any]:
    return get_kline(resolve_symbol(symbol), count=count, use_cache=use_cache)


TOOL = ToolSpec(
    name="get_kline",
    description=KLINE_DESCRIPTION,
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "count": {"type": "integer", "minimum": 20, "maximum": 500, "default": 60, "description": "最近日线数量"},
        "use_cache": {"type": "boolean", "default": True, "description": "默认使用本地缓存；仅怀疑数据陈旧时设为 false"},
    }, ["symbol"]),
    executor=_execute,
    category="data",
)

__all__ = ["KLINE_DESCRIPTION", "get_kline", "TOOL"]
