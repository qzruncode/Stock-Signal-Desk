"""Turn an arbitrary webpage into a readable feed from the Agent."""

from __future__ import annotations

from typing import Any

from src.tools._rss_agent import endpoint_value, object_value


def transform_webpage_to_feed(
    url: str,
    item: str = "html",
    title: str = "",
    item_title: str = "",
    item_title_attr: str = "",
    item_link: str = "",
    item_link_attr: str = "",
    item_desc: str = "",
    item_desc_attr: str = "",
    item_pubdate: str = "",
    item_pubdate_attr: str = "",
    item_content: str = "",
    encoding: str = "",
    limit: int = 20,
    options: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    from api.v1.endpoints.rss import HtmlTransformRequest, transform_html

    clean_options = object_value(options, "options")
    body = HtmlTransformRequest(
        url=str(url or "").strip(),
        title=str(title or "").strip() or None,
        item=str(item or "html").strip() or "html",
        item_title=str(item_title or "").strip() or None,
        item_title_attr=str(item_title_attr or "").strip() or None,
        item_link=str(item_link or "").strip() or None,
        item_link_attr=str(item_link_attr or "").strip() or None,
        item_desc=str(item_desc or "").strip() or None,
        item_desc_attr=str(item_desc_attr or "").strip() or None,
        item_pubdate=str(item_pubdate or "").strip() or None,
        item_pubdate_attr=str(item_pubdate_attr or "").strip() or None,
        item_content=str(item_content or "").strip() or None,
        encoding=str(encoding or "").strip() or None,
        limit=max(1, min(int(limit or 20), 100)),
        options=clean_options,
    )
    result = endpoint_value(lambda: transform_html(body))
    items = result.get("items") or []
    data_time = result.get("_fetched_at")
    return {
        **result,
        "success": bool(items),
        "partial": bool(result.get("errors")) and bool(items),
        "item_count": len(items),
        "params": {
            "url": body.url,
            "title": body.title,
            "item": body.item,
            "itemTitle": body.item_title,
            "itemTitleAttr": body.item_title_attr,
            "itemLink": body.item_link,
            "itemLinkAttr": body.item_link_attr,
            "itemDesc": body.item_desc,
            "itemDescAttr": body.item_desc_attr,
            "itemPubDate": body.item_pubdate,
            "itemPubDateAttr": body.item_pubdate_attr,
            "itemContent": body.item_content,
            "encoding": body.encoding,
        },
        "options": clean_options,
        "data_time": data_time,
        "is_stale": False if data_time else None,
        "freshness_unknown": data_time is None,
        "errors": [str(error) for error in result.get("errors") or []],
        "warnings": [],
    }


__all__ = ["transform_webpage_to_feed"]
