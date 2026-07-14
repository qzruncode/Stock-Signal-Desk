"""Shared search-service response serializer."""

from typing import Any


def serialize_search_response(response: Any) -> dict[str, Any]:
    return {
        "query": response.query,
        "provider": response.provider,
        "success": response.success,
        "error_message": response.error_message,
        "search_time": response.search_time,
        "results": [
            {"title": item.title, "snippet": item.snippet, "url": item.url, "source": item.source, "published_date": item.published_date}
            for item in response.results
        ],
    }
