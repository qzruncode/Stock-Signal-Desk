"""``get_market_breadth`` tool."""

from typing import Any
from src.tools.base import ToolSpec, object_schema


def get_market_breadth() -> Any:
    from src.tools._market_snapshot import get_market_snapshot, market_breadth_view

    return market_breadth_view(get_market_snapshot())


TOOL = ToolSpec(
    name="get_market_breadth",
    description=(
        "获取沪深 A 股上涨、下跌、平盘、涨跌停、炸板率、涨跌比、市场活跃度和沪深成交额，"
        "用于判断市场参与度与赚钱效应。所有指标均返回真实统计范围，不使用行业数量冒充股票数量。"
    ),
    parameters=object_schema(),
    executor=get_market_breadth,
    category="market",
)
