"""``get_cashflow`` tool."""

from typing import Any
from src.tools._financial_statements import get_financial_statements


def get_cashflow(symbol: str, periods: int = 4) -> Any:
    result = get_financial_statements(symbol, periods)
    return {"symbol": result.get("symbol"), "periods": result.get("periods"), "cashflow": result.get("cashflow", []), "source": result.get("source")}
