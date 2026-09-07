"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def get_peer_comparison(symbol: str, dimension: str = "all") -> dict[str, Any]:
    return read_source("get_peer_comparison.get_peer_comparison", locals())


def read_peer_comparison_dimension_eastmoney(
    symbol: str, dimension: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source(
        "get_peer_comparison.read_peer_comparison_dimension_eastmoney", locals()
    )


TOOLS = (
    ToolSpec(
        name="read_peer_comparison_dimension_eastmoney",
        description="从东方财富读取一只 A 股在一个明确同行比较维度上的原始对标数据。dimension 必须明确选择成长、估值、盈利能力或规模；不并发读取其他维度，也不输出综合评分。",
        parameters=object_schema(
            {
                "symbol": {"type": "string", "description": "股票代码或股票名称"},
                "dimension": {
                    "type": "string",
                    "enum": ["growth", "valuation", "profitability", "scale"],
                    "description": "同行比较维度",
                },
            },
            ["symbol", "dimension"],
        ),
        executor=read_peer_comparison_dimension_eastmoney,
        category="analysis",
        web_fallback=True,
    ),
)
__all__ = ["TOOLS", "get_peer_comparison", "read_peer_comparison_dimension_eastmoney"]
