"""``get_research_report`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol


def get_research_report(symbol: str, days: int = 365) -> Any:
    from api.v1.endpoints.financials import get_research_report as endpoint
    return endpoint(symbol=resolve_symbol(symbol), days=days)
