"""Compatibility signatures; all source access belongs to the independent data service."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from src.services.market_data_client import read_source


def get_rss_catalog(force: bool = False, *, scope: str = "finance") -> Dict[str, Any]:
    return read_source(
        "rss.rss_catalog.get_rss_catalog", {"force": force, "scope": scope}
    )


def list_catalog_routes(
    *, category: Optional[str] = None, keyword: Optional[str] = None
) -> List[Dict[str, Any]]:
    return read_source(
        "rss.rss_catalog.list_catalog_routes",
        {"category": category, "keyword": keyword},
    )["data"]
