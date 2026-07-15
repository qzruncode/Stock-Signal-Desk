"""``get_macro_indicator`` tool."""

from typing import Any
from src.tools.base import ToolSpec, object_schema


def get_macro_indicator(indicator: str, months: int = 12) -> Any:
    from api.v1.endpoints.macro import get_macro_indicator as endpoint
    return endpoint(indicator=indicator, months=months)


TOOL = ToolSpec(
    name="get_macro_indicator",
    description="获取 PMI、CPI、PPI、GDP、M2、社融或 LPR 的最新值和历史趋势。",
    parameters=object_schema({
        "indicator": {"type": "string", "enum": ["PMI", "CPI", "PPI", "GDP", "M2", "社融", "LPR"], "description": "宏观指标"},
        "months": {"type": "integer", "minimum": 3, "maximum": 120, "default": 12, "description": "最近月份数量"},
    }, ["indicator"]),
    executor=get_macro_indicator,
    category="macro",
)
