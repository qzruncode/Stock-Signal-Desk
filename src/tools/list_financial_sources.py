"""Browse the curated finance-source catalog formerly exposed by Infos."""

from __future__ import annotations

import re
from typing import Any

from src.tools.rss_sources import RSS_ROUTE_CAPABILITIES


_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _matches_keyword(route: dict[str, Any], keyword: str) -> bool:
    """Match short ASCII terms as words so `AI` does not match `kuaixun`."""
    if not keyword:
        return True
    searchable = " ".join(
        str(route.get(key) or "") for key in ("route_path", "name", "namespace", "namespace_name", "description")
    ).lower()
    query_tokens = _ASCII_TOKEN_RE.findall(keyword)
    if keyword.isascii() and query_tokens and any(len(token) <= 3 for token in query_tokens):
        searchable_tokens = set(_ASCII_TOKEN_RE.findall(searchable))
        return all(token in searchable_tokens for token in query_tokens)
    return keyword in searchable


def list_financial_sources(
    keyword: str = "",
    namespace: str = "",
    capability: str = "all",
    force: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    from api.v1.endpoints._rss_catalog import get_rss_catalog

    catalog = get_rss_catalog(force=bool(force))
    keyword_lower = str(keyword or "").strip().lower()
    namespace_lower = str(namespace or "").strip().lower()
    bounded = max(1, min(int(limit or 50), 100))
    items: list[dict[str, Any]] = []
    routes = [route for route in (catalog.get("routes") or []) if isinstance(route, dict)]
    for route in routes:
        path = str(route.get("route_path") or "")
        capabilities = sorted(RSS_ROUTE_CAPABILITIES.get(path, frozenset()))
        if capability != "all" and capability not in capabilities:
            continue
        namespace_text = f"{route.get('namespace', '')} {route.get('namespace_name', '')}".lower()
        if namespace_lower and namespace_lower not in namespace_text:
            continue
        if not _matches_keyword(route, keyword_lower):
            continue
        items.append(
            {
                "route_path": path,
                "name": route.get("name"),
                "namespace": route.get("namespace"),
                "namespace_name": route.get("namespace_name"),
                "description": route.get("description"),
                "example": route.get("example"),
                "params": route.get("params") or [],
                "capabilities": capabilities,
                "categories": route.get("categories") or [],
                "features": route.get("features") or {},
                "maintainers": route.get("maintainers") or [],
                "requires_configuration": bool(route.get("requires_configuration")),
            }
        )
    returned = items[:bounded]
    data_time = catalog.get("_fetched_at")
    stale = bool(catalog.get("_stale"))
    error = catalog.get("_error")
    return {
        "success": bool(items) or not error,
        "partial": bool(error) and bool(items),
        "items": returned,
        "catalog_count": len(routes),
        "matched_count": len(items),
        "item_count": len(items),
        "returned_count": len(returned),
        "has_more": len(items) > len(returned),
        "data_time": data_time,
        "is_stale": stale if data_time else None,
        "freshness_unknown": data_time is None,
        "query_scope": "source_catalog_metadata",
        "query_note": (
            "keyword 仅筛选来源名称、命名空间、路由和用途；搜索资讯内容请使用 websearch，"
            "或通过 discover_rss_sources、inspect_rss_source、read_rss_feed 读取具体来源。"
        ),
        "applied_filters": {
            "keyword": str(keyword or "").strip(),
            "namespace": str(namespace or "").strip(),
            "capability": capability,
        },
        "errors": [str(error)] if error and not items else [],
        "warnings": [str(error)] if error and items else [],
    }
__all__ = ["list_financial_sources"]
