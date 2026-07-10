# -*- coding: utf-8 -*-
"""Mixin: RSS subscription (persistent FeedSpec) operations."""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session

from src.storage.models import RssSubscription, RssSubscriptionTitleConflict


def _clean_json_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """归一化为可序列化的扁平 dict（键值均转 str，丢弃空值）。"""
    cleaned: Dict[str, Any] = {}
    for k, v in (value or {}).items():
        key = str(k).strip()
        if not key:
            continue
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        cleaned[key] = v
    return cleaned


class RssSubscriptionMixin:
    """Mixin providing RSS subscription CRUD operations."""

    def list_rss_subscriptions(self) -> List[Dict[str, Any]]:
        """列出全部 RSS 订阅（按 sort_order、id 升序）。"""
        with self.get_session() as session:
            rows = session.execute(
                select(RssSubscription).order_by(
                    RssSubscription.sort_order.asc(),
                    RssSubscription.id.asc(),
                )
            ).scalars().all()
            return [row.to_dict() for row in rows]

    def upsert_rss_subscription(
        self,
        title: str,
        namespace: str,
        route_path: str,
        params: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """按 title 创建或更新订阅（同名即更新 namespace/route_path/params/options）。"""
        clean_title = (title or '').strip()
        if not clean_title:
            raise ValueError('订阅标题不能为空')
        clean_ns = (namespace or '').strip()
        clean_path = (route_path or '').strip()
        if not clean_ns or not clean_path:
            raise ValueError('namespace 与 route_path 不能为空')
        params_json = json.dumps(_clean_json_dict(params), ensure_ascii=False)
        options_json = json.dumps(_clean_json_dict(options), ensure_ascii=False)

        def _write(session: Session) -> Dict[str, Any]:
            existing = session.execute(
                select(RssSubscription).where(RssSubscription.title == clean_title)
            ).scalars().first()
            if existing is not None:
                existing.namespace = clean_ns
                existing.route_path = clean_path
                existing.params_json = params_json
                existing.options_json = options_json
                existing.updated_at = datetime.now()
                session.flush()
                return existing.to_dict()
            max_order = session.execute(
                select(func.max(RssSubscription.sort_order))
            ).scalar()
            row = RssSubscription(
                title=clean_title,
                namespace=clean_ns,
                route_path=clean_path,
                params_json=params_json,
                options_json=options_json,
                sort_order=int(max_order or 0) + 1,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

        return self._run_write_transaction(
            f"upsert_rss_subscription[{clean_title}]",
            _write,
        )

    def update_rss_subscription(
        self,
        sub_id: Any,
        *,
        title: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
        namespace: Optional[str] = None,
        route_path: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """按 id 更新订阅字段。不存在返回 None。改名撞其它订阅抛 RssSubscriptionTitleConflict。"""
        try:
            sid = int(sub_id)
        except (TypeError, ValueError):
            return None

        def _write(session: Session) -> Optional[Dict[str, Any]]:
            row = session.get(RssSubscription, sid)
            if row is None:
                return None
            if title is not None:
                clean_title = title.strip()
                if not clean_title:
                    raise ValueError('订阅标题不能为空')
                dup = session.execute(
                    select(RssSubscription).where(
                        RssSubscription.title == clean_title,
                        RssSubscription.id != sid,
                    )
                ).scalars().first()
                if dup is not None:
                    raise RssSubscriptionTitleConflict(clean_title)
                row.title = clean_title
            if namespace is not None:
                row.namespace = namespace.strip()
            if route_path is not None:
                row.route_path = route_path.strip()
            if params is not None:
                row.params_json = json.dumps(_clean_json_dict(params), ensure_ascii=False)
            if options is not None:
                row.options_json = json.dumps(_clean_json_dict(options), ensure_ascii=False)
            row.updated_at = datetime.now()
            session.flush()
            return row.to_dict()

        return self._run_write_transaction(
            f"update_rss_subscription[{sid}]",
            _write,
        )

    def delete_rss_subscription(self, sub_id: Any) -> bool:
        """按 id 删除订阅。成功返回 True，不存在返回 False。"""
        try:
            sid = int(sub_id)
        except (TypeError, ValueError):
            return False

        def _write(session: Session) -> bool:
            result = session.execute(
                delete(RssSubscription).where(RssSubscription.id == sid)
            )
            return (result.rowcount or 0) > 0

        return self._run_write_transaction(
            f"delete_rss_subscription[{sid}]",
            _write,
        )

    def reorder_rss_subscriptions(self, ordered_ids: List[Any]) -> bool:
        """按给定 id 顺序重排订阅的 sort_order（从 1 开始）。未列出的订阅保持原序追加在后。"""
        clean_ids: List[int] = []
        for sid in (ordered_ids or []):
            try:
                clean_ids.append(int(sid))
            except (TypeError, ValueError):
                continue
        if not clean_ids:
            return False

        def _write(session: Session) -> bool:
            # Load all existing rows once.
            rows = {r.id: r for r in session.execute(
                select(RssSubscription)
            ).scalars().all()}
            order = 1
            for sid in clean_ids:
                row = rows.pop(sid, None)
                if row is not None:
                    row.sort_order = order
                    order += 1
            # Remaining (not in the list) keep their relative order after.
            for row in rows.values():
                row.sort_order = order
                order += 1
            session.flush()
            return True

        return self._run_write_transaction(
            "reorder_rss_subscriptions",
            _write,
        )
