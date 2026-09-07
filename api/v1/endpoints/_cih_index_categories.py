"""Compatibility signatures; all source access belongs to the independent data service."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from src.services.market_data_client import read_source


def get_cih_index_categories(force: bool = False) -> Dict[str, Any]:
    return read_source(
        "rss.cih_index_categories.get_cih_index_categories", {"force": force}
    )
