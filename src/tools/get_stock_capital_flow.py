"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def read_stock_capital_flow_history_eastmoney(
    symbol: str, days: int = 20, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source(
        "get_stock_capital_flow.read_stock_capital_flow_history_eastmoney", locals()
    )


def read_stock_capital_flow_quote_eastmoney(
    symbol: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source(
        "get_stock_capital_flow.read_stock_capital_flow_quote_eastmoney", locals()
    )


TOOLS = (
    ToolSpec(
        name="read_stock_capital_flow_history_eastmoney",
        description="从东方财富读取一只 A 股的资金流日线序列，包含主力、超大单、大单、中单、小单净流入及占比；不补入实时快照，不汇总 5/10/20 日结论。",
        parameters=object_schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码或可解析的股票名称",
                },
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                    "description": "返回最近交易日数量",
                },
            },
            ["symbol"],
        ),
        executor=read_stock_capital_flow_history_eastmoney,
        category="market",
    ),
    ToolSpec(
        name="read_stock_capital_flow_quote_eastmoney",
        description="从东方财富读取一只 A 股当前交易日的资金流快照；不查询历史日线、不计算资金持续性，也不把成交单大小解释为机构持仓。",
        parameters=object_schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码或可解析的股票名称",
                }
            },
            ["symbol"],
        ),
        executor=read_stock_capital_flow_quote_eastmoney,
        category="market",
    ),
)
__all__ = [
    "TOOLS",
    "read_stock_capital_flow_history_eastmoney",
    "read_stock_capital_flow_quote_eastmoney",
]
