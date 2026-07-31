"""Prepare a FeedSpec for user-triggered download in the Assistant UI."""

from __future__ import annotations

from typing import Any

from src.tools._rss_agent import rss_options_schema
from src.tools.base import ToolSpec, object_schema
from src.tools.export_rss_feed import export_rss_feed

_FORMATS = ("rss", "atom", "json", "rss3")


def export_financial_feed(
    route_path: str,
    params: dict[str, Any] | str | None = None,
    options: dict[str, Any] | str | None = None,
    namespace: str = "",
    format: str = "rss",
    limit: int = 30,
) -> dict[str, Any]:
    return export_rss_feed(
        route_path=route_path,
        params=params,
        options=options,
        namespace=namespace,
        format=format,
        limit=limit,
    )


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
