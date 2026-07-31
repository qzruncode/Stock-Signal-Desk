"""Read one filtered RSSHub finance route into a text-only collection."""

from __future__ import annotations

from typing import Any

from src.tools._rss_agent import (
    endpoint_value,
    ensure_rss_route,
    object_value,
    rss_item_ref,
    rss_options_schema,
)
from src.tools.base import ToolSpec, object_schema, report_tool_progress


def read_rss_feed(
    route_path: str,
    params: dict[str, Any] | str | None = None,
    options: dict[str, Any] | str | None = None,
    namespace: str = "",
    limit: int = 30,
    force: bool = False,
) -> dict[str, Any]:
    from api.v1.endpoints._rss_text import normalize_text_item
    from api.v1.endpoints.rss import FeedSpecRequest, get_rss_feeds_by_spec

    report_tool_progress("正在读取 Feed", progress=15)
    path = ensure_rss_route(route_path)
    clean_params = object_value(params, "params")
    clean_options = object_value(options, "options")
    bounded = max(1, min(int(limit or 30), 100))
    body = FeedSpecRequest(
        route_path=path,
        params=clean_params,
        options=clean_options,
        namespace=str(namespace or "").strip() or None,
        limit=bounded,
        force=bool(force),
    )
    raw = endpoint_value(lambda: get_rss_feeds_by_spec(body))
    items: list[dict[str, Any]] = []
    discarded_non_text = 0
    for value in raw.get("items") or []:
        if not isinstance(value, dict):
            continue
        item = normalize_text_item(value)
        discarded_non_text += int(item.get("_discarded_non_text") or 0)
        item["item_ref"] = rss_item_ref(
            route_path=path,
            params=clean_params,
            options=clean_options,
            namespace=str(body.namespace or ""),
            item=item,
        )
        items.append(item)
    errors = [str(error) for error in raw.get("errors") or []]
    data_time = raw.get("_fetched_at")
    report_tool_progress("Feed 读取完成", progress=100)
    return {
        **raw,
        "success": bool(items) or not errors,
        "partial": bool(items) and bool(errors),
        "route_path": path,
        "params": clean_params,
        "options": clean_options,
        "namespace": body.namespace,
        "items": items,
        "item_count": len(items),
        "coverage": {
            "planned_sources": 1,
            "attempted_sources": 1,
            "successful_sources": 1 if items or not errors else 0,
            "item_count": len(items),
            "text_documents_found": sum(
                len(item.get("attachments") or []) for item in items
            ),
            "text_documents_extracted": 0,
            "discarded_non_text": discarded_non_text,
            "failures": errors,
        },
        "data_time": data_time,
        "is_stale": False if data_time else None,
        "freshness_unknown": data_time is None,
        "errors": errors,
        "warnings": errors if items else [],
    }


TOOL = ToolSpec(
    name="read_rss_feed",
    description=(
        "读取助手已筛选 RSSHub 来源目录中的路由，返回纯文本条目、稳定 item_ref 和"
        "文本型附件；图片、音频、视频会在规范化阶段丢弃。"
    ),
    parameters=object_schema(
        {
            "route_path": {"type": "string"},
            "params": {"type": "object", "additionalProperties": True},
            "options": rss_options_schema(),
            "namespace": {"type": "string"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 30,
            },
            "force": {"type": "boolean", "default": False},
        },
        required=("route_path",),
    ),
    executor=read_rss_feed,
    category="sentiment",
)


__all__ = ["TOOL", "read_rss_feed"]
