"""``get_valuation_ratios`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol


def get_valuation_ratios(symbol: str, with_history: bool = True) -> Any:
    from api.v1.endpoints.financials import get_valuation_ratios as endpoint
    return endpoint(symbol=resolve_symbol(symbol), with_history=with_history)
