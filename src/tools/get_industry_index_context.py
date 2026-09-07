"""Source retrieval is owned by the independent data service."""

from __future__ import annotations
from src.services.market_data_client import read_source


def get_industry_index_context(
    query: str = "",
    index_type: str = "一级行业",
    index_code: str = "",
    include_components: bool = True,
    max_matches: int = 5,
    history_points: int = 120,
):
    return read_source(
        "get_industry_index_context.get_industry_index_context",
        {
            "query": query,
            "index_type": index_type,
            "index_code": index_code,
            "include_components": include_components,
            "max_matches": max_matches,
            "history_points": history_points,
        },
    )
