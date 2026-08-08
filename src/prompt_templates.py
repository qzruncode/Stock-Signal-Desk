# -*- coding: utf-8 -*-
"""Prompt template storage and CRUD operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm.exc import NoResultFound

from src.storage import Base, get_db

DEFAULT_PROMPT_NAME = "通用取证分析"

DEFAULT_PROMPT_CONTENT = """## 任务
围绕用户当前问题完成可核验的分析或说明，不套用预设领域框架。

## 方法
1. 先明确问题、范围、时间口径和交付形式。
2. 只使用与问题直接相关的事实、材料或工具结果；区分事实、计算、观点和推断。
3. 对每项重要结论说明依据、反证或不确定性；信息不足时明确缺口，不补造细节。
4. 输出应直接回应用户问题，避免无关维度、固定评分和预设行动建议。

## 输出要求
先给结论，再给简洁依据；保留必要的来源、时间和限制说明。"""

_LEGACY_DEFAULT_TEMPLATE_NAME = "综合多维分析"
_LEGACY_DEFAULT_TEMPLATE_MARKERS = (
    "综合评分（满分100）",
    "操作建议：买入/观望/减仓",
)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(String(36), primary_key=True)
    name = Column(String(100), nullable=False)
    content = Column(Text, nullable=False)
    is_default = Column(Integer, default=0)
    created_at = Column(String(30), nullable=False)
    updated_at = Column(String(30), nullable=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PromptTemplateStore:
    def __init__(self):
        self._ensure_default()

    def _ensure_default(self):
        existing = self.load_all()
        if not existing:
            self.create(DEFAULT_PROMPT_NAME, DEFAULT_PROMPT_CONTENT, is_default=True)
            return
        # Migrate only the untouched, code-supplied stock-analysis SOP.  A
        # user-authored template is never rewritten merely because it happens
        # to discuss a similar subject.
        for template in existing:
            content = str(template.get("content") or "")
            if (
                template.get("name") == _LEGACY_DEFAULT_TEMPLATE_NAME
                and all(marker in content for marker in _LEGACY_DEFAULT_TEMPLATE_MARKERS)
            ):
                self.update(
                    str(template["id"]),
                    name=DEFAULT_PROMPT_NAME,
                    content=DEFAULT_PROMPT_CONTENT,
                )

    def load_all(self) -> List[dict]:
        db = get_db()
        with db._engine.begin() as conn:
            rows = conn.execute(PromptTemplate.__table__.select().order_by(PromptTemplate.created_at)).fetchall()
        return [
            {
                "id": row.id,
                "name": row.name,
                "content": row.content,
                "is_default": bool(row.is_default),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]

    def get(self, template_id: str) -> Optional[dict]:
        db = get_db()
        with db._engine.begin() as conn:
            row = conn.execute(PromptTemplate.__table__.select().where(PromptTemplate.id == template_id)).fetchone()
        if row is None:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "content": row.content,
            "is_default": bool(row.is_default),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def get_default(self) -> Optional[dict]:
        db = get_db()
        with db._engine.begin() as conn:
            row = conn.execute(PromptTemplate.__table__.select().where(PromptTemplate.is_default == 1)).fetchone()
        if row is None:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "content": row.content,
            "is_default": bool(row.is_default),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def create(self, name: str, content: str, is_default: bool = False) -> dict:
        now = _now_iso()
        template_id = str(uuid.uuid4())
        db = get_db()
        with db._engine.begin() as conn:
            conn.execute(
                PromptTemplate.__table__.insert().values(
                    id=template_id,
                    name=name,
                    content=content,
                    is_default=1 if is_default else 0,
                    created_at=now,
                    updated_at=now,
                )
            )
        return self.get(template_id)

    def update(
        self,
        template_id: str,
        name: Optional[str] = None,
        content: Optional[str] = None,
        is_default: Optional[bool] = None,
    ) -> Optional[dict]:
        existing = self.get(template_id)
        if existing is None:
            return None
        db = get_db()
        values = {"updated_at": _now_iso()}
        if name is not None:
            values["name"] = name
        if content is not None:
            values["content"] = content
        if is_default is not None:
            values["is_default"] = 1 if is_default else 0
        with db._engine.begin() as conn:
            conn.execute(PromptTemplate.__table__.update().where(PromptTemplate.id == template_id).values(**values))
        return self.get(template_id)

    def set_default(self, template_id: str) -> Optional[dict]:
        """Set exactly one default template inside one database transaction."""
        db = get_db()
        now = _now_iso()
        with db._engine.begin() as conn:
            target = conn.execute(
                PromptTemplate.__table__.update()
                .where(PromptTemplate.id == template_id)
                .values(is_default=1, updated_at=now)
            )
            if not target.rowcount:
                return None
            conn.execute(
                PromptTemplate.__table__.update()
                .where(PromptTemplate.id != template_id)
                .values(is_default=0, updated_at=now)
            )
        return self.get(template_id)

    def delete_non_default(self, template_id: str) -> bool:
        """Delete one non-default template with no preceding read."""
        db = get_db()
        with db._engine.begin() as conn:
            result = conn.execute(
                PromptTemplate.__table__.delete().where(
                    PromptTemplate.id == template_id,
                    PromptTemplate.is_default != 1,
                )
            )
        return bool(result.rowcount)

    def delete(self, template_id: str) -> bool:
        existing = self.get(template_id)
        if existing is None:
            return False
        db = get_db()
        with db._engine.begin() as conn:
            conn.execute(PromptTemplate.__table__.delete().where(PromptTemplate.id == template_id))
        return True


# Cached singleton
_store: Optional[PromptTemplateStore] = None


def get_prompt_template_store() -> PromptTemplateStore:
    global _store
    if _store is None:
        _store = PromptTemplateStore()
    return _store
