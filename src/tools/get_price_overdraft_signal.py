"""``get_price_overdraft_signal`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol


def get_price_overdraft_signal(symbol: str) -> Any:
    from api.v1.endpoints.financials import get_price_overdraft_signal as endpoint
    return endpoint(symbol=resolve_symbol(symbol))
