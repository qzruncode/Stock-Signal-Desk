# -*- coding: utf-8 -*-
"""富途牛牛话题列表代理 — 供 ``/futunn/topic/:id`` 路由选参。

RSSHub 的 ``/futunn/topic/:id`` 路由需要一个话题编号 ``id``，但上游元数据只写了
``Topic ID, can be found in URL``，没有可选列表，用户只能去富途网站
``news.futunn.com/news-topics/:id/`` 的 URL 里找编号。

富途本身有公开的话题列表接口
``https://news.futunn.com/news-site-api/main/get-topics-list?pageSize=48&seqMark=<seqMark>``
（无需签名/cookie，分页返回 ``hasMore``/``seqMark`` 游标 + ``list[]``，每条含
``idx``/``title``/``detail``/``timestamp``/``subscribed``）。RSSHub 该路由 handler 内部
正是用这个接口按 ``idx`` 反查话题标题/描述（见 ``services/rsshub/.../futunn/topic.ts``
的 ``getTopic``），这里复用它翻页累积全量话题、按订阅数降序输出，让前端按话题名
点选后填入 ``idx``。

鲁棒性与 ``_cls_subjects.py`` / ``_gelonghui_subjects.py`` 一致：抓取失败时回退 stale
缓存，都没有则返回带 ``_error`` 的空结果，前端据此降级（回退成普通输入框 + 手动输入）。
话题列表只覆盖热门话题，不保证完整——前端提供"手动输入 id"兜底（见 ``FutunnTopicPicker``）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from api.v1.endpoints._rss_cache import _cache_get, _cache_put

logger = logging.getLogger(__name__)

BLOB_CACHE_KEY = "rss:futunn:topics:blob:v1"
BLOB_TTL_SECONDS = 6 * 3600  # 话题列表变化不频繁
FETCH_TIMEOUT = 20.0
ROOT_URL = "https://news.futunn.com"
TOPICS_LIST_URL = f"{ROOT_URL}/news-site-api/main/get-topics-list"

# 单次最多返回给前端的条数（按订阅数降序截断）。
MAX_RETURN = 100
# 单次抓取最多翻页数（每页 pageSize 条），防止上游异常导致无限翻页。
MAX_PAGES = 20
PAGE_SIZE = 48


def _is_stale_ok(blob: Optional[dict], ttl: int = BLOB_TTL_SECONDS) -> bool:
    """True if a cached blob exists and is within TTL (or fresh enough to serve stale)."""
    if not isinstance(blob, dict):
        return False
    fetched = blob.get("_fetched_at")
    if not fetched:
        return True
    try:
        ts = datetime.fromisoformat(fetched)
    except Exception:
        return True
    return (datetime.now() - ts) < timedelta(seconds=ttl)


def _fetch_page(seq_mark: str = "") -> Optional[Dict[str, Any]]:
    """抓取一页话题列表，返回上游 ``data.data.data``（含 hasMore/seqMark/list）。

    抓取失败返回 ``None``（调用方按页累积，单页失败不致命）。
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
    }
    try:
        resp = requests.get(
            TOPICS_LIST_URL,
            params={"pageSize": PAGE_SIZE, "seqMark": seq_mark},
            headers=headers,
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("[RSS] futunn topics fetch failed seqMark=%s: %s", seq_mark, exc)
        return None

    # 上游结构：{code, message, data: {code, message, data: {hasMore, seqMark, list}}}
    inner = data.get("data") if isinstance(data, dict) else None
    if isinstance(inner, dict):
        inner = inner.get("data")
    if not isinstance(inner, dict):
        return None
    return inner


def _normalize_topic(item: Any) -> Optional[Dict[str, Any]]:
    """Map a raw futunn topic to the slim shape the frontend needs."""
    if not isinstance(item, dict):
        return None
    idx = item.get("idx")
    if idx is None:
        return None
    return {
        "topicId": str(idx),
        "title": str(item.get("title") or "").strip(),
        "detail": str(item.get("detail") or "").strip(),
        "subscribed": int(item.get("subscribed") or 0),
        "timestamp": int(item.get("timestamp") or 0),
        "link": str(item.get("url") or f"{ROOT_URL}/news-topics/{idx}/"),
    }


def _get_topics_raw(force: bool = False) -> Dict[str, Any]:
    """Return the raw topic list + cache metadata.

    Response shape: ``{"topics": [...], "_fetched_at", "_cached", "_stale", "_error"}``.
    On fetch failure, serves the stale cached blob if available.
    """
    cached = _cache_get(BLOB_CACHE_KEY)
    if not force and _is_stale_ok(cached):
        cached["_cached"] = True
        cached["_stale"] = False
        cached["_error"] = None
        return cached

    harvested: List[Dict[str, Any]] = []
    seq_mark = ""
    for _page in range(MAX_PAGES):
        page = _fetch_page(seq_mark)
        if page is None:
            break  # 抓取失败，停止翻页
        raw_list = page.get("list")
        if isinstance(raw_list, list):
            for item in raw_list:
                norm = _normalize_topic(item)
                if norm is not None:
                    harvested.append(norm)
        has_more = page.get("hasMore")
        seq_mark = page.get("seqMark") or ""
        # hasMore === 1 表示还有下一页；非 1（0 或缺失）则结束。
        if has_more != 1 or not seq_mark:
            break

    if not harvested:
        if isinstance(cached, dict) and cached.get("topics"):
            logger.info("[RSS] futunn unavailable; serving stale topics blob.")
            cached["_cached"] = True
            cached["_stale"] = True
            cached["_error"] = "富途暂不可用，返回上次缓存的话题数据"
            return cached
        return {
            "topics": [],
            "_fetched_at": None,
            "_cached": False,
            "_stale": False,
            "_error": "富途不可用且无缓存数据",
        }

    # 去重（按 topicId）后按订阅数降序、时间降序。
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in harvested:
        tid = item["topicId"]
        if tid in by_id:
            by_id[tid]["subscribed"] = max(by_id[tid]["subscribed"], item["subscribed"])
            continue
        by_id[tid] = item
    topics = list(by_id.values())
    topics.sort(key=lambda t: (t.get("subscribed", 0), t.get("timestamp", 0)), reverse=True)

    blob = {
        "topics": topics,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        "_stale": False,
        "_error": None,
    }
    try:
        _cache_put(BLOB_CACHE_KEY, blob)
    except Exception as exc:
        logger.warning("[RSS] futunn topics cache write failed: %s", exc)
    return blob


def get_futunn_topics(force: bool = False, keyword: Optional[str] = None) -> Dict[str, Any]:
    """Filtered + capped topic list for the frontend param picker.

    ``keyword`` does a case-insensitive substring match on title/detail. The
    returned list is capped at ``MAX_RETURN`` (already subscribed-desc from the
    cache), and ``total`` reflects the count after filtering (before the cap).
    """
    blob = _get_topics_raw(force=force)
    topics: List[Dict[str, Any]] = blob.get("topics") or []

    kw = (keyword or "").strip().lower()
    if kw:
        topics = [
            t for t in topics
            if kw in (str(t.get("title") or "").lower() + " " + str(t.get("detail") or "").lower())
        ]

    return {
        "topics": topics[:MAX_RETURN],
        "total": len(topics),
        "_fetched_at": blob.get("_fetched_at"),
        "_cached": bool(blob.get("_cached")),
        "_stale": bool(blob.get("_stale")),
        "_error": blob.get("_error"),
    }
