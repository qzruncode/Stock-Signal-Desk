"""``get_bond_yield`` tool."""

from typing import Any


def get_bond_yield(country: str = "cn", term: str = "10y") -> Any:
    from api.v1.endpoints.macro import get_bond_yield as endpoint
    return endpoint(country=country, term=term)
