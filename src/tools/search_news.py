"""``search_news`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol


def search_news(symbol: str, days: int = 30, source: str = "all") -> Any:
    from api.v1.endpoints.financials import search_news as endpoint
    return endpoint(symbol=resolve_symbol(symbol), days=days, source=source)
