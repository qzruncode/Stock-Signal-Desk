# -*- coding: utf-8 -*-
"""RSS feed URL building and HTTP fetching."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import feedparser
import requests

from src.config import Config
from api.v1.endpoints._rss_routes import RSSHUB_ROUTES

logger = logging.getLogger(__name__)


def _stock_code_to_rsshub_id(code: str) -> str:
    """将 6 位股票代码转为 RSSHub/雪球格式 (SH600519, SZ000002, BJ430001)。"""
    c = code.strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if c.startswith(prefix):
            c = c[len(prefix):]
            break
    if "." in c:
        c = c.split(".")[0]
    if not c.isdigit() or len(c) != 6:
        return code
    if c[0] in ("6", "9"):
        return f"SH{c}"
    elif c[0] in ("0", "2", "3"):
        return f"SZ{c}"
    else:
        return f"BJ{c}"


def _normalize_stock_code(code: str) -> str:
    """将股票代码统一为 6 位纯数字，供交易所公告路由使用。"""
    raw_code = code.strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if raw_code.startswith(prefix):
            raw_code = raw_code[len(prefix):]
            break
    if "." in raw_code:
        raw_code = raw_code.split(".")[0]
    return raw_code


def _build_feed_url(source: str, stock_code: Optional[str] = None,
                    type: Optional[str] = None,
                    category: Optional[str] = None,
                    keyword: Optional[str] = None,
                    uid: Optional[str] = None) -> str:
    """根据 source 和参数构建 RSSHub feed URL。"""
    route_info = RSSHUB_ROUTES.get(source)
    if not route_info:
        raise ValueError(f"未知的 RSS 源: {source}")

    base_url = Config.get_instance().rsshub_base_url.rstrip("/")
    path: str = route_info["path"]

    if "{id}" in path:
        if not stock_code:
            raise ValueError(f"源 {source} 需要提供 stock_code 参数")
        rsshub_id = _stock_code_to_rsshub_id(stock_code)
        path = path.replace("{id}", rsshub_id)

    if "{type}" in path:
        t = type or route_info.get("default_type", "news")
        path = path.replace("{type}", t)

    if "{category}" in path:
        cat = category or route_info.get("default_category", "")
        path = path.replace("{category}", cat)

    if "{keyword}" in path:
        if not keyword:
            raise ValueError(f"源 {source} 需要提供 keyword 参数")
        path = path.replace("{keyword}", quote(keyword.strip(), safe=""))

    if "{uid}" in path:
        if not uid:
            raise ValueError(f"源 {source} 需要提供 uid 参数")
        path = path.replace("{uid}", quote(uid.strip(), safe=""))

    if "{query}" in path:
        if not stock_code:
            raise ValueError(f"源 {source} 需要提供 stock_code 参数")
        raw_code = _normalize_stock_code(stock_code)
        path = path.replace("{query}", f"stock={raw_code}")

    return f"{base_url}{path}"


def _fetch_rss_feed(url: str, limit: int = 20, timeout: float = 15.0) -> dict:
    """抓取并解析 RSS/Atom feed。"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        }
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(f"[RSS] 请求失败 {url}: {exc}")
        return {"items": [], "errors": [f"请求失败: {exc}"]}

    feed = feedparser.parse(resp.text)

    if not feed.entries and feed.bozo and not getattr(feed, "feed", None):
        error_msg = getattr(feed, "bozo_exception", "未知解析错误")
        logger.warning(f"[RSS] 解析失败 {url}: {error_msg}")
        return {"items": [], "errors": [f"解析失败: {error_msg}"]}

    items = []
    for entry in feed.entries[:limit]:
        summary = ""
        if hasattr(entry, "summary"):
            summary = entry.summary or ""
        elif hasattr(entry, "description"):
            summary = entry.description or ""
        summary = re.sub(r"<[^>]+>", "", summary).strip()
        if len(summary) > 500:
            summary = summary[:497] + "..."

        published = None
        for attr in ("published", "updated", "created"):
            val = getattr(entry, attr, None)
            if val:
                try:
                    parsed = getattr(entry, f"{attr}_parsed", None)
                    if parsed:
                        from time import mktime
                        published = datetime.fromtimestamp(mktime(parsed)).isoformat()
                    else:
                        published = str(val)
                except Exception:
                    published = str(val)
                break

        tags = []
        if hasattr(entry, "tags"):
            tags = [t.get("term", "") for t in entry.tags if t.get("term")]

        items.append({
            "title": getattr(entry, "title", ""),
            "link": getattr(entry, "link", ""),
            "summary": summary,
            "published": published,
            "author": getattr(entry, "author", ""),
            "tags": tags,
        })

    feed_title = ""
    feed_link = ""
    if hasattr(feed, "feed"):
        feed_title = getattr(feed.feed, "title", "")
        feed_link = getattr(feed.feed, "link", "")

    return {
        "feed_title": feed_title,
        "feed_link": feed_link,
        "items": items,
        "errors": [],
    }