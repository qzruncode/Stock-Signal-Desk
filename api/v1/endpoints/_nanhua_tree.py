# -*- coding: utf-8 -*-
"""南华期货研报分类树代理 — 供 ``/nanhua/report/:type1/:type2`` 路由选参。

RSSHub 的 ``/nanhua/report/:type1/:type2`` 路由要两个分类代码，但上游元数据只给了
几行散文示例（``WEEK``/``SEASON``/``HOT``… 配 ``WEEK_black``/``WEEK_enchem``…），没有
可选列表，用户无从知道 type2 必须与 type1 同前缀（``HOT`` 配 ``HOT_black``，而非
``WEEK_black``）。填错组合上游返回空，RSSHub 抛 ``this route is empty`` 503。

南华官网的分类树接口 ``getTreeList.json``（POST ``{}``）返回完整的合法 type1/type2
树（10 个一级分类，各带若干二级）。这里代理它并做 6h 缓存，让前端按 type1 联动出该
分类下的合法 type2，从根本上避免非法组合。

鲁棒性与 ``_gelonghui_subjects.py`` 一致：抓取失败回退 stale 缓存，都没有则返回带
``_error`` 的空结果，前端据此降级（回退成普通输入框）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from api.v1.endpoints._rss_cache import _cache_get, _cache_put

logger = logging.getLogger(__name__)

BLOB_CACHE_KEY = "rss:nanhua:tree:blob:v1"
BLOB_TTL_SECONDS = 6 * 3600  # 分类树变化不频繁
FETCH_TIMEOUT = 20.0
NANHUA_TREELIST_URL = "https://mall.nanhua.net/mall/nh/api/reportType/getTreeList.json"


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
            "Content-Type": "application/json",
        }
        # 上游是 POST + 空 body（与 RSSHub handler 调用方式一致）。
        resp = requests.post(url, json={}, headers=headers, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("[RSS] nanhua treeList fetch failed %s: %s", url, exc)
        return None


def _normalize_child(item: Any) -> Optional[Dict[str, str]]:
    if not isinstance(item, dict):
        return None
    code = item.get("type")
    if not code:
        return None
    return {"type": str(code), "name": str(item.get("name") or "").strip()}


def _normalize_type1(item: Any) -> Optional[Dict[str, Any]]:
    """Map a raw treeList node to ``{type, name, children: [{type, name}]}``."""
    if not isinstance(item, dict):
        return None
    code = item.get("type")
    if not code:
        return None
    children: List[Dict[str, str]] = []
    for child in item.get("children") or []:
        norm = _normalize_child(child)
        if norm is not None:
            children.append(norm)
    return {
        "type": str(code),
        "name": str(item.get("name") or "").strip(),
        "children": children,
    }


def _get_tree_raw(force: bool = False) -> Dict[str, Any]:
    """Return the normalized category tree + cache metadata.

    Response shape: ``{"types": [...], "_fetched_at", "_cached", "_stale", "_error"}``.
    On fetch failure, serves the stale cached blob if available.
    """
    cached = _cache_get(BLOB_CACHE_KEY)
    if not force and _is_stale_ok(cached):
        cached["_cached"] = True
        cached["_stale"] = False
        cached["_error"] = None
        return cached

    raw = _fetch_json(NANHUA_TREELIST_URL)
    if raw is None:
        if isinstance(cached, dict) and cached.get("types"):
            logger.info("[RSS] nanhua treeList unavailable; serving stale tree.")
            cached["_cached"] = True
            cached["_stale"] = True
            cached["_error"] = "南华分类树暂不可用，返回上次缓存的数据"
            return cached
        return {
            "types": [],
            "_fetched_at": None,
            "_cached": False,
            "_stale": False,
            "_error": "南华分类树不可用且无缓存数据",
        }

    data_list = raw.get("data") if isinstance(raw, dict) else None
    types: List[Dict[str, Any]] = []
    if isinstance(data_list, list):
        for item in data_list:
            norm = _normalize_type1(item)
            if norm is not None:
                types.append(norm)

    blob = {
        "types": types,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        "_stale": False,
        "_error": None,
    }
    try:
        _cache_put(BLOB_CACHE_KEY, blob)
    except Exception as exc:
        logger.warning("[RSS] nanhua tree cache write failed: %s", exc)
    return blob


def get_nanhua_tree(force: bool = False) -> Dict[str, Any]:
    """Category tree for the frontend cascade picker.

    Returns ``{types: [{type, name, children: [{type, name}]}], total, _cached, _stale, _error}``.
    """
    blob = _get_tree_raw(force=force)
    types: List[Dict[str, Any]] = blob.get("types") or []
    return {
        "types": types,
        "total": len(types),
        "_fetched_at": blob.get("_fetched_at"),
        "_cached": bool(blob.get("_cached")),
        "_stale": bool(blob.get("_stale")),
        "_error": blob.get("_error"),
    }
