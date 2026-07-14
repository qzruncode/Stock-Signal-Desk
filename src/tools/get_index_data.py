"""``get_index_data`` tool."""

from typing import Any


def get_index_data(index_code: str = "000001", days: int = 20) -> Any:
    from api.v1.endpoints.macro import get_index_data as endpoint
    return endpoint(index_code=index_code, days=days)
