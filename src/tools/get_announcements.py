"""``get_announcements`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol


def get_announcements(symbol: str, days: int = 30, type: str = "all") -> Any:
    from api.v1.endpoints.financials import get_announcements as endpoint
    return endpoint(symbol=resolve_symbol(symbol), days=days, type=type)
