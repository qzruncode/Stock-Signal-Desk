# -*- coding: utf-8 -*-
"""Mixin: RSS feed / namespace blob 缓存存取。

与 kline_snapshot 解耦的独立 KV 缓存：RSS 缓存键长（含 route_path+params+
options+小时桶）且 namespace blob 体积大（~3.3MB），单独成表便于运维与按 TTL
清理。底层仍是 key→JSON text 的 upsert，语义与 kline 快照一致。
"""
import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.models import RssCache

logger = logging.getLogger(__name__)


class RssCacheMixin:
    """RSS 缓存存取 mixin."""

    def save_rss_cache(self, cache_key: str, data_json: str) -> None:
        """保存 RSS 缓存（upsert by cache_key）。写入失败不抛异常。"""
        try:

            def _write(session: Session) -> None:
                existing = session.execute(select(RssCache).where(RssCache.cache_key == cache_key)).scalar_one_or_none()
                if existing:
                    existing.data = data_json
                    existing.updated_at = datetime.now()
                else:
                    session.add(RssCache(cache_key=cache_key, data=data_json))

            self._run_write_transaction(f"save_rss_cache[{cache_key[:40]}]", _write)
        except Exception:
            logger.debug("RSS 缓存写入失败: key=%s", cache_key[:80], exc_info=True)

    def get_rss_cache(self, cache_key: str) -> Optional[dict[str, Any]]:
        """获取 RSS 缓存。返回 dict 含 _fetched_at 字段，或 None。"""
        try:
            with self.get_session() as session:
                row = session.execute(select(RssCache).where(RssCache.cache_key == cache_key)).scalar_one_or_none()
                if row:
                    d = json.loads(row.data or "{}")
                    if isinstance(d, dict):
                        d["_fetched_at"] = row.updated_at.isoformat() if row.updated_at else None
                        return d
        except Exception:
            logger.debug("RSS 缓存读取失败: key=%s", cache_key[:80], exc_info=True)
        return None
