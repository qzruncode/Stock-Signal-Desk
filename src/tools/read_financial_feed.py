"""Standalone compatibility helper for reading a curated RSS FeedSpec."""

from __future__ import annotations

from typing import Any

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
__all__ = ["read_financial_feed"]
