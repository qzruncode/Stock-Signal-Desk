"""``get_cashflow`` tool."""

from typing import Any
from src.tools._financial_statements import get_financial_statements
from src.tools.base import ToolSpec, object_schema


def get_cashflow(symbol: str, periods: int = 4) -> Any:
    result = get_financial_statements(symbol, periods)
    return {"symbol": result.get("symbol"), "periods": result.get("periods"), "cashflow": result.get("cashflow", []), "source": result.get("source")}


TOOL = ToolSpec(
    name="get_cashflow",
    description="获取现金流量表，包括经营、投资、筹资现金流、资本开支、自由现金流和利润含金量。",
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "periods": {"type": "integer", "minimum": 2, "maximum": 20, "default": 4, "description": "最近报告期数量"},
    }, ["symbol"]),
    executor=get_cashflow,
    category="financials",
)
