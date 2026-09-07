"""Compatibility signatures; all source access belongs to the independent data service."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from src.services.market_data_client import read_source


def get_namespaces_flat(
    force: bool = False, finance_only: bool = False, include_hidden: bool = False
) -> Dict[str, Any]:
    return read_source(
        "rss.rss_namespace.get_namespaces_flat",
        {
            "force": force,
            "finance_only": finance_only,
            "include_hidden": include_hidden,
        },
    )


def get_namespace_detail(ns: str, force: bool = False) -> Dict[str, Any]:
    return read_source(
        "rss.rss_namespace.get_namespace_detail", {"ns": ns, "force": force}
    )


def get_categories(
    force: bool = False, finance_only: bool = False, include_hidden: bool = False
) -> List[str]:
    return read_source(
        "rss.rss_namespace.get_categories",
        {
            "force": force,
            "finance_only": finance_only,
            "include_hidden": include_hidden,
        },
    )["data"]
