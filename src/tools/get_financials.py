"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def get_financials(
    symbol: str, periods: int = 6, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("financials.get_financials", locals())


def read_core_financial_indicators_ths(
    symbol: str, periods: int = 6, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("financials.read_core_financial_indicators_ths", locals())


TOOLS = (
    ToolSpec(
        name="read_core_financial_indicators_ths",
        description="从同花顺财务摘要（AKShare）读取一只 A 股的已披露核心指标；返回该来源原始报告期指标，不合并东方财富三张报表，也不生成财务结论。",
        parameters=object_schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码或可解析的股票名称",
                },
                "periods": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 20,
                    "default": 6,
                    "description": "最近报告期数量",
                },
            },
            ["symbol"],
        ),
        executor=read_core_financial_indicators_ths,
        category="financials",
    ),
)
__all__ = ["TOOLS", "get_financials", "read_core_financial_indicators_ths"]
