"""``get_financials`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol
from src.tools.base import ToolSpec, object_schema


def get_financials(symbol: str, periods: int = 6) -> Any:
    from api.v1.endpoints.financials import get_financials as endpoint
    return endpoint(symbol=resolve_symbol(symbol), periods=periods)


TOOL = ToolSpec(
    name="get_financials",
    description="获取核心财务指标，包括盈利、成长、现金流、营运质量、偿债能力和每股指标。",
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "periods": {"type": "integer", "minimum": 2, "maximum": 20, "default": 6, "description": "最近报告期数量"},
    }, ["symbol"]),
    executor=get_financials,
    category="financials",
)
