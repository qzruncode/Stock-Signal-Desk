# -*- coding: utf-8 -*-
"""RSS feed cache helpers — key building, get, and put."""

from __future__ import annotations

import json
from hashlib import sha256
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _cache_get(key: str) -> Optional[dict]:
    """从数据库获取缓存的 RSS 数据。"""
    from market_data_service.providers.common import force_source_read

    if force_source_read.get():
        return None
    try:
        from market_data_service.storage import DatabaseManager

        db = DatabaseManager.get_instance()
        data = db.get_rss_cache(key)
        return data
    except Exception:
        logger.warning("[RSS] _cache_get failed for key=%s", key, exc_info=True)
        return None


def _cache_put(key: str, data: dict) -> None:
    """将 RSS 数据写入缓存。"""
    try:
        from market_data_service.storage import DatabaseManager

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
    privileged_requested = any(
        key in options for key in ("access_key", "key", "scihub", "code", "token")
    )
    policy_fingerprint = ""
    if privileged_requested:
        policy_fingerprint = sha256(
            str(os.getenv("RSSHUB_AGENT_PRIVILEGED_OPTIONS") or "").encode("utf-8")
        ).hexdigest()[:16]
    # v5 adds normalized summaries/titles/content and drops structurally empty items.
    return (
        f"rss:spec:v6:{route_path}:{_stable_json(params)}:"
        f"{_stable_json(options)}:{policy_fingerprint}:{hour}"
    )
