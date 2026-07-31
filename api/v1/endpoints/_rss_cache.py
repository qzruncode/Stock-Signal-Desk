# -*- coding: utf-8 -*-
"""RSS feed cache helpers — key building, get, and put."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _cache_get(key: str) -> Optional[dict]:
    """从数据库获取缓存的 RSS 数据。"""
    try:
        from src.storage import DatabaseManager

        db = DatabaseManager.get_instance()
        data = db.get_rss_cache(key)
        return data
    except Exception:
        logger.warning("[RSS] _cache_get failed for key=%s", key, exc_info=True)
        return None


def _cache_put(key: str, data: dict) -> None:
    """将 RSS 数据写入缓存。"""
    try:
        from src.storage import DatabaseManager

        db = DatabaseManager.get_instance()
        db.save_rss_cache(key, json.dumps(data, ensure_ascii=False))
    except Exception as exc:
        logger.warning(f"[RSS] 缓存写入失败: {exc}")


def _stable_json(value: dict) -> str:
    """Stable, sorted-key JSON for deterministic cache keys."""
    return json.dumps(value or {}, sort_keys=True, ensure_ascii=False)


def _rss_cache_key_generic(
    route_path: str,
    params: Optional[dict] = None,
    options: Optional[dict] = None,
) -> str:
    """Cache key for a generic FeedSpec (route_path + params + universal options).

    Must include the FULL options dict so e.g. mode=fulltext vs default don't
    collide. Hour granularity stays (news cadence); force=true bypasses cache.
    """
    hour = datetime.now().strftime("%Y%m%d%H")
    params = params or {}
    options = options or {}
    # v5 adds normalized summaries/titles/content and drops structurally empty items.
    return f"rss:spec:v5:{route_path}:{_stable_json(params)}:{_stable_json(options)}:{hour}"
