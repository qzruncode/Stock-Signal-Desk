"""Inspect one route from the existing filtered finance catalog."""

from __future__ import annotations

from src.tools._rss_agent import source_ref
from src.tools.base import ToolSpec, object_schema
from src.tools.inspect_financial_source import _dynamic_options


def inspect_rss_source(
    route_path: str,
    keyword: str = "",
    force: bool = False,
) -> dict:
    from api.v1.endpoints._rss_catalog import get_rss_catalog

    path = str(route_path or "").strip()
    catalog = get_rss_catalog(force=bool(force), scope="finance")
    route = next(
        (
            item
            for item in catalog.get("routes") or []
            if isinstance(item, dict) and item.get("route_path") == path
        ),
        None,
    )
    if route is None:
        raise ValueError(f"RSSHub 路由不在助手已筛选来源目录中: {path}")
    dynamic = _dynamic_options(
        path,
        str(keyword or "").strip(),
        bool(force),
    )
    dynamic_error = (
        dynamic.get("_error") if isinstance(dynamic, dict) else None
    )
    data_time = (
        dynamic.get("_fetched_at")
        if isinstance(dynamic, dict)
        else None
    ) or catalog.get("_fetched_at")
    inspected_ref = source_ref(route)
    return {
        "success": True,
        "partial": bool(dynamic_error) and bool(dynamic),
        "route": route,
        "source_ref": inspected_ref,
        "items": [
            {
                **route,
                "source_ref": inspected_ref,
                "relevance_score": 1.0,
            }
        ],
        "dynamic_options": dynamic,
        "coverage": {
            "planned_sources": 1,
            "attempted_sources": 1,
            "successful_sources": 1,
            "item_count": 1,
            "text_documents_found": 0,
            "text_documents_extracted": 0,
            "discarded_non_text": 0,
            "failures": [],
        },
        "data_time": data_time,
        "is_stale": (
            bool(dynamic.get("_stale"))
            if isinstance(dynamic, dict) and data_time
            else bool(catalog.get("_stale")) if data_time else None
        ),
        "freshness_unknown": data_time is None,
        "errors": [],
        "warnings": [str(dynamic_error)] if dynamic_error else [],
    }


TOOL = ToolSpec(
    name="inspect_rss_source",
    description=(
        "检查助手已筛选 RSSHub 来源的参数、功能、健康状态和动态可选值。route_path "
        "必须来自 discover_rss_sources。"
    ),
    parameters=object_schema(
        {
            "route_path": {"type": "string"},
            "keyword": {"type": "string"},
            "force": {"type": "boolean", "default": False},
        },
        required=("route_path",),
    ),
    executor=inspect_rss_source,
    category="sentiment",
)


__all__ = ["TOOL", "inspect_rss_source"]
