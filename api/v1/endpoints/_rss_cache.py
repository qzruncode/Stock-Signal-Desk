# -*- coding: utf-8 -*-
"""RSS feed cache helpers — key building, get, and put."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _rss_cache_key(source: str, stock_code: str = "", type: str = "",
                   category: str = "", keyword: str = "", uid: str = "") -> str:
    """按小时粒度构建缓存 key（新闻更新频率高于日线）。"""
    hour = datetime.now().strftime("%Y%m%d%H")
    parts = [f"rss:{source}"]
    if stock_code:
        parts.append(f"stock={stock_code}")
    if type:
        parts.append(f"type={type}")
    if category:
        parts.append(f"cat={category}")
    if keyword:
        parts.append(f"kw={keyword}")
    if uid:
        parts.append(f"uid={uid}")
    parts.append(hour)
    return ":".join(parts)


def _cache_get(key: str) -> Optional[dict]:
    """从数据库获取缓存的 RSS 数据。"""
    try:
        from src.storage import DatabaseManager
        db = DatabaseManager.get_instance()
        data = db.get_kline_snapshot(key)
        return data
    except Exception:
        logger.warning("[RSS] _cache_get failed for key=%s", key, exc_info=True)
        return None


def _cache_put(key: str, data: dict) -> None:
    """将 RSS 数据写入缓存。"""
    try:
        from src.storage import DatabaseManager
        db = DatabaseManager.get_instance()
        db.save_kline_snapshot(key, json.dumps(data, ensure_ascii=False))
    except Exception as exc:
        logger.warning(f"[RSS] 缓存写入失败: {exc}")