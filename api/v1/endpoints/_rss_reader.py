# -*- coding: utf-8 -*-
"""Reusable RSS read primitives shared by the HTTP endpoints (``rss.py``) and
the AI assistant tools (``tool_registry.py``).

These are thin, body-agnostic wrappers around the fetch/cache/fulltext
machinery so the assistant tools can read RSS feeds WITHOUT an HTTP detour —
they call the same code path the ``/api/v1/rss/feeds`` and ``/feeds/item``
endpoints use, reusing the hour-bucket cache and the fulltext re-fetch +
fallback logic.

Kept free of FastAPI/pydantic types on purpose: the assistant executor is a
plain Python callable running inside ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from api.v1.endpoints._rss_fetch import (
    _build_feed_url_generic,
    _fetch_rss_feed,
    _fetch_rss_feed_json,
    _html_to_text,
)
from api.v1.endpoints._rss_cache import (
    _rss_cache_key_generic,
    _cache_get,
    _cache_put,
)

logger = logging.getLogger(__name__)

# Cap how many items the assistant feed reader returns by default — RSSHub
# feeds can be long and the LLM only needs the freshest handful for analysis.
DEFAULT_FEED_LIMIT = 20
MAX_FEED_LIMIT = 50
# Fulltext detail re-fetch batch size. RSSHub applies filter_title *after*
# limit truncation, so this must be large enough to keep the target item in
# the filtered batch (see rss.py:get_rss_feed_item_detail for the rationale).
DETAIL_LIMIT = 30
# Caps for the assistant-facing item text — LLM context is finite.
ITEM_SUMMARY_CAP = 180
ITEM_TEXT_CAP = 2000


def read_feed(
    route_path: str,
    params: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    namespace: Optional[str] = None,
    limit: int = DEFAULT_FEED_LIMIT,
    force: bool = False,
    fallback_to_xml: bool = True,
) -> Dict[str, Any]:
    """Fetch a feed by FeedSpec and return a compact, assistant-friendly dict.

    Mirrors ``rss.py:get_rss_feeds_by_spec`` but returns a trimmed payload:
    each item keeps title/summary/link/published/source/image, dropping the
    heavy content_html/attachments (the LLM doesn't need raw HTML; the frontend
    UI fetches the full item separately via read_item when the user digs in).

    Returns ``{feed_title, feed_link, items, item_count, errors, _cached}``.
    """
    limit = max(1, min(MAX_FEED_LIMIT, int(limit)))
    effective_options = dict(options or {})
    # The app is a reader; never apply RSSHub's `brief` truncation even if a
    # caller forwards a stale option.
    effective_options.pop("brief", None)
    try:
        effective_limit = max(1, min(MAX_FEED_LIMIT, int(effective_options.get("limit") or limit)))
    except (TypeError, ValueError):
        effective_limit = limit
    # ``limit`` must reach RSSHub, not only trim the already-returned payload.
    # Otherwise the first cached 8-item read permanently starves later research
    # searches that ask for a 40-50 item candidate window.
    effective_options["limit"] = effective_limit

    try:
        feed_url = _build_feed_url_generic(route_path, params, effective_options, namespace=namespace)
    except ValueError as exc:
        return {
            "feed_title": "",
            "feed_link": "",
            "items": [],
            "item_count": 0,
            "errors": [str(exc)],
            "_cached": False,
        }

    cache_key = _rss_cache_key_generic(route_path, params or {}, effective_options)
    if not force:
        cached = _cache_get(cache_key)
        if cached and isinstance(cached, dict) and cached.get("items"):
            cached = dict(cached)
            cached["_cached"] = True
            return _trim_feed(cached, effective_limit)

    # Default to JSON (rich items); fall back to XML parse if JSON path fails.
    result = _fetch_rss_feed_json(feed_url, limit=effective_limit)
    if fallback_to_xml and result.get("errors") and not result.get("items"):
        logger.info("[RSS-reader] JSON fetch failed, falling back to XML: %s", result["errors"][0])
        result = _fetch_rss_feed(feed_url, limit=effective_limit)

    response = {
        "feed_title": result.get("feed_title", ""),
        "feed_link": result.get("feed_link", ""),
        "items": result.get("items", []),
        "item_count": len(result.get("items", []) or []),
        "errors": result.get("errors", []),
        "_cached": False,
    }
    if response["items"]:
        _cache_put(cache_key, result)
    return _trim_feed(response, effective_limit)


def _trim_feed(feed: Dict[str, Any], limit: int) -> Dict[str, Any]:
    """Drop heavy fields and cap summary length so the payload is LLM-friendly."""
    trimmed_items: List[Dict[str, Any]] = []
    for item in (feed.get("items") or [])[:limit]:
        summary = _html_to_text(item.get("summary") or item.get("content_html") or "")
        summary = re.sub(r"\s+", " ", summary).strip()
        if len(summary) > ITEM_SUMMARY_CAP:
            summary = summary[:ITEM_SUMMARY_CAP].rstrip() + "…"
        trimmed_items.append({
            "id": item.get("id", ""),
            "title": item.get("title", ""),
            "link": item.get("link", ""),
            "summary": summary,
            "published": item.get("published"),
            "author": item.get("author", ""),
            "image": item.get("image", ""),
            "source": _source_from_namespace(item) or item.get("author", ""),
        })
    return {
        "feed_title": feed.get("feed_title", ""),
        "feed_link": feed.get("feed_link", ""),
        "items": trimmed_items,
        "item_count": len(trimmed_items),
        "errors": feed.get("errors", []),
        "_cached": bool(feed.get("_cached")),
    }


def _source_from_namespace(item: Dict[str, Any]) -> str:
    """Best-effort source label from the item's RSS source id/label if present."""
    src = item.get("_rss_source_label") or item.get("_rss_source_id")
    return str(src) if src else ""


