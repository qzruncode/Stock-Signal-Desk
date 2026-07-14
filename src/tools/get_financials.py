"""``get_financials`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol


def get_financials(symbol: str, periods: int = 6) -> Any:
    from api.v1.endpoints.financials import get_financials as endpoint
    return endpoint(symbol=resolve_symbol(symbol), periods=periods)
