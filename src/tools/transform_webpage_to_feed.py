"""Turn an arbitrary webpage into a readable feed from the Agent."""

from __future__ import annotations

from typing import Any

from src.tools._rss_agent import endpoint_value, object_value, rss_options_schema
from src.tools.base import ToolSpec, object_schema


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


TOOL = ToolSpec(
    name="transform_webpage_to_feed",
    description=(
        "把任意网页通过 CSS 选择器转换成 RSS Feed 并预览结果。适用于用户提供网页并要求持续读取其列表内容；"
        "需要 RSSHub 允许目标域名。"
    ),
    parameters=object_schema(
        {
            "url": {"type": "string"},
            "item": {"type": "string", "default": "html", "description": "每条内容的 CSS 选择器"},
            "title": {"type": "string", "description": "Feed 标题，可留空"},
            "item_title": {"type": "string"},
            "item_title_attr": {"type": "string", "description": "从标题元素读取的属性名，如 title"},
            "item_link": {"type": "string"},
            "item_link_attr": {"type": "string", "description": "从链接元素读取的属性名，通常为 href"},
            "item_desc": {"type": "string"},
            "item_desc_attr": {"type": "string", "description": "从描述元素读取的属性名"},
            "item_pubdate": {"type": "string"},
            "item_pubdate_attr": {"type": "string", "description": "从时间元素读取的属性名，如 datetime"},
            "item_content": {"type": "string", "description": "二次抓取正文的 CSS 选择器"},
            "encoding": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "options": rss_options_schema(),
        },
        required=("url",),
    ),
    executor=transform_webpage_to_feed,
    category="sentiment",
)


__all__ = ["TOOL", "transform_webpage_to_feed"]
