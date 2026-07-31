# -*- coding: utf-8 -*-
"""Mixin: AI 助手 system prompt 模板的 CRUD 与生效切换。"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, desc, update
from sqlalchemy.orm import Session

from src.storage.models import AgentPromptTemplate


class AgentPromptMixin:
    """Mixin providing agent prompt template operations."""

    def create_agent_prompt(
        self,
        name: str,
        content: str,
        is_active: bool = False,
    ) -> AgentPromptTemplate:
        """新建一个 prompt 模板。"""
        now = datetime.now()
        with self.session_scope() as session:
            record = AgentPromptTemplate(
                name=(name or "").strip() or "未命名模板",
                content=content or "",
                is_active=bool(is_active),
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            session.expunge(record)
            return record

    def get_agent_prompt(self, template_id: int) -> Optional[AgentPromptTemplate]:
        """按 ID 查询单个 prompt 模板。"""
        with self.get_session() as session:
            record = (
                session.execute(select(AgentPromptTemplate).where(AgentPromptTemplate.id == template_id))
                .scalars()
                .first()
            )
            if record:
                session.expunge(record)
            return record

    def list_agent_prompts(self) -> List[AgentPromptTemplate]:
        """列出全部 prompt 模板，生效的排最前，其次按更新时间倒序。"""
        with self.get_session() as session:
            records = (
                session.execute(
                    select(AgentPromptTemplate).order_by(
                        desc(AgentPromptTemplate.is_active),
                        desc(AgentPromptTemplate.updated_at),
                        desc(AgentPromptTemplate.created_at),
                    )
                )
                .scalars()
                .all()
            )
            for record in records:
                session.expunge(record)
            return list(records)

    def update_agent_prompt(
        self,
        template_id: int,
        name: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Optional[AgentPromptTemplate]:
        """更新模板名称或内容（不影响 is_active 状态）。"""
        with self.session_scope() as session:
            record = (
                session.execute(select(AgentPromptTemplate).where(AgentPromptTemplate.id == template_id))
                .scalars()
                .first()
            )
            if record is None:
                return None
            if name is not None:
                record.name = (name or "").strip() or record.name
            if content is not None:
                record.content = content
            record.updated_at = datetime.now()
            session.flush()
            session.expunge(record)
            return record

    def delete_agent_prompt(self, template_id: int) -> bool:
        """删除模板，返回是否实际删除。"""
        with self.session_scope() as session:
            record = (
                session.execute(select(AgentPromptTemplate).where(AgentPromptTemplate.id == template_id))
                .scalars()
                .first()
            )
            if record is None:
                return False
            session.delete(record)
            return True

    def get_active_agent_prompt(self) -> Optional[AgentPromptTemplate]:
        """查询当前生效的 prompt 模板（同时刻仅一个）。"""
        with self.get_session() as session:
            record = (
                session.execute(select(AgentPromptTemplate).where(AgentPromptTemplate.is_active == True))  # noqa: E712
                .scalars()
                .first()
            )
            if record:
                session.expunge(record)
            return record

    def set_agent_prompt_active(self, template_id: int) -> Optional[AgentPromptTemplate]:
        """将指定模板设为生效，同时把其余所有模板置为非生效。

        清零 + 置位在同一个写事务内完成（SQLite 下走 BEGIN IMMEDIATE + 锁重试），
        保证同时刻只有一个 is_active=True。
        """

        def _write(session: Session) -> Optional[AgentPromptTemplate]:
            target = (
                session.execute(select(AgentPromptTemplate).where(AgentPromptTemplate.id == template_id))
                .scalars()
                .first()
            )
            if target is None:
                return None
            # 先把所有行的 is_active 清零
            session.execute(update(AgentPromptTemplate).values(is_active=False, updated_at=datetime.now()))
            # 再置目标行为生效
            target.is_active = True
            target.updated_at = datetime.now()
            session.flush()
            session.expunge(target)
            return target

        return self._run_write_transaction(f"set_agent_prompt_active[{template_id}]", _write)
