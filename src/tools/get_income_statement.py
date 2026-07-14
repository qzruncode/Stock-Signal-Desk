"""``get_income_statement`` tool."""

from typing import Any
from src.tools._financial_statements import get_financial_statements


def get_income_statement(symbol: str, periods: int = 4) -> Any:
    result = get_financial_statements(symbol, periods)
    return {"symbol": result.get("symbol"), "periods": result.get("periods"), "income_statement": result.get("income_statement", []), "source": result.get("source")}
