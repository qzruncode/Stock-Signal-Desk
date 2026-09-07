"""Source retrieval is owned by the independent data service."""

from __future__ import annotations
from src.services.market_data_client import read_source


def get_market_regime(index: str = "沪深300", history_points: int = 120):
    return read_source(
        "get_market_regime.get_market_regime",
        {"index": index, "history_points": history_points},
    )
