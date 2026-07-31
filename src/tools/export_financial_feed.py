"""Prepare a FeedSpec for user-triggered download in the Assistant UI."""

from __future__ import annotations

from typing import Any

from src.tools._rss_agent import ensure_financial_route, object_value, rss_options_schema
from src.tools.base import ToolSpec, object_schema

_FORMATS = ("rss", "atom", "json", "rss3")


def export_financial_feed(
    route_path: str,
    params: dict[str, Any] | str | None = None,
    options: dict[str, Any] | str | None = None,
    namespace: str = "",
    format: str = "rss",
    limit: int = 30,
) -> dict[str, Any]:
    fmt = str(format or "rss").lower()
    if fmt not in _FORMATS:
        raise ValueError(f"不支持的导出格式: {fmt}")
    path = ensure_financial_route(route_path)
    clean_options = object_value(options, "options")
    clean_options["limit"] = max(1, min(int(limit or 30), 100))
    return {
        "success": True,
        "partial": False,
        "route_path": path,
        "params": object_value(params, "params"),
        "options": clean_options,
        "namespace": str(namespace or "").strip(),
        "format": fmt,
        "available_formats": list(_FORMATS),
        "limit": clean_options["limit"],
        "download_ready": True,
        "data_time": None,
        "is_stale": None,
        "freshness_unknown": True,
        "errors": [],
        "warnings": [],
    }


TOOL = ToolSpec(
    name="export_financial_feed",
    description=(
        "准备下载指定财经 Feed，支持 RSS 2.0、Atom、JSON Feed、RSS3。工具返回后，助手卡片会提供下载按钮；"
        "只有用户明确要求导出或下载时调用。"
    ),
    parameters=object_schema(
        {
            "route_path": {"type": "string"},
            "params": {"type": "object", "additionalProperties": True},
            "options": rss_options_schema(),
            "namespace": {"type": "string"},
            "format": {"type": "string", "enum": list(_FORMATS), "default": "rss"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
        },
        required=("route_path",),
    ),
    executor=export_financial_feed,
    category="action",
)


__all__ = ["TOOL", "export_financial_feed"]
