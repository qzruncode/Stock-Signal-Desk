"""A-share views backed by the data service, not the business database."""

from fastapi import Query
from api.v1.endpoints.stocks import router
from src.services.market_data_client import get_market_data_client


@router.get("")
def list_stocks(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=500),
    search: str = "",
    market: str = "",
):
    result = get_market_data_client().securities(
        page=page, page_size=page_size, search=search, market=market or "all"
    )
    total_pages = max(1, (result["total"] + page_size - 1) // page_size)
    return {**result, "has_more": page < total_pages, "total_pages": total_pages}
