"""``get_market_breadth`` tool."""

from typing import Any
from src.tools.base import ToolSpec, object_schema


def get_market_breadth() -> Any:
    from api.v1.endpoints.macro import get_market_breadth as endpoint
    return endpoint()


TOOL = ToolSpec(
    name="get_market_breadth",
    description="获取上涨下跌家数、涨跌停、炸板、新高新低和总成交额等市场宽度指标。",
    parameters=object_schema(),
    executor=get_market_breadth,
    category="market",
)
