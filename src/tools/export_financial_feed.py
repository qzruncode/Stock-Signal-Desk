"""Prepare a FeedSpec for user-triggered download in the Assistant UI."""

from __future__ import annotations

from typing import Any

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
__all__ = ["export_financial_feed"]
