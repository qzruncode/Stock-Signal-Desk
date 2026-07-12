# -*- coding: utf-8 -*-
"""格隆汇主题列表代理 — 供 ``/gelonghui/subject/:id`` 路由选参。

RSSHub 的 ``/gelonghui/subject/:id`` 路由需要一个主题编号 ``id``，但上游元数据
只写了"主题编号，可在主题页 URL 中找到"，没有可选列表。格隆汇本身有公开的主题
列表 API（``https://www.gelonghui.com/api/subjects``，返回约 850 个主题，每个含
``subjectId``/``name``/``followCount``/``summary``/``link``），这里代理它并做 6h
缓存，让前端能按主题名搜索、点选后填入 ``subjectId``。

鲁棒性与 ``_rss_namespace.py`` 一致：抓取失败时回退到 stale 缓存，都没有则返回
带 ``_error`` 的空结果，前端据此降级（回退成普通输入框）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from api.v1.endpoints._rss_cache import _cache_get, _cache_put

logger = logging.getLogger(__name__)

BLOB_CACHE_KEY = "rss:gelonghui:subjects:blob:v1"
BLOB_TTL_SECONDS = 6 * 3600  # 主题列表变化不频繁
FETCH_TIMEOUT = 20.0
GELONGHUI_SUBJECTS_URL = "https://www.gelonghui.com/api/subjects"

# 单次最多返回给前端的条数（按关注数降序截断，避免 851 条全量传输）。
MAX_RETURN = 100


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


def _fetch_json(url: str) -> Optional[dict]:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        }
        resp = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("[RSS] gelonghui subjects fetch failed %s: %s", url, exc)
        return None


def _normalize_subject(item: Any) -> Optional[Dict[str, Any]]:
    """Map a raw gelonghui subject to the slim shape the frontend needs."""
    if not isinstance(item, dict):
        return None
    subject_id = item.get("subjectId")
    if subject_id is None:
        return None
    return {
        "subjectId": subject_id,
        "name": str(item.get("name") or "").strip(),
        "followCount": int(item.get("followCount") or 0),
        "summary": str(item.get("summary") or "").strip(),
        "link": str(item.get("link") or f"https://www.gelonghui.com/subject/{subjectId}"),
    }


def _get_subjects_raw(force: bool = False) -> Dict[str, Any]:
    """Return the raw subject list + cache metadata.

    Response shape: ``{"subjects": [...], "_fetched_at", "_cached", "_stale", "_error"}``.
    On fetch failure, serves the stale cached blob if available.
    """
    cached = _cache_get(BLOB_CACHE_KEY)
    if not force and _is_stale_ok(cached):
        cached["_cached"] = True
        cached["_stale"] = False
        cached["_error"] = None
        return cached

    raw = _fetch_json(GELONGHUI_SUBJECTS_URL)
    if raw is None:
        if isinstance(cached, dict) and cached.get("subjects"):
            logger.info("[RSS] gelonghui unavailable; serving stale subjects blob.")
            cached["_cached"] = True
            cached["_stale"] = True
            cached["_error"] = "格隆汇暂不可用，返回上次缓存的主题数据"
            return cached
        return {
            "subjects": [],
            "_fetched_at": None,
            "_cached": False,
            "_stale": False,
            "_error": "格隆汇不可用且无缓存数据",
        }

    result_list = raw.get("result") if isinstance(raw, dict) else None
    subjects: List[Dict[str, Any]] = []
    if isinstance(result_list, list):
        for item in result_list:
            norm = _normalize_subject(item)
            if norm is not None:
                subjects.append(norm)
    # 按关注数降序，热门主题在前。
    subjects.sort(key=lambda s: s.get("followCount", 0), reverse=True)

    blob = {
        "subjects": subjects,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        "_stale": False,
        "_error": None,
    }
    try:
        _cache_put(BLOB_CACHE_KEY, blob)
    except Exception as exc:
        logger.warning("[RSS] gelonghui subjects cache write failed: %s", exc)
    return blob


def get_subjects(force: bool = False, keyword: Optional[str] = None) -> Dict[str, Any]:
    """Filtered + capped subject list for the frontend param picker.

    ``keyword`` does a case-insensitive substring match on name/summary. The
    returned list is capped at ``MAX_RETURN`` (already hot-first from the cache),
    and ``total`` reflects the count after filtering (before the cap).
    """
    blob = _get_subjects_raw(force=force)
    subjects: List[Dict[str, Any]] = blob.get("subjects") or []

    kw = (keyword or "").strip().lower()
    if kw:
        subjects = [
            s for s in subjects
            if kw in (str(s.get("name") or "").lower() + " " + str(s.get("summary") or "").lower())
        ]

    return {
        "subjects": subjects[:MAX_RETURN],
        "total": len(subjects),
        "_fetched_at": blob.get("_fetched_at"),
        "_cached": bool(blob.get("_cached")),
        "_stale": bool(blob.get("_stale")),
        "_error": blob.get("_error"),
    }
