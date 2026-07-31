"""Shared adapters for Agent-facing RSS capabilities."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException


def object_value(value: Any, field: str) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} 必须是 JSON 对象") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{field} 必须是对象")


def array_value(value: Any, field: str) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} 必须是 JSON 数组") from exc
        if isinstance(parsed, list):
            return parsed
    raise ValueError(f"{field} 必须是数组")


def ensure_rss_route(route_path: str) -> str:
    from api.v1.endpoints._rss_catalog import get_rss_catalog

    path = str(route_path or "").strip()
    routes = get_rss_catalog(force=False, scope="finance").get("routes") or []
    if not any(isinstance(route, dict) and route.get("route_path") == path for route in routes):
        raise ValueError(f"RSSHub 路由不在助手已筛选来源目录中: {path}")
    return path


def ensure_financial_route(route_path: str) -> str:
    """Compatibility alias for the shared filtered finance catalog."""
    return ensure_rss_route(route_path)


def rss_item_ref(
    *,
    route_path: str,
    params: dict[str, Any] | None,
    options: dict[str, Any] | None,
    namespace: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    from api.v1.endpoints._rss_text import item_content_hash

    content_hash = str(item.get("content_hash") or item_content_hash(item))
    return {
        "route_path": route_path,
        "params": dict(params or {}),
        "options": dict(options or {}),
        "namespace": namespace,
        "item_id": str(item.get("id") or ""),
        "title": str(item.get("title") or ""),
        "link": str(item.get("link") or ""),
        "content_hash": content_hash,
    }


def source_ref(route: dict[str, Any]) -> dict[str, Any]:
    params = route.get("params")
    if not isinstance(params, list):
        params = []
    return {
        "route_path": str(route.get("route_path") or ""),
        "namespace": str(route.get("namespace") or ""),
        "name": str(route.get("name") or ""),
        "categories": [str(value) for value in route.get("categories") or []],
        "parameters": params,
        "features": dict(route.get("features") or {}),
        "readiness": str(route.get("readiness") or "available"),
        "auto_recommended": bool(route.get("auto_recommended", True)),
        "readiness_reason": route.get("readiness_reason"),
    }


def rss_options_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "RSSHub 通用选项；只传用户明确要求的字段",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "filter": {"type": "string", "description": "标题、描述、作者和分类的正则过滤"},
            "filter_title": {"type": "string", "description": "标题正则过滤"},
            "filter_description": {"type": "string"},
            "filter_author": {"type": "string"},
            "filter_category": {"type": "string"},
            "filterout": {"type": "string", "description": "正则排除"},
            "filterout_title": {"type": "string"},
            "filterout_description": {"type": "string"},
            "filterout_author": {"type": "string"},
            "filterout_category": {"type": "string"},
            "filter_case_sensitive": {"type": "boolean"},
            "filter_time": {"type": "integer"},
            "sorted": {"type": "boolean", "description": "false 表示关闭按时间倒序"},
            "mode": {"type": "string", "enum": ["fulltext"]},
            "opencc": {"type": "string", "description": "繁简转换，如 t2s 或 s2t"},
            "brief": {"type": "integer"},
            "format": {"type": "string", "enum": ["rss", "atom", "json", "rss3"]},
        },
        "additionalProperties": True,
    }


def endpoint_value(call) -> dict[str, Any]:
    try:
        result = call()
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            raise ValueError(str(detail.get("message") or detail.get("error") or detail)) from exc
        raise ValueError(str(detail)) from exc
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    if not isinstance(result, dict):
        raise TypeError("RSS 能力必须返回对象")
    return result


def html_text(value: Any) -> str:
    from api.v1.endpoints._rss_fetch import _html_to_text

    return re.sub(r"\s+", " ", _html_to_text(str(value or ""))).strip()


def timestamp() -> str:
    return datetime.now().astimezone().isoformat()


__all__ = [
    "array_value",
    "endpoint_value",
    "ensure_financial_route",
    "ensure_rss_route",
    "html_text",
    "object_value",
    "rss_item_ref",
    "rss_options_schema",
    "source_ref",
    "timestamp",
]
