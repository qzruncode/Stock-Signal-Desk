"""``get_announcements`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol
from src.tools.base import ToolSpec, object_schema


def get_announcements(symbol: str, days: int = 30, type: str = "all") -> Any:
    from api.v1.endpoints.financials import get_announcements as endpoint
    return endpoint(symbol=resolve_symbol(symbol), days=days, type=type)


TOOL = ToolSpec(
    name="get_announcements",
    description="获取上市公司正式公告及重要性、事件标签、风险、资本动作和治理变化证据。",
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "days": {"type": "integer", "minimum": 1, "maximum": 730, "default": 30, "description": "最近天数"},
        "type": {"type": "string", "enum": ["all", "业绩", "分红", "增持", "减持", "高管变动"], "default": "all", "description": "公告类型"},
    }, ["symbol"]),
    executor=get_announcements,
    category="sentiment",
)
