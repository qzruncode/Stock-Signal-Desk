# -*- coding: utf-8 -*-
"""``get_market_status`` tool."""

from typing import Any
from src.tools.base import ToolSpec, object_schema


def get_market_status() -> Any:
    from api.v1.endpoints.market_status import get_market_status as endpoint
    return endpoint(force=False)


TOOL = ToolSpec(
    name="get_market_status",
    description="获取 A 股市场整体状态，包括涨跌家数、涨跌停数、成交额、北向资金和主要指数。",
    parameters=object_schema(),
    executor=get_market_status,
    category="market",
)
