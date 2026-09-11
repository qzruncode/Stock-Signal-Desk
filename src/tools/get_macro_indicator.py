"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def read_macro_indicator_akshare(
    indicator: str, periods: int = 12, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("get_macro_indicator.read_macro_indicator_akshare", locals())


INDICATORS: dict[str, dict[str, Any]] = {
    "PMI": {
        "name": "制造业采购经理指数",
        "frequency": "monthly",
        "unit": "index_point",
    },
    "CPI": {
        "name": "居民消费价格指数",
        "frequency": "monthly",
        "unit": "index_previous_year_100",
    },
    "PPI": {
        "name": "工业生产者出厂价格指数",
        "frequency": "monthly",
        "unit": "index_previous_year_100",
    },
    "GDP": {"name": "国内生产总值", "frequency": "quarterly", "unit": "亿元"},
    "M2": {"name": "广义货币(M2)", "frequency": "monthly", "unit": "亿元"},
    "社融": {"name": "社会融资规模增量", "frequency": "monthly", "unit": "亿元"},
    "LPR": {"name": "贷款市场报价利率", "frequency": "monthly", "unit": "%"},
}
TOOLS = (
    ToolSpec(
        name="read_macro_indicator_akshare",
        description="从 AKShare 的指定宏观指标来源读取 PMI、CPI、PPI、GDP、M2、社融或 LPR 的逐期结构化记录。只返回来源字段与发布期，不计算趋势，不读取本地缓存作为失败兜底。",
        parameters=object_schema(
            {
                "indicator": {"type": "string", "enum": list(INDICATORS)},
                "periods": {
                    "type": "integer",
                    "minimum": 3,
                    "maximum": 120,
                    "default": 12,
                    "description": "返回最近发布期数；GDP 为季度，其余通常为月度",
                },
            },
            ["indicator"],
        ),
        executor=read_macro_indicator_akshare,
        category="macro",
    ),
)
__all__ = [
    "TOOLS",
    "read_macro_indicator_akshare",
]
