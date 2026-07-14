"""``get_sector_flow`` tool."""

from typing import Any


def get_sector_flow(type: str = "industry", top_n: int = 10) -> Any:
    from api.v1.endpoints.macro import get_sector_flow as endpoint
    return endpoint(type=type, top_n=top_n)
