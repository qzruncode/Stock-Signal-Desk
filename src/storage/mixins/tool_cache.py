# -*- coding: utf-8 -*-
"""Mixin: persistent typed cache used by Agent tools."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.models import ToolCache

logger = logging.getLogger(__name__)


class ToolCacheMixin:
    def save_tool_cache(self, cache_key: str, payload: bytes) -> None:
        """Upsert a trusted, application-serialized tool cache value."""
        try:

            def _write(session: Session) -> None:
                existing = session.execute(
                    select(ToolCache).where(ToolCache.cache_key == cache_key)
                ).scalar_one_or_none()
                if existing:
                    existing.payload = payload
                    existing.updated_at = datetime.now()
                else:
                    session.add(ToolCache(cache_key=cache_key, payload=payload))

            self._run_write_transaction(f"save_tool_cache[{cache_key[:40]}]", _write)
        except Exception:
            logger.debug("工具缓存写入失败: key=%s", cache_key[:80], exc_info=True)

    def get_tool_cache(self, cache_key: str) -> dict[str, Any] | None:
        """Return binary payload plus its persisted update time."""
        try:
            with self.get_session() as session:
                row = session.execute(select(ToolCache).where(ToolCache.cache_key == cache_key)).scalar_one_or_none()
                if row:
                    return {
                        "payload": bytes(row.payload),
                        "updated_at": row.updated_at,
                    }
        except Exception:
            logger.debug("工具缓存读取失败: key=%s", cache_key[:80], exc_info=True)
        return None
