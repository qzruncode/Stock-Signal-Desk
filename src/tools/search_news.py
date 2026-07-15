"""``search_news`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol
from src.tools.base import ToolSpec, object_schema


def search_news(symbol: str, days: int = 30, source: str = "all") -> Any:
    from api.v1.endpoints.financials import search_news as endpoint
    return endpoint(symbol=resolve_symbol(symbol), days=days, source=source)


TOOL = ToolSpec(
    name="search_news",
    description="搜索指定股票的项目内 RSSHub 新闻与研报，并返回来源、事件、情绪和证据汇总。仅用于个股；主题资讯使用 search_financial_news。",
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码、名称或公司关键词"},
        "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30, "description": "最近天数"},
        "source": {"type": "string", "enum": ["all", "eastmoney", "news", "research"], "default": "all", "description": "来源类型"},
    }, ["symbol"]),
    executor=search_news,
    category="sentiment",
)
