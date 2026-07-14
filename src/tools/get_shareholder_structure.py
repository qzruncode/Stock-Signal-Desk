"""``get_shareholder_structure`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol


def get_shareholder_structure(symbol: str) -> Any:
    from api.v1.endpoints.financials import get_shareholder_structure as endpoint
    return endpoint(symbol=resolve_symbol(symbol))
