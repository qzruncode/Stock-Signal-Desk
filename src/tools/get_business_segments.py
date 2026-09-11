"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def read_business_segments_eastmoney(
    symbol: str, category: str = "all", periods: int = 2
) -> dict[str, Any]:
    return read_source(
        "get_business_segments.read_business_segments_eastmoney", locals()
    )


SOURCE_DESCRIPTION = "从东方财富主营构成披露（AKShare）读取一只 A 股按产品、行业或地区列示的主营收入、成本、毛利及来源披露的占比。返回逐条披露记录，不按收入或利润排序、不计算集中度、不判断主要利润来源；中期数据为年初至报告期累计口径。"
TOOLS = (
    ToolSpec(
        name="read_business_segments_eastmoney",
        description=SOURCE_DESCRIPTION,
        parameters=object_schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "股票代码或股票名称，如 600519 或 贵州茅台",
                },
                "category": {
                    "type": "string",
                    "enum": ["all", "product", "industry", "region"],
                    "default": "all",
                    "description": "主营分类：all 全部、product 产品、industry 行业、region 地区",
                },
                "periods": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 8,
                    "default": 2,
                    "description": "返回最近披露报告期数量",
                },
            },
            ["symbol"],
        ),
        executor=read_business_segments_eastmoney,
        category="financials",
    ),
)
__all__ = ["TOOLS", "read_business_segments_eastmoney"]
