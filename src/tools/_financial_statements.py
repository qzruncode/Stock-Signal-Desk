"""Shared financial-statement loader used by statement-specific tools."""

from typing import Any
from src.tools.symbols import resolve_symbol


def get_financial_statements(symbol: str, periods: int) -> dict[str, Any]:
    from api.v1.endpoints.financials import get_financial_statements as endpoint
    return endpoint(symbol=resolve_symbol(symbol), periods=periods)
