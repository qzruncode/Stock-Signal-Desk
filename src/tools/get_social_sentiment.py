"""Source retrieval is owned by the independent data service."""

from __future__ import annotations
from src.services.market_data_client import read_source


def get_social_sentiment(
    symbol: str,
    days: int = 30,
    limit: int = 50,
    max_pages: int = 3,
    use_cache: bool = True,
):
    return read_source(
        "get_social_sentiment.get_social_sentiment",
        {
            "symbol": symbol,
            "days": days,
            "limit": limit,
            "max_pages": max_pages,
            "use_cache": use_cache,
        },
    )
