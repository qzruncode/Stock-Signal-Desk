"""``get_risk_events`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol
from src.tools.base import ToolSpec, object_schema


def get_risk_events(symbol: str, days: int = 90) -> Any:
    from api.v1.endpoints.financials import get_risk_events as endpoint
    return endpoint(symbol=resolve_symbol(symbol), days=days)


TOOL = ToolSpec(
    name="get_risk_events",
    description="聚合新闻与公告中的风险线索、主题和启发式标签；返回证据，不代替最终风险判断。",
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "days": {"type": "integer", "minimum": 1, "maximum": 730, "default": 90, "description": "最近天数"},
    }, ["symbol"]),
    executor=get_risk_events,
    category="sentiment",
)
