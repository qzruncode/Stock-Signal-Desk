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


def ensure_financial_route(route_path: str) -> str:
    from api.v1.endpoints._rss_catalog import get_rss_catalog

    path = str(route_path or "").strip()
    routes = get_rss_catalog(force=False).get("routes") or []
    if not any(isinstance(route, dict) and route.get("route_path") == path for route in routes):
        raise ValueError(f"资讯源路由不在已维护的 47 个股市资讯源中: {path}")
    return path


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
            "filter_case_sensitive": {"type": "boolean"},
            "filter_time": {"type": "integer"},
            "sorted": {"type": "boolean", "description": "false 表示关闭按时间倒序"},
            "mode": {"type": "string", "enum": ["fulltext"]},
            "opencc": {"type": "string", "description": "繁简转换，如 t2s 或 s2t"},
            "brief": {"type": "integer"},
            "tgiv": {"type": "string"},
            "scihub": {"type": "string"},
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
    "html_text",
    "object_value",
    "rss_options_schema",
    "timestamp",
]
