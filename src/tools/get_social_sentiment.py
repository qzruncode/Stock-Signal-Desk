"""``get_social_sentiment`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol
from src.tools.base import ToolSpec, object_schema


def get_social_sentiment(symbol: str, days: int = 30) -> Any:
    from api.v1.endpoints.financials import get_social_sentiment as endpoint
    return endpoint(symbol=resolve_symbol(symbol), days=days)


TOOL = ToolSpec(
    name="get_social_sentiment",
    description="获取个股公开讨论代理舆情、情绪比例、趋势、主题和证据；仅反映采样讨论，不代表全市场观点。",
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "days": {"type": "integer", "minimum": 1, "maximum": 180, "default": 30, "description": "最近天数"},
    }, ["symbol"]),
    executor=get_social_sentiment,
    category="sentiment",
)
