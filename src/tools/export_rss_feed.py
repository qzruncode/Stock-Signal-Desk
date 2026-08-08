"""Prepare a text-only RSSHub feed export."""

from __future__ import annotations

from typing import Any

from src.tools._rss_agent import (
    ensure_rss_route,
    object_value,
)


_FORMATS = ("rss", "atom", "json", "rss3")


def export_rss_feed(
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
    clean_options = object_value(options, "options")
    clean_options["limit"] = max(1, min(int(limit or 30), 100))
    return {
        "success": True,
        "partial": False,
        "route_path": ensure_rss_route(route_path),
        "params": object_value(params, "params"),
        "options": clean_options,
        "namespace": str(namespace or "").strip(),
        "format": fmt,
        "available_formats": list(_FORMATS),
        "limit": clean_options["limit"],
        "download_ready": True,
        "text_only": True,
        "data_time": None,
        "is_stale": None,
        "freshness_unknown": True,
        "errors": [],
        "warnings": [],
    }


__all__ = ["export_rss_feed"]
