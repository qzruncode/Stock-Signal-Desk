"""``get_market_breadth`` tool."""

from typing import Any


def get_market_breadth() -> Any:
    from api.v1.endpoints.macro import get_market_breadth as endpoint
    return endpoint()
