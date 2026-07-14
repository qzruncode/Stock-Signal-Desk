"""``get_risk_events`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol


def get_risk_events(symbol: str, days: int = 90) -> Any:
    from api.v1.endpoints.financials import get_risk_events as endpoint
    return endpoint(symbol=resolve_symbol(symbol), days=days)
