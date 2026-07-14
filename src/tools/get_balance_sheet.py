"""``get_balance_sheet`` tool."""

from typing import Any
from src.tools._financial_statements import get_financial_statements


def get_balance_sheet(symbol: str, periods: int = 4) -> Any:
    result = get_financial_statements(symbol, periods)
    return {"symbol": result.get("symbol"), "periods": result.get("periods"), "balance_sheet": result.get("balance_sheet", []), "source": result.get("source")}
