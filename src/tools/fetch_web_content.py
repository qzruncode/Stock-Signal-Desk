"""``fetch_web_content`` tool."""

from typing import Any


def fetch_web_content(url: str) -> Any:
    from src.search_service import fetch_url_content
    return {"url": url, "content": fetch_url_content(url)}
