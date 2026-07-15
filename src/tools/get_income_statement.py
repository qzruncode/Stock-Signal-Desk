"""``get_income_statement`` tool."""

from typing import Any
from src.tools._financial_statements import get_financial_statements
from src.tools.base import ToolSpec, object_schema


def get_income_statement(symbol: str, periods: int = 4) -> Any:
    result = get_financial_statements(symbol, periods)
    return {"symbol": result.get("symbol"), "periods": result.get("periods"), "income_statement": result.get("income_statement", []), "source": result.get("source")}


TOOL = ToolSpec(
    name="get_income_statement",
    description="获取利润表，包括收入、成本、营业利润、净利润、扣非净利润、每股收益及费用明细。",
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "periods": {"type": "integer", "minimum": 2, "maximum": 20, "default": 4, "description": "最近报告期数量"},
    }, ["symbol"]),
    executor=get_income_statement,
    category="financials",
)
