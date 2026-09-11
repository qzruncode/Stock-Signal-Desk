# -*- coding: utf-8 -*-
"""Historical fundamental snapshot reads used by report reconstruction."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy import and_, desc, select

from src.storage.models import FundamentalSnapshot

logger = logging.getLogger(__name__)


class MacroMixin:
    """Read the legacy snapshot needed to render historical reports."""

    def get_latest_fundamental_snapshot(
        self,
        query_id: str,
        code: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the latest stored fundamental snapshot, if one exists."""
        if not query_id or not code:
            return None

        with self.get_session() as session:
            try:
                row = session.execute(
                    select(FundamentalSnapshot)
                    .where(
                        and_(
                            FundamentalSnapshot.query_id == query_id,
                            FundamentalSnapshot.code == code,
                        )
                    )
                    .order_by(desc(FundamentalSnapshot.created_at))
                    .limit(1)
                ).scalar_one_or_none()
            except Exception as exc:
                logger.debug(
                    "基本面快照读取失败（fail-open）: query_id=%s code=%s err=%s",
                    query_id,
                    code,
                    exc,
                )
                return None

            if row is None:
                return None
            try:
                payload = json.loads(row.payload or "{}")
                return payload if isinstance(payload, dict) else None
            except Exception:
                logger.warning("[Storage] 快照 payload JSON 解析失败", exc_info=True)
                return None


__all__ = ["MacroMixin"]
