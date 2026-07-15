"""``get_balance_sheet`` tool."""

from typing import Any
from src.tools._financial_statements import get_financial_statements
from src.tools.base import ToolSpec, object_schema


def get_balance_sheet(symbol: str, periods: int = 4) -> Any:
    result = get_financial_statements(symbol, periods)
    return {"symbol": result.get("symbol"), "periods": result.get("periods"), "balance_sheet": result.get("balance_sheet", []), "source": result.get("source")}


TOOL = ToolSpec(
    name="get_balance_sheet",
    description="获取资产负债表，包括资产、负债、权益、现金、应收、存货、借款及杠杆指标。",
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "periods": {"type": "integer", "minimum": 2, "maximum": 20, "default": 4, "description": "最近报告期数量"},
    }, ["symbol"]),
    executor=get_balance_sheet,
    category="financials",
)
