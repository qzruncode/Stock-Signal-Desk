# -*- coding: utf-8 -*-
"""中指指数报告分类代理 — 供 ``/cih-index/report/list/:report?`` 路由选参。

RSSHub 的 ``/cih-index/report/list/:report?`` 路由的 ``report`` 参数并非"报告 id"，
而是一个**复合路径段**，把分类/排序/分页编码进 ``-`` 分隔的 ``{前缀}{值}`` 片段。上游
前端 ``buildUrlParams`` 的前缀表（逆向自 cih-index report JS bundle）::

    f = firstId      一级分类 classId
    s = secondId     二级分类 classId（选了一级后才出现）
    p = pageNum      页码
    t = tagId        标签
    r = reportCycle  报告周期
    i = isCharging   是否收费
    o = orderIndex   排序字段（addtime / publishTime …）
    d = orderType    排序方向（desc / asc）

例：``p1-oaddtime-ddesc`` = 第 1 页、按添加时间降序；``f<classId>-p1-oaddtime-ddesc``
= 限定一级分类。各片段顺序无关（上游按前缀字母识别）。留空时 RSSHub 用默认
``p1-oaddtime-ddesc``（全部报告）。

上游元数据只给了散文（"报告 id，可在 URL 中找到"），无可选列表。但报告列表页
``https://www.cih-index.com/report/list/p1-oaddtime-ddesc`` 的 ``__INITIAL_STATE__``
内嵌了完整一级分类树 ``indNavLists``（8 个：政策解读/住宅市场/商业市场/…）。这里代理
它并做 6h 缓存，让前端按分类名选择并拼出合法路径段，避免用户盲填。

鲁棒性与 ``_nanhua_tree.py`` / ``_gelonghui_subjects.py`` 一致：抓取失败回退 stale 缓存，
都没有则返回带 ``_error`` 的空结果，前端据此降级（回退成普通输入框）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from api.v1.endpoints._rss_cache import _cache_get, _cache_put

logger = logging.getLogger(__name__)

BLOB_CACHE_KEY = "rss:cih-index:categories:blob:v1"
BLOB_TTL_SECONDS = 6 * 3600  # 分类树变化不频繁
FETCH_TIMEOUT = 20.0
CIH_REPORT_LIST_URL = "https://www.cih-index.com/report/list/p1-oaddtime-ddesc"

# 默认 report 路径段（与 RSSHub handler 默认值一致）。
DEFAULT_REPORT_SEGMENT = "p1-oaddtime-ddesc"


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


def _fetch_initial_state() -> Optional[dict]:
    """Fetch the report list page and extract ``window.__INITIAL_STATE__``."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
        }
        resp = requests.get(CIH_REPORT_LIST_URL, headers=headers, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("[RSS] cih-index report list fetch failed: %s", exc)
        return None

    # 上游把初始状态挂在 <script>window.__INITIAL_STATE__ = {...};</script>。
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", resp.text)
    if not match:
        logger.warning("[RSS] cih-index __INITIAL_STATE__ not found in page")
        return None
    try:
        return json.loads(match.group(1))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("[RSS] cih-index __INITIAL_STATE__ parse failed: %s", exc)
        return None


def _normalize_category(item: Any) -> Optional[Dict[str, str]]:
    """Map a raw indNavLists node to ``{classId, className}``."""
    if not isinstance(item, dict):
        return None
    class_id = item.get("classId")
    if not class_id:
        return None
    return {
        "classId": str(class_id),
        "className": str(item.get("className") or "").strip(),
    }


def _get_categories_raw(force: bool = False) -> Dict[str, Any]:
    """Return the normalized category list + cache metadata.

    Response shape: ``{"categories": [...], "_fetched_at", "_cached", "_stale", "_error"}``.
    On fetch failure, serves the stale cached blob if available.
    """
    cached = _cache_get(BLOB_CACHE_KEY)
    if not force and _is_stale_ok(cached):
        cached["_cached"] = True
        cached["_stale"] = False
        cached["_error"] = None
        return cached

    state = _fetch_initial_state()
    if state is None:
        if isinstance(cached, dict) and cached.get("categories"):
            logger.info("[RSS] cih-index unavailable; serving stale categories.")
            cached["_cached"] = True
            cached["_stale"] = True
            cached["_error"] = "中指指数分类暂不可用，返回上次缓存的数据"
            return cached
        return {
            "categories": [],
            "_fetched_at": None,
            "_cached": False,
            "_stale": False,
            "_error": "中指指数分类不可用且无缓存数据",
        }

    data = state.get("data") if isinstance(state, dict) else None
    raw_list = data.get("indNavLists") if isinstance(data, dict) else None
    categories: List[Dict[str, str]] = []
    if isinstance(raw_list, list):
        for item in raw_list:
            norm = _normalize_category(item)
            if norm is not None:
                categories.append(norm)

    blob = {
        "categories": categories,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        "_stale": False,
        "_error": None,
    }
    try:
        _cache_put(BLOB_CACHE_KEY, blob)
    except Exception as exc:
        logger.warning("[RSS] cih-index categories cache write failed: %s", exc)
    return blob


def get_cih_index_categories(force: bool = False) -> Dict[str, Any]:
    """Top-level categories for the frontend picker.

    Returns ``{categories: [{classId, className}], total, _cached, _stale, _error}``.
    """
    blob = _get_categories_raw(force=force)
    categories: List[Dict[str, str]] = blob.get("categories") or []
    return {
        "categories": categories,
        "total": len(categories),
        "_fetched_at": blob.get("_fetched_at"),
        "_cached": bool(blob.get("_cached")),
        "_stale": bool(blob.get("_stale")),
        "_error": blob.get("_error"),
    }
