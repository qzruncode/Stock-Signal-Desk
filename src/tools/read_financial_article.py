"""Read one RSS article completely in deterministic text segments."""

from __future__ import annotations

from typing import Any

from src.tools._rss_agent import (
    array_value,
    endpoint_value,
    ensure_financial_route,
    html_text,
    object_value,
    rss_options_schema,
)
from src.tools.base import ToolSpec, object_schema


def read_financial_article(
    route_path: str,
    title: str,
    params: dict[str, Any] | str | None = None,
    options: dict[str, Any] | str | None = None,
    namespace: str = "",
    item_id: str = "",
    link: str = "",
    list_summary: str = "",
    list_content_html: str = "",
    list_image: str = "",
    published: str = "",
    author: str = "",
    tags: list[str] | str | None = None,
    attachments: list[dict[str, Any]] | str | None = None,
    offset: int = 0,
    max_chars: int = 6000,
    force: bool = False,
) -> dict[str, Any]:
    from api.v1.endpoints.rss import FeedItemDetailRequest, get_rss_feed_item_detail

    body = FeedItemDetailRequest(
        route_path=ensure_financial_route(route_path),
        params=object_value(params, "params"),
        options=object_value(options, "options"),
        namespace=str(namespace or "").strip() or None,
        item_id=str(item_id or ""),
        title=str(title or "").strip(),
        link=str(link or "").strip(),
        force=bool(force),
        content_html=str(list_content_html or ""),
        summary=str(list_summary or ""),
        image=str(list_image or ""),
        published=str(published or ""),
        author=str(author or ""),
        tags=[str(value) for value in array_value(tags, "tags")],
        attachments=[value for value in array_value(attachments, "attachments") if isinstance(value, dict)],
    )
    item = endpoint_value(lambda: get_rss_feed_item_detail(body))
    full_text = html_text(item.get("content_html") or item.get("summary") or "")
    start = max(0, int(offset or 0))
    size = max(500, min(int(max_chars or 6000), 12000))
    segment = full_text[start:start + size]
    next_offset = start + len(segment)
    has_more = next_offset < len(full_text)
    published = item.get("published") or None
    attachments = item.get("attachments") or []
    return {
        "success": bool(segment or attachments or item.get("image") or item.get("link")),
        "partial": has_more,
        "title": item.get("title") or body.title,
        "link": item.get("link") or body.link,
        "published": published,
        "author": item.get("author"),
        "tags": item.get("tags") or [],
        "image": item.get("image"),
        "attachments": attachments,
        "content_text": segment,
        "content_length": len(full_text),
        "offset": start,
        "next_offset": next_offset if has_more else None,
        "has_more": has_more,
        "route_path": body.route_path,
        "params": body.params,
        "options": body.options,
        "namespace": body.namespace,
        "data_time": published,
        "is_stale": None,
        "freshness_unknown": published is None,
        "errors": [],
        "warnings": ["正文较长，可使用 next_offset 继续读取"] if has_more else [],
    }


TOOL = ToolSpec(
    name="read_financial_article",
    description=(
        "读取一篇财经资讯全文及图片/音视频/PDF/文档附件。正文按字符分段返回；has_more=true 时必须用 next_offset 继续读取，"
        "直到完整覆盖。调用时应把上一步 Feed 返回的 summary/content_html/image/published/author/tags/attachments 分别传入"
        "对应 list_* 与元数据参数，保证上游全文抓取失败时不丢内容。"
    ),
    parameters=object_schema({
        "route_path": {"type": "string"},
        "title": {"type": "string"},
        "params": {"type": "object", "additionalProperties": True},
        "options": rss_options_schema(),
        "namespace": {"type": "string"},
        "item_id": {"type": "string"},
        "link": {"type": "string"},
        "list_summary": {"type": "string", "description": "Feed 列表已有摘要，用作全文抓取失败时的可靠回退"},
        "list_content_html": {"type": "string", "description": "Feed 列表已有 HTML 正文，必须原样传入以保留图片和 PDF 链接"},
        "list_image": {"type": "string", "description": "Feed 列表已有主图"},
        "published": {"type": "string", "description": "Feed 列表已有发布时间"},
        "author": {"type": "string", "description": "Feed 列表已有作者"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "attachments": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "max_chars": {"type": "integer", "minimum": 500, "maximum": 12000, "default": 6000},
        "force": {"type": "boolean", "default": False},
    }, required=("route_path", "title")),
    executor=read_financial_article,
    category="sentiment",
)


__all__ = ["TOOL", "read_financial_article"]
