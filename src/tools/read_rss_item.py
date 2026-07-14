"""``read_rss_item`` tool."""

from typing import Any, Dict, Optional


def read_rss_item(route_path: str, title: str, params: Optional[Dict[str, Any]] = None, namespace: Optional[str] = None, item_id: Optional[str] = None, link: Optional[str] = None, summary: Optional[str] = None, force: bool = False) -> Any:
    from api.v1.endpoints._rss_reader import read_item
    return read_item(route_path=route_path, params=params or {}, namespace=namespace, title=title, item_id=item_id or "", link=link or "", list_summary=summary or "", force=force)
