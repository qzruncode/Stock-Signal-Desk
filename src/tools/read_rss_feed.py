"""``read_rss_feed`` tool."""

from typing import Any, Dict, Optional


def read_rss_feed(route_path: str, params: Optional[Dict[str, Any]] = None, namespace: Optional[str] = None, limit: int = 20, force: bool = False) -> Any:
    from api.v1.endpoints._rss_reader import read_feed
    return read_feed(route_path=route_path, params=params or {}, namespace=namespace, limit=limit, force=force)
