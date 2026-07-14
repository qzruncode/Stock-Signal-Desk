# -*- coding: utf-8 -*-
"""``get_stock_business`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol


def get_stock_business(symbol: str) -> Any:
    from api.v1.endpoints.stock_info import get_stock_business as endpoint
    return endpoint(symbol=resolve_symbol(symbol), force=False)
