"""Inspect one finance source and resolve its dynamic route parameters."""

from __future__ import annotations

from typing import Any

from src.tools._rss_agent import endpoint_value
from src.tools.rss_sources import RSS_ROUTE_CAPABILITIES


def _dynamic_options(route_path: str, keyword: str, force: bool) -> dict[str, Any] | None:
    if route_path.startswith("/gelonghui/subject/"):
        from api.v1.endpoints._gelonghui_subjects import get_subjects

        return get_subjects(force=force, keyword=keyword or None)
    if route_path == "/nanhua/report/:type1/:type2":
        from api.v1.endpoints._nanhua_tree import get_nanhua_tree

        return get_nanhua_tree(force=force)
    if route_path == "/cih-index/report/list/:report?":
        from api.v1.endpoints._cih_index_categories import get_cih_index_categories

        return get_cih_index_categories(force=force)
    if route_path.startswith("/cls/subject/"):
        from api.v1.endpoints._cls_subjects import get_cls_subjects

        return get_cls_subjects(force=force, keyword=keyword or None)
    if route_path.startswith("/futunn/topic/"):
        from api.v1.endpoints._futunn_topics import get_futunn_topics

        return get_futunn_topics(force=force, keyword=keyword or None)
    return None


def inspect_financial_source(
    route_path: str,
    keyword: str = "",
    force: bool = False,
) -> dict[str, Any]:
    from api.v1.endpoints._rss_catalog import get_rss_catalog

    path = str(route_path or "").strip()
    catalog = get_rss_catalog(force=bool(force))
    route = next(
        (item for item in catalog.get("routes") or [] if item.get("route_path") == path),
        None,
    )
    if route is None:
        raise ValueError(f"资讯源路由不存在: {path}")
    dynamic = _dynamic_options(path, str(keyword or "").strip(), bool(force))
    readiness = None
    if path.startswith("/xueqiu/"):
        from api.v1.endpoints.rss import test_xueqiu_cookie

        readiness = endpoint_value(test_xueqiu_cookie)
    data_time = (dynamic.get("_fetched_at") if isinstance(dynamic, dict) else None) or catalog.get("_fetched_at")
    stale = bool(dynamic.get("_stale")) if isinstance(dynamic, dict) else bool(catalog.get("_stale"))
    dynamic_error = dynamic.get("_error") if isinstance(dynamic, dict) else None
    return {
        "success": not bool(dynamic_error),
        "partial": bool(dynamic_error) and bool(dynamic),
        "route": {
            **route,
            "capabilities": sorted(RSS_ROUTE_CAPABILITIES.get(path, frozenset())),
        },
        "dynamic_options": dynamic,
        "readiness": readiness,
        "data_time": data_time,
        "is_stale": stale if data_time else None,
        "freshness_unknown": data_time is None,
        "errors": [str(dynamic_error)] if dynamic_error and not dynamic else [],
        "warnings": [str(dynamic_error)] if dynamic_error and dynamic else [],
    }
__all__ = ["inspect_financial_source"]
