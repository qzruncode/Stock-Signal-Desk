"""Compatibility signatures; all source access belongs to the independent data service."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from src.services.market_data_client import read_source


def get_futunn_topics(
    force: bool = False, keyword: Optional[str] = None
) -> Dict[str, Any]:
    return read_source(
        "rss.futunn_topics.get_futunn_topics", {"force": force, "keyword": keyword}
    )
