"""Discover routes from the existing filtered RSSHub finance catalog."""

from __future__ import annotations

import re
from typing import Any

from src.tools._rss_agent import source_ref
from src.tools.base import ToolSpec, object_schema, report_tool_progress


_ASCII_WORD_RE = re.compile(r"[a-z0-9]+", re.I)


def _tokens(value: str) -> set[str]:
    normalized = str(value or "").lower()
    # A single ASCII letter (for example the "A" in "A股公告") occurs in
    # route URLs and namespace metadata too often to express relevance. Chinese
    # matching is intentionally based on two-character terms for the same reason.
    basic = {
        token
        for token in _ASCII_WORD_RE.findall(normalized)
        if len(token) >= 2 or token.isdigit()
    }
    cjk = "".join(ch for ch in normalized if "\u3400" <= ch <= "\u9fff")
    basic.update(
        cjk[index : index + 2]
        for index in range(max(0, len(cjk) - 1))
    )
    return {token for token in basic if token}


def _normalize_category_filter(
    *,
    category: str,
    query: str,
    routes: list[dict[str, Any]],
) -> tuple[str, str, str | None]:
    """Keep taxonomy filters separate from semantic source-use descriptions."""
    requested = str(category or "").strip()
    if not requested:
        return "", str(query or "").strip(), None
    known_categories = {
        str(value).strip().casefold()
        for route in routes
        for value in route.get("categories") or []
        if str(value).strip()
    }
    normalized = requested.casefold()
    if normalized in known_categories:
        return normalized, str(query or "").strip(), None

    query_text = str(query or "").strip()
    if requested.casefold() not in query_text.casefold():
        query_text = " ".join(value for value in (query_text, requested) if value)
    return (
        "",
        query_text,
        (
            f"已忽略不是 RSSHub 分类的 category={requested!r}；"
            "已将其按来源用途并入 query。"
        ),
    )


def _relevance(route: dict[str, Any], query: str) -> float:
    if not query:
        return 0.5 if route.get("auto_recommended") else 0.0
    searchable = " ".join(
        str(route.get(key) or "")
        for key in (
            "route_path",
            "name",
            "namespace",
            "namespace_name",
            "description",
            "categories",
        )
    ).lower()
    lowered = query.lower()
    score = 8.0 if lowered and lowered in searchable else 0.0
    query_tokens = _tokens(lowered)
    if query_tokens:
        haystack = _tokens(searchable)
        score += 5.0 * len(query_tokens & haystack) / len(query_tokens)
    # Recommendation is only a tie-breaker after the source has matched the
    # requested information need. It must never turn an unrelated source into
    # a search hit.
    if score <= 0:
        return 0.0
    if route.get("auto_recommended"):
        score += 0.5
    if route.get("readiness") == "unavailable":
        score -= 3.0
    return score


def discover_rss_sources(
    query: str = "",
    namespace: str = "",
    category: str = "",
    recommended_only: bool = False,
    force: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    from api.v1.endpoints._rss_catalog import get_rss_catalog

    report_tool_progress("正在选择 RSSHub 来源", progress=15)
    catalog = get_rss_catalog(force=bool(force), scope="finance")
    routes = [
        route
        for route in catalog.get("routes") or []
        if isinstance(route, dict)
    ]
    namespace_query = str(namespace or "").strip().lower()
    category_query, query_text, category_warning = _normalize_category_filter(
        category=category,
        query=query,
        routes=routes,
    )
    matched: list[tuple[float, dict[str, Any]]] = []
    for route in routes:
        if recommended_only and not bool(route.get("auto_recommended")):
            continue
        namespace_text = (
            f"{route.get('namespace', '')} {route.get('namespace_name', '')}"
        ).lower()
        if namespace_query and namespace_query not in namespace_text:
            continue
        categories = [
            str(value).lower() for value in route.get("categories") or []
        ]
        if category_query and category_query not in categories:
            continue
        score = _relevance(route, query_text)
        if query_text and score <= 0:
            continue
        matched.append((score, route))
    matched.sort(
        key=lambda value: (
            -value[0],
            str(value[1].get("namespace") or ""),
            str(value[1].get("route_path") or ""),
        )
    )
    start = max(0, int(offset or 0))
    bounded = max(1, min(int(limit or 50), 100))
    items = [
        {
            **route,
            "source_ref": source_ref(route),
            "relevance_score": round(score, 4),
        }
        for score, route in matched[start : start + bounded]
    ]
    report_tool_progress("RSSHub 来源选择完成", progress=100)
    error = catalog.get("_error")
    data_time = catalog.get("_fetched_at")
    empty_page = bool(matched) and start >= len(matched)
    no_match = not matched and not error and not empty_page
    no_match_error = (
        "已筛选的 RSSHub 财经来源中没有匹配项，请调整来源用途、命名空间或分类条件。"
        if no_match
        else None
    )
    failures = [str(error)] if error else []
    if no_match_error:
        failures.append(no_match_error)
    return {
        # A valid page past the end is not a failed lookup. A genuine zero
        # match, however, must remain visible to Workflow coverage instead of
        # being reported as a completed source-discovery task.
        "success": bool(items) or empty_page,
        "partial": bool(error) and bool(items),
        "items": items,
        "catalog_count": len(routes),
        "matched_count": len(matched),
        "returned_count": len(items),
        "offset": start,
        "next_offset": (
            start + len(items)
            if start + len(items) < len(matched)
            else None
        ),
        "has_more": start + len(items) < len(matched),
        "scope": "filtered_finance_routes",
        "filters": {
            "query": query_text,
            "namespace": str(namespace or "").strip(),
            "category": category_query,
            "requested_category": str(category or "").strip() or None,
            "recommended_only": bool(recommended_only),
        },
        "coverage": {
            "planned_sources": len(items),
            "attempted_sources": len(items),
            "successful_sources": len(items),
            "item_count": len(items),
            "text_documents_found": 0,
            "text_documents_extracted": 0,
            "discarded_non_text": 0,
            "failures": failures,
        },
        "data_time": data_time,
        "is_stale": bool(catalog.get("_stale")) if data_time else None,
        "freshness_unknown": data_time is None,
        "errors": failures if not items else [],
        "warnings": [
            *([category_warning] if category_warning else []),
            *([str(error)] if error and items else []),
        ],
    }


TOOL = ToolSpec(
    name="discover_rss_sources",
    description=(
        "动态发现助手既有过滤目录中的 RSSHub 财经来源。可按自然语言用途、命名空间、"
        "分类和状态筛选；返回稳定 source_ref、路径参数、健康状态和能力元数据。"
    ),
    parameters=object_schema(
        {
            "query": {
                "type": "string",
                "description": "来源用途或主题描述；留空浏览已筛选财经来源",
            },
            "namespace": {"type": "string"},
            "category": {
                "type": "string",
                "enum": ["finance"],
                "description": (
                    "RSSHub 路由元数据分类；当前目录仅支持 finance。"
                    "来源用途请使用 query。"
                ),
            },
            "recommended_only": {"type": "boolean", "default": False},
            "force": {"type": "boolean", "default": False},
            "offset": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 50,
            },
        }
    ),
    executor=discover_rss_sources,
    category="sentiment",
)


__all__ = ["TOOL", "discover_rss_sources"]
