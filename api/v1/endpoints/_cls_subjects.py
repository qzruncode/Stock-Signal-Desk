# -*- coding: utf-8 -*-
"""财联社话题列表代理 — 供 ``/cls/subject/:id?`` 路由选参。

RSSHub 的 ``/cls/subject/:id?`` 路由需要一个话题编号 ``id``（默认 ``1103`` 即
A股盘面直播），但上游元数据只给两个例子（``1103``、``1151`` 有声早报）并要求用户
"在对应话题页 URL 中找到"，没有可选列表。

财联社没有公开的话题列表接口（``/subject`` 索引页是客户端渲染、SSP 返回空
``data: {}``），但其文章 API ``https://www.cls.cn/api/subject/{id}/article`` 会
返回每篇文章附带的 ``subjects`` 数组（含 ``subject_id``/``subject_name``/
``attention_num``）。这里以默认话题（``1103``、``1151``）为种子分页抓取若干页，
从文章的 ``subjects`` 字段里收割话题去重，按关注度降序输出，让前端能按话题名点选
后填入 ``subject_id``。

cls 的文章 API 需要签名：对 ``{appName, os, sv, ...业务参数}`` 排序后 urlencode，
``sign = MD5(SHA1(querystring))``（与 RSSHub 本地 ``cls/utils.ts`` 的
``getSearchParams`` 一致，复刻在 :func:`_signed_params`）。

鲁棒性与 ``_gelonghui_subjects.py`` / ``_rss_namespace.py`` 一致：抓取失败时回退
stale 缓存，都没有则返回带 ``_error`` 的空结果，前端据此降级（回退成普通输入框）。
话题列表只覆盖热门话题，不完整——前端提供"手动输入 id"兜底（见 ``ClsSubjectPicker``）。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

from api.v1.endpoints._rss_cache import _cache_get, _cache_put

logger = logging.getLogger(__name__)

BLOB_CACHE_KEY = "rss:cls:subjects:blob:v1"
BLOB_TTL_SECONDS = 6 * 3600  # 话题列表变化不频繁
FETCH_TIMEOUT = 20.0
ROOT_URL = "https://www.cls.cn"

# cls 文章 API 的固定签名参数（与 RSSHub app/lib/routes/cls/utils.ts 一致）。
_SIGN_BASE = {"appName": "CailianpressWeb", "os": "web", "sv": "8.7.9"}

# 种子话题：默认两个（盘面直播 1103、有声早报 1151），从它们的文章里收割更多话题。
_SEED_SUBJECT_IDS = ("1103", "1151")
# 每个种子话题抓取的页数（每页 20 篇，足以覆盖热门话题集合）。
_SEED_PAGES = 3
_PAGE_SIZE = 20

# 单次最多返回给前端的条数（按关注度降序截断）。
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


def _signed_params(more: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """复刻 cls ``getSearchParams``：排序→urlencode→SHA1→MD5，作为 ``sign``。

    返回可直接作为 query string 的 dict（已包含 ``sign``，键已排序）。
    """
    merged = {**_SIGN_BASE, **(more or {})}
    merged = {k: v for k, v in merged.items() if v is not None}
    # 排序后 urlencode（cls 用 searchParams.sort()，Python 这里用 sorted 保持稳定顺序）。
    qs = urlencode(sorted(merged.items()))
    sign = hashlib.md5(hashlib.sha1(qs.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()
    out = dict(sorted(merged.items()))
    out["sign"] = sign
    return out


def _fetch_article_subjects(subject_id: str, page: int) -> List[Dict[str, Any]]:
    """抓取某话题某页的文章，返回其中出现的去重话题列表（``subject_id``/``name``/``attention``）。

    抓取失败返回空列表（调用方按种子累加，单页失败不致命）。
    """
    params = _signed_params({"Subject_Id": subject_id, "Page": page, "PageSize": _PAGE_SIZE})
    url = f"{ROOT_URL}/api/subject/{subject_id}/article"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("[RSS] cls subjects fetch failed subject=%s page=%s: %s", subject_id, page, exc)
        return []

    articles = data.get("data") if isinstance(data, dict) else None
    if not isinstance(articles, list):
        return []

    harvested: List[Dict[str, Any]] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        for subj in article.get("subjects") or []:
            if not isinstance(subj, dict):
                continue
            sid = subj.get("subject_id")
            name = subj.get("subject_name")
            if sid is None or not name:
                continue
            harvested.append({
                "subjectId": sid,
                "name": str(name).strip(),
                "attention_num": int(subj.get("attention_num") or 0),
            })
    return harvested


def _normalize_subjects(raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """去重（按 subjectId）并按关注度降序。"""
    by_id: Dict[Any, Dict[str, Any]] = {}
    for item in raw_list:
        sid = item["subjectId"]
        if sid in by_id:
            # 同一话题可能在不同文章出现，取关注度最大值。
            by_id[sid]["attention_num"] = max(by_id[sid]["attention_num"], item["attention_num"])
            continue
        by_id[sid] = {
            "subjectId": sid,
            "name": item["name"],
            "attention_num": item["attention_num"],
            "link": f"{ROOT_URL}/subject/{sid}",
        }
    subjects = list(by_id.values())
    subjects.sort(key=lambda s: s.get("attention_num", 0), reverse=True)
    return subjects


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

    harvested: List[Dict[str, Any]] = []
    for seed_id in _SEED_SUBJECT_IDS:
        for page in range(1, _SEED_PAGES + 1):
            page_items = _fetch_article_subjects(seed_id, page)
            if not page_items:
                break  # 该话题没有更多页，换下一个种子
            harvested.extend(page_items)

    if not harvested:
        if isinstance(cached, dict) and cached.get("subjects"):
            logger.info("[RSS] cls unavailable; serving stale subjects blob.")
            cached["_cached"] = True
            cached["_stale"] = True
            cached["_error"] = "财联社暂不可用，返回上次缓存的话题数据"
            return cached
        return {
            "subjects": [],
            "_fetched_at": None,
            "_cached": False,
            "_stale": False,
            "_error": "财联社不可用且无缓存数据",
        }

    subjects = _normalize_subjects(harvested)

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
        logger.warning("[RSS] cls subjects cache write failed: %s", exc)
    return blob


def get_cls_subjects(force: bool = False, keyword: Optional[str] = None) -> Dict[str, Any]:
    """Filtered + capped subject list for the frontend param picker.

    ``keyword`` does a case-insensitive substring match on name. The returned
    list is capped at ``MAX_RETURN`` (already attention-desc from the cache),
    and ``total`` reflects the count after filtering (before the cap).
    """
    blob = _get_subjects_raw(force=force)
    subjects: List[Dict[str, Any]] = blob.get("subjects") or []

    kw = (keyword or "").strip().lower()
    if kw:
        subjects = [s for s in subjects if kw in str(s.get("name") or "").lower()]

    return {
        "subjects": subjects[:MAX_RETURN],
        "total": len(subjects),
        "_fetched_at": blob.get("_fetched_at"),
        "_cached": bool(blob.get("_cached")),
        "_stale": bool(blob.get("_stale")),
        "_error": blob.get("_error"),
    }
