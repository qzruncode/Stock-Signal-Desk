"""``get_macro_indicator`` tool."""

from typing import Any


def get_macro_indicator(indicator: str, months: int = 12) -> Any:
    from api.v1.endpoints.macro import get_macro_indicator as endpoint
    return endpoint(indicator=indicator, months=months)
