"""Compatibility signatures; all source access belongs to the independent data service."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from src.services.market_data_client import read_source


def get_subjects(force: bool = False, keyword: Optional[str] = None) -> Dict[str, Any]:
    return read_source(
        "rss.gelonghui_subjects.get_subjects", {"force": force, "keyword": keyword}
    )
