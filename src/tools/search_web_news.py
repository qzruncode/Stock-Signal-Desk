"""``search_web_news`` tool."""

from typing import Any
from src.tools._search_response import serialize_search_response
from src.tools.symbols import resolve_symbol


def search_web_news(symbol: str, max_results: int = 5) -> Any:
    from src.data.stock_mapping import STOCK_NAME_MAP
    from src.search_service import get_search_service
    code = resolve_symbol(symbol)
    response = get_search_service().search_stock_news(code, STOCK_NAME_MAP.get(code, symbol), max_results=max_results)
    return serialize_search_response(response)
