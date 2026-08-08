"""Lower-level adapter for reading one RSSHub route.

This module is intentionally not registered as a model tool. Agent-visible
``read_rss_*`` tools bind a fixed route before calling this adapter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from src.tools._rss_agent import (
    endpoint_value,
    ensure_rss_route,
    object_value,
    rss_item_ref,
)
from src.tools.base import report_tool_progress


def _source_published_time(value: Any) -> tuple[float, str] | None:
    """Return a comparable source-published timestamp, never a fetch time.

    RSS publishers use both ISO-8601 and RFC 2822 dates.  The enclosing Feed
    response's ``_fetched_at`` is deliberately excluded: it describes our
    transport/cache activity and must not become evidence that an article was
    published at that time.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    # A few feeds omit a timezone.  Preserve that source value in the result;
    # the UTC replacement is only a stable ordering key for this collection.
    comparable = (
        parsed.timestamp()
        if parsed.tzinfo
        else parsed.replace(tzinfo=timezone.utc).timestamp()
    )
    return comparable, parsed.isoformat()


def _latest_source_published_time(items: list[dict[str, Any]]) -> str | None:
    candidates = [
        parsed
        for item in items
        if isinstance(item, dict)
        if (parsed := _source_published_time(item.get("published"))) is not None
    ]
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def read_rss_feed(
    route_path: str,
    params: dict[str, Any] | str | None = None,
    options: dict[str, Any] | str | None = None,
    namespace: str = "",
    limit: int = 30,
    force: bool = False,
    *,
    validate_catalog: bool = True,
) -> dict[str, Any]:
    from api.v1.endpoints._rss_text import normalize_text_item
    from api.v1.endpoints.rss import FeedSpecRequest, get_rss_feeds_by_spec

    report_tool_progress("正在读取 Feed", progress=15)
    path = (
        ensure_rss_route(route_path)
        if validate_catalog
        else str(route_path or "").strip()
    )
    if not path.startswith("/"):
        raise ValueError("RSSHub 路由必须以 / 开头")
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
    data_time = _latest_source_published_time(items)
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
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": (
            None
            if data_time
            else "RSS Feed 未返回可解析的条目发布时间；_fetched_at 仅表示本服务获取或缓存刷新时间。"
        ),
        # A publication time alone cannot establish that an arbitrary news
        # feed is stale; do not turn cache age into a source-data judgement.
        "is_stale": None,
        "freshness_unknown": data_time is None,
        "errors": errors,
        "warnings": errors if items else [],
    }

__all__ = ["read_rss_feed"]
