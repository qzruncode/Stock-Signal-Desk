"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def read_market_breadth_legu(*, use_cache: bool = True) -> dict[str, Any]:
    return read_source("market_snapshot_tools.read_market_breadth_legu", locals())


def read_market_breadth_sina(*, use_cache: bool = True) -> dict[str, Any]:
    return read_source("market_snapshot_tools.read_market_breadth_sina", locals())


def read_market_indices_sina(*, use_cache: bool = True) -> dict[str, Any]:
    return read_source("market_snapshot_tools.read_market_indices_sina", locals())


def read_market_limit_up_pool_eastmoney(
    trade_date: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source(
        "market_snapshot_tools.read_market_limit_up_pool_eastmoney", locals()
    )


def read_market_limit_down_pool_eastmoney(
    trade_date: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source(
        "market_snapshot_tools.read_market_limit_down_pool_eastmoney", locals()
    )


def read_market_broken_board_pool_eastmoney(
    trade_date: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source(
        "market_snapshot_tools.read_market_broken_board_pool_eastmoney", locals()
    )


TOOLS = (
    ToolSpec(
        name="read_market_breadth_legu",
        description="从乐咕乐股读取沪深 A 股市场活跃度表中的上涨、下跌、平盘、停牌及其原始涨跌停计数；不改用新浪兜底，也不计算赚钱效应、涨跌比或市场结论。",
        parameters=object_schema(),
        executor=read_market_breadth_legu,
        category="market",
    ),
    ToolSpec(
        name="read_market_breadth_sina",
        description="从新浪全 A 股实时行情读取上涨、下跌、平盘计数；这是独立来源，供模型在需要时与其他来源交叉核对，不作为乐咕的程序兜底。",
        parameters=object_schema(),
        executor=read_market_breadth_sina,
        category="market",
    ),
    ToolSpec(
        name="read_market_indices_sina",
        description="从新浪实时指数行情读取上证、深证、创业板、科创50的价格、涨跌和沪深成交额；不读取市场宽度、涨跌停池或指数日线。",
        parameters=object_schema(),
        executor=read_market_indices_sina,
        category="market",
    ),
    ToolSpec(
        name="read_market_limit_up_pool_eastmoney",
        description="从东方财富（AKShare）读取一个指定交易日的涨停池数量。",
        parameters=object_schema(
            {
                "trade_date": {
                    "type": "string",
                    "pattern": "^[0-9]{8}$",
                    "description": "交易日，YYYYMMDD",
                }
            },
            ["trade_date"],
        ),
        executor=read_market_limit_up_pool_eastmoney,
        category="market",
    ),
    ToolSpec(
        name="read_market_limit_down_pool_eastmoney",
        description="从东方财富（AKShare）读取一个指定交易日的跌停池数量。",
        parameters=object_schema(
            {
                "trade_date": {
                    "type": "string",
                    "pattern": "^[0-9]{8}$",
                    "description": "交易日，YYYYMMDD",
                }
            },
            ["trade_date"],
        ),
        executor=read_market_limit_down_pool_eastmoney,
        category="market",
    ),
    ToolSpec(
        name="read_market_broken_board_pool_eastmoney",
        description="从东方财富（AKShare）读取一个指定交易日的炸板池数量。",
        parameters=object_schema(
            {
                "trade_date": {
                    "type": "string",
                    "pattern": "^[0-9]{8}$",
                    "description": "交易日，YYYYMMDD",
                }
            },
            ["trade_date"],
        ),
        executor=read_market_broken_board_pool_eastmoney,
        category="market",
    ),
)
__all__ = ["TOOLS"]
