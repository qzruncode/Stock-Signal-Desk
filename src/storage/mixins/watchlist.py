# -*- coding: utf-8 -*-
"""Mixin: watchlist group operations."""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session

from src.storage.models import WatchlistGroup, WatchlistGroupNameConflict


class WatchlistMixin:
    """Mixin providing watchlist group CRUD operations."""

    @staticmethod
    def _clean_group_codes(codes: Optional[List[Any]]) -> List[str]:
        """去重并清洗分组成员代码，保持原始顺序。"""
        cleaned: List[str] = []
        seen: set[str] = set()
        for code in codes or []:
            value = str(code).strip()
            if value and value not in seen:
                seen.add(value)
                cleaned.append(value)
        return cleaned

    def list_watchlist_groups(self) -> List[Dict[str, Any]]:
        """列出全部自定义分组（按 sort_order、id 升序）。"""
        with self.get_session() as session:
            rows = (
                session.execute(
                    select(WatchlistGroup).order_by(
                        WatchlistGroup.sort_order.asc(),
                        WatchlistGroup.id.asc(),
                    )
                )
                .scalars()
                .all()
            )
            return [row.to_dict() for row in rows]

    def upsert_watchlist_group(
        self,
        name: str,
        codes: Optional[List[Any]] = None,
        source: str = "manual",
    ) -> Dict[str, Any]:
        """按 name 创建或更新分组（同名即更新 codes）。"""
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("分组名称不能为空")
        codes_json = json.dumps(self._clean_group_codes(codes), ensure_ascii=False)
        clean_source = (source or "manual").strip() or "manual"

        def _write(session: Session) -> Dict[str, Any]:
            existing = (
                session.execute(select(WatchlistGroup).where(WatchlistGroup.name == clean_name)).scalars().first()
            )
            if existing is not None:
                existing.codes_json = codes_json
                existing.source = clean_source
                existing.updated_at = datetime.now()
                session.flush()
                return existing.to_dict()
            max_order = session.execute(select(func.max(WatchlistGroup.sort_order))).scalar()
            row = WatchlistGroup(
                name=clean_name,
                codes_json=codes_json,
                source=clean_source,
                sort_order=int(max_order or 0) + 1,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

        return self._run_write_transaction(
            f"upsert_watchlist_group[{clean_name}]",
            _write,
        )

    def update_watchlist_group(
        self,
        group_id: Any,
        *,
        name: Optional[str] = None,
        codes: Optional[List[Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """按 id 更新分组的名称 / 成员。分组不存在返回 None。

        改名撞到其它分组的名称时抛 WatchlistGroupNameConflict。
        """
        try:
            gid = int(group_id)
        except (TypeError, ValueError):
            return None

        def _write(session: Session) -> Optional[Dict[str, Any]]:
            row = session.get(WatchlistGroup, gid)
            if row is None:
                return None
            if name is not None:
                clean_name = name.strip()
                if not clean_name:
                    raise ValueError("分组名称不能为空")
                dup = (
                    session.execute(
                        select(WatchlistGroup).where(
                            WatchlistGroup.name == clean_name,
                            WatchlistGroup.id != gid,
                        )
                    )
                    .scalars()
                    .first()
                )
                if dup is not None:
                    raise WatchlistGroupNameConflict(clean_name)
                row.name = clean_name
            if codes is not None:
                row.codes_json = json.dumps(self._clean_group_codes(codes), ensure_ascii=False)
            row.updated_at = datetime.now()
            session.flush()
            return row.to_dict()

        return self._run_write_transaction(
            f"update_watchlist_group[{gid}]",
            _write,
        )

    def delete_watchlist_group(self, group_id: Any) -> bool:
        """按 id 删除分组。删除成功返回 True，不存在返回 False。"""
        try:
            gid = int(group_id)
        except (TypeError, ValueError):
            return False

        def _write(session: Session) -> bool:
            result = session.execute(delete(WatchlistGroup).where(WatchlistGroup.id == gid))
            return (result.rowcount or 0) > 0

        return self._run_write_transaction(
            f"delete_watchlist_group[{gid}]",
            _write,
        )
