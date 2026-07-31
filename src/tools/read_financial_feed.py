"""Read an arbitrary curated RSS FeedSpec through the Agent."""

from __future__ import annotations

from typing import Any

from src.tools._rss_agent import rss_options_schema
from src.tools.base import ToolSpec, object_schema
from src.tools.read_rss_feed import read_rss_feed


def read_financial_feed(
    route_path: str,
    params: dict[str, Any] | str | None = None,
    options: dict[str, Any] | str | None = None,
    namespace: str = "",
    limit: int = 30,
    force: bool = False,
) -> dict[str, Any]:
    return read_rss_feed(
        route_path=route_path,
        params=params,
        options=options,
        namespace=namespace,
        limit=limit,
        force=force,
    )


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