def read_item(
    route_path: str,
    params: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    namespace: Optional[str] = None,
    title: str = "",
    item_id: str = "",
    link: str = "",
    list_content_html: str = "",
    list_summary: str = "",
    list_image: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """Fetch a single item's full text via the fulltext re-fetch + fallback
    path used by ``rss.py:get_rss_feed_item_detail``.

    Returns ``{title, content_text, link, published, source, _fallback,
    _not_found, errors}``. ``content_text`` is plain text (HTML stripped),
    capped at ``ITEM_TEXT_CAP``. When the fulltext re-fetch comes back empty
    or worse than the list body, falls back to ``list_content_html``/``list_summary``
    (mirrors the endpoint behavior). ``_not_found=True`` when there is genuinely
    nothing to show.
    """
    detail_options = dict(options or {})
    detail_options.pop("brief", None)
    detail_options.update({"mode": "fulltext", "limit": DETAIL_LIMIT})
    if title:
        detail_options["filter_title"] = f"^{re.escape(title)}$"

    try:
        feed_url = _build_feed_url_generic(route_path, params, detail_options, namespace=namespace)
    except ValueError as exc:
        return {
            "title": title,
            "content_text": "",
            "link": link,
            "published": None,
            "source": "",
            "_fallback": False,
            "_not_found": True,
            "errors": [str(exc)],
        }

    cache_key = _rss_cache_key_generic(route_path, params or {}, detail_options)
    cached = None if force else _cache_get(cache_key)
    if cached and isinstance(cached, dict) and cached.get("items"):
        result = cached
    else:
        result = _fetch_rss_feed_json(feed_url, limit=DETAIL_LIMIT, timeout=45.0)
        if result.get("errors") and not result.get("items"):
            result = _fetch_rss_feed(feed_url, limit=DETAIL_LIMIT, timeout=30.0)
        if result.get("items"):
            _cache_put(cache_key, result)

    if result.get("errors") and not result.get("items"):
        fallback_html = (list_content_html or "").strip()
        fallback_summary = (list_summary or "").strip()
        fallback_image = (list_image or "").strip()
        if fallback_html or fallback_summary or fallback_image:
            return _pack_item_text(
                title=title or "",
                link=link,
                published=None,
                source="",
                content_text=_html_to_text(fallback_html or fallback_summary),
                fallback=True,
            )
        return {
            "title": title,
            "content_text": "",
            "link": link,
            "published": None,
            "source": "",
            "_fallback": False,
            "_not_found": True,
            "errors": result["errors"],
        }

    items = result.get("items") or []
    selected = next(
        (
            item for item in items
            if (item_id and item.get("id") == item_id)
            or (link and item.get("link") == link)
            or (title and item.get("title") == title)
        ),
        None,
    )

    if not selected:
        # Fulltext re-fetch empty / filter missed → fall back to the list body.
        fallback_html = (list_content_html or "").strip()
        fallback_summary = (list_summary or "").strip()
        fallback_image = (list_image or "").strip()
        if fallback_html or fallback_summary or fallback_image:
            content_text = _html_to_text(fallback_html or fallback_summary)
            return _pack_item_text(
                title=title or "",
                link=link,
                published=None,
                source="",
                content_text=content_text,
                fallback=True,
            )
        return {
            "title": title,
            "content_text": "",
            "link": link,
            "published": None,
            "source": "",
            "_fallback": False,
            "_not_found": True,
            "errors": [],
        }

    # Fulltext can produce a worse body than the list had — fall back to list.
    list_html = (list_content_html or "").strip()
    new_html = str(selected.get("content_html") or "").strip()
    if list_html and (
        not new_html
        or len(new_html) < max(80, int(len(list_html) * 0.6))
        or _fulltext_lost_content(list_html, new_html)
    ):
        selected = dict(selected)
        selected["content_html"] = list_content_html
        if not str(selected.get("summary") or "").strip() and list_summary:
            selected["summary"] = list_summary

    content_text = _html_to_text(
        selected.get("content_html") or selected.get("summary") or ""
    )
    return _pack_item_text(
        title=str(selected.get("title") or title or ""),
        link=str(selected.get("link") or link or ""),
        published=selected.get("published"),
        source=_source_from_namespace(selected) or str(selected.get("author") or ""),
        content_text=content_text,
        fallback=False,
    )


def _pack_item_text(
    *,
    title: str,
    link: str,
    published: Any,
    source: str,
    content_text: str,
    fallback: bool,
) -> Dict[str, Any]:
    text = re.sub(r"\s+", " ", content_text).strip()
    truncated = False
    if len(text) > ITEM_TEXT_CAP:
        text = text[:ITEM_TEXT_CAP].rstrip() + "…"
        truncated = True
    return {
        "title": title,
        "content_text": text,
        "link": link,
        "published": published,
        "source": source,
        "_fallback": fallback,
        "_truncated": truncated,
        "_not_found": False,
        "errors": [],
    }


def _fulltext_lost_content(list_html: str, new_html: str) -> bool:
    """Near-zero 8-gram overlap → the re-fetch captured page chrome / an
    anti-crawl payload rather than the article. Mirrors rss.py's same-named
    helper; duplicated here to keep the reader self-contained for the
    assistant path (no FastAPI import chain).
    """
    list_txt = _html_to_text(list_html)
    new_txt = _html_to_text(new_html)
    if not list_txt or not new_txt:
        return False
    if len(list_txt) < 16:
        return list_txt not in new_txt
    n = 8
    grams = [list_txt[i:i + n] for i in range(0, len(list_txt) - n + 1, n)]
    if not grams:
        return list_txt not in new_txt
    hit = sum(1 for g in grams if g in new_txt)
    return (hit / len(grams)) < 0.3
