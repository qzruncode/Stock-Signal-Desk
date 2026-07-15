# -*- coding: utf-8 -*-
"""``get_stock_info`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol
from src.tools.base import ToolSpec, object_schema


def get_stock_info(symbol: str) -> Any:
    from api.v1.endpoints.stock_info import get_stock_info as endpoint
    return endpoint(symbol=resolve_symbol(symbol))


TOOL = ToolSpec(
    name="get_stock_info",
    description="获取个股基础资料、行业、上市日期、主营介绍、股本和基础估值；工具内部可解析股票名称。",
    parameters=object_schema({"symbol": {"type": "string", "description": "股票代码或名称"}}, ["symbol"]),
    executor=get_stock_info,
    category="data",
)
