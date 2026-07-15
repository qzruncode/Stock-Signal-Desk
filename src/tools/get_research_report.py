"""``get_research_report`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol
from src.tools.base import ToolSpec, object_schema


def get_research_report(symbol: str, days: int = 365) -> Any:
    from api.v1.endpoints.financials import get_research_report as endpoint
    return endpoint(symbol=resolve_symbol(symbol), days=days)


TOOL = ToolSpec(
    name="get_research_report",
    description="获取券商个股研报摘要、机构、评级、盈利预测和关键证据；不把研报观点当作事实。",
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "days": {"type": "integer", "minimum": 1, "maximum": 1825, "default": 365, "description": "最近天数"},
    }, ["symbol"]),
    executor=get_research_report,
    category="sentiment",
)
