"""Read an arbitrary curated RSS FeedSpec through the Agent."""

from __future__ import annotations

from typing import Any

from src.tools._rss_agent import endpoint_value, ensure_financial_route, object_value, rss_options_schema
from src.tools.base import ToolSpec, object_schema


def read_financial_feed(
    route_path: str,
    params: dict[str, Any] | str | None = None,
    options: dict[str, Any] | str | None = None,
    namespace: str = "",
    limit: int = 30,
    force: bool = False,
) -> dict[str, Any]:
    from api.v1.endpoints.rss import FeedSpecRequest, get_rss_feeds_by_spec

    clean_params = object_value(params, "params")
    clean_options = object_value(options, "options")
    bounded = max(1, min(int(limit or 30), 100))
    body = FeedSpecRequest(
        route_path=ensure_financial_route(route_path),
        params=clean_params,
        options=clean_options,
        namespace=str(namespace or "").strip() or None,
        limit=bounded,
        force=bool(force),
    )
    result = endpoint_value(lambda: get_rss_feeds_by_spec(body))
    items = result.get("items") or []
    errors = [str(error) for error in result.get("errors") or []]
    data_time = result.get("_fetched_at")
    return {
        **result,
        "success": bool(items) or not errors,
        "partial": bool(items) and bool(errors),
        "route_path": body.route_path,
        "params": clean_params,
        "options": clean_options,
        "namespace": body.namespace,
        "item_count": len(items),
        "data_time": data_time,
        "is_stale": False if data_time else None,
        "freshness_unknown": data_time is None,
        "errors": errors,
        "warnings": errors if items else [],
    }


TOOL = ToolSpec(
    name="read_financial_feed",
    description=(
        "读取任意财经 RSS 路由，支持路由参数、limit、正则过滤/排除、繁简转换、排序、全文模式和强制刷新。"
        "route_path 与参数应先通过 list_financial_sources/inspect_financial_source 确认。"
    ),
    parameters=object_schema(
        {
            "route_path": {"type": "string"},
            "params": {"type": "object", "description": "路由路径参数对象", "additionalProperties": True},
            "options": rss_options_schema(),
            "namespace": {"type": "string", "description": "来源命名空间，可留空"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
            "force": {"type": "boolean", "default": False, "description": "跳过缓存强制刷新"},
        },
        required=("route_path",),
    ),
    executor=read_financial_feed,
    category="sentiment",
)


__all__ = ["TOOL", "read_financial_feed"]
