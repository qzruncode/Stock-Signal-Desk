# -*- coding: utf-8 -*-
"""``get_market_status`` tool."""

from typing import Any
from src.tools.base import ToolSpec, object_schema


def get_market_status() -> Any:
    from src.tools._market_snapshot import get_market_snapshot, market_status_view

    return market_status_view(get_market_snapshot())


TOOL = ToolSpec(
    name="get_market_status",
    description=(
        "获取沪深 A 股市场实时状态，包括主要指数、上涨下跌家数、涨跌停数和沪深成交额。"
        "返回每项指标的统计范围、实际数据时间及降级信息；已停止公开披露的北向净流入会明确标记不可用。"
    ),
    parameters=object_schema(),
    executor=get_market_status,
    category="market",
)
