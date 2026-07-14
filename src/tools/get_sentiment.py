"""``get_sentiment`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol


def get_sentiment(symbol: str, days: int = 30) -> Any:
    from api.v1.endpoints.financials import get_sentiment as endpoint
    return endpoint(symbol=resolve_symbol(symbol), days=days)
