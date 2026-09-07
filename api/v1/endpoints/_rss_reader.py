"""Compatibility signatures; all source access belongs to the independent data service."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from src.services.market_data_client import read_source

DEFAULT_FEED_LIMIT = 20


def read_feed(
    route_path: str,
    params: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    namespace: Optional[str] = None,
    limit: int = DEFAULT_FEED_LIMIT,
    force: bool = False,
    fallback_to_xml: bool = True,
) -> Dict[str, Any]:
    return read_source(
        "rss.read_feed",
        {
            "route_path": route_path,
            "params": params,
            "options": options,
            "namespace": namespace,
            "limit": limit,
            "force": force,
            "fallback_to_xml": fallback_to_xml,
        },
    )


def read_item(
    route_path: str,
    params: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    namespace: Optional[str] = None,
    title: str = "",
    item_id: str = "",
    link: str = "",
    list_content_html: str = "",
    list_summary: str = "",
    list_image: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    return read_source(
        "rss.read_item",
        {
            "route_path": route_path,
            "params": params,
            "options": options,
            "namespace": namespace,
            "title": title,
            "item_id": item_id,
            "link": link,
            "list_content_html": list_content_html,
            "list_summary": list_summary,
            "list_image": list_image,
            "force": force,
        },
    )
