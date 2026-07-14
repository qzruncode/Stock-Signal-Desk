"""``list_rss_sources`` tool."""

from typing import Any, Optional


def list_rss_sources(category: Optional[str] = None, keyword: Optional[str] = None) -> Any:
    from api.v1.endpoints._rss_catalog import list_catalog_routes
    return {"sources": list_catalog_routes(category=category, keyword=keyword)}
