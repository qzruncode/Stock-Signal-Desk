# -*- coding: utf-8 -*-
"""Mixin: quote and kline snapshot operations."""
import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.models import QuoteSnapshot, KlineSnapshot

logger = logging.getLogger(__name__)


class QuoteKlineMixin:
    """行情快照与K线快照存取操作 mixin."""

    def save_quote_snapshot(self, code: str, data_json: str) -> None:
        """保存实时行情快照（upsert by code）。写入失败不抛异常。"""
        try:
            def _write(session: Session) -> None:
                existing = session.execute(
                    select(QuoteSnapshot).where(QuoteSnapshot.code == code)
                ).scalar_one_or_none()
                if existing:
                    existing.data = data_json
                    existing.updated_at = datetime.now()
                else:
                    session.add(QuoteSnapshot(code=code, data=data_json))
            self._run_write_transaction(f"save_quote_snapshot[{code}]", _write)
        except Exception:
            logger.debug("行情快照写入失败: code=%s", code, exc_info=True)

    def get_quote_snapshots(self, codes: list[str], since: Optional[datetime] = None) -> dict[str, Any]:
        """批量获取行情快照。返回 {code: data_dict}，每个 dict 含 _fetched_at 字段。

        Args:
            codes: 股票代码列表
            since: 可选，只返回 updated_at >= since 的快照（用于过滤旧交易日盘中数据）
        """
        if not codes:
            return {}
        result: dict[str, Any] = {}
        with self.get_session() as session:
            try:
                stmt = select(QuoteSnapshot).where(QuoteSnapshot.code.in_(codes))
                if since is not None:
                    stmt = stmt.where(QuoteSnapshot.updated_at >= since)
                rows = session.execute(stmt).scalars().all()
                for row in rows:
                    try:
                        d = json.loads(row.data or "{}")
                        if isinstance(d, dict):
                            d['_fetched_at'] = row.updated_at.isoformat() if row.updated_at else None
                            result[row.code] = d
                    except Exception:
                        continue
            except Exception:
                logger.debug("行情快照读取失败", exc_info=True)
        return result

    def save_kline_snapshot(self, code: str, data_json: str) -> None:
        """保存K线数据快照（upsert by code）。写入失败不抛异常。"""
        try:
            def _write(session: Session) -> None:
                existing = session.execute(
                    select(KlineSnapshot).where(KlineSnapshot.code == code)
                ).scalar_one_or_none()
                if existing:
                    existing.data = data_json
                    existing.updated_at = datetime.now()
                else:
                    session.add(KlineSnapshot(code=code, data=data_json))
            self._run_write_transaction(f"save_kline_snapshot[{code}]", _write)
        except Exception:
            logger.debug("K线快照写入失败: code=%s", code, exc_info=True)

    def get_kline_snapshot(self, code: str) -> dict[str, Any] | None:
        """获取K线数据快照。返回 dict 含 _fetched_at 字段，或 None。"""
        try:
            with self.get_session() as session:
                row = session.execute(
                    select(KlineSnapshot).where(KlineSnapshot.code == code)
                ).scalar_one_or_none()
                if row:
                    d = json.loads(row.data or "{}")
                    if isinstance(d, dict):
                        d['_fetched_at'] = row.updated_at.isoformat() if row.updated_at else None
                        return d
        except Exception:
            logger.debug("K线快照读取失败", exc_info=True)
        return None