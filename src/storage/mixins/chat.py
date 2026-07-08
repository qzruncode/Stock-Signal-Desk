# -*- coding: utf-8 -*-
"""Mixin: chat conversation and LLM usage operations."""
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, and_, desc, func, delete, or_
from sqlalchemy.orm import Session

from src.storage.models import ChatConversation, ChatMessage, ConversationMessage, LLMUsage


class ChatMixin:
    """Mixin providing chat conversation, message, and LLM usage operations."""

    def create_chat_conversation(
        self,
        conversation_id: str,
        title: str = "新对话",
        title_source: str = "auto",
    ) -> ChatConversation:
        """创建对话会话。"""
        now = datetime.now()
        normalized_title = (title or "新对话").strip() or "新对话"

        with self.session_scope() as session:
            record = ChatConversation(
                id=conversation_id,
                title=normalized_title,
                title_source=title_source or "auto",
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            session.expunge(record)
            return record

    def get_chat_conversation(self, conversation_id: str) -> Optional[ChatConversation]:
        """按 ID 查询单个对话会话。"""
        with self.get_session() as session:
            record = session.execute(
                select(ChatConversation).where(ChatConversation.id == conversation_id)
            ).scalars().first()
            if record:
                session.expunge(record)
            return record

    def list_chat_conversations(
        self,
        offset: int = 0,
        limit: int = 50,
    ) -> Tuple[List[ChatConversation], int]:
        """分页查询对话会话列表。"""
        with self.get_session() as session:
            total = session.execute(
                select(func.count(ChatConversation.id))
            ).scalar() or 0
            records = session.execute(
                select(ChatConversation)
                .order_by(desc(ChatConversation.updated_at), desc(ChatConversation.created_at))
                .offset(offset)
                .limit(limit)
            ).scalars().all()
            for record in records:
                session.expunge(record)
            return list(records), total

    def update_chat_conversation(
        self,
        conversation_id: str,
        *,
        title: Optional[str] = None,
        title_source: Optional[str] = None,
        preview_text: Optional[str] = None,
        updated_at: Optional[datetime] = None,
    ) -> Optional[ChatConversation]:
        """更新对话会话元数据。"""
        with self.session_scope() as session:
            record = session.execute(
                select(ChatConversation).where(ChatConversation.id == conversation_id)
            ).scalars().first()
            if not record:
                return None

            if title is not None:
                normalized_title = title.strip() or "新对话"
                record.title = normalized_title[:120]
            if title_source is not None:
                record.title_source = title_source
            if preview_text is not None:
                record.preview_text = preview_text[:200] if preview_text else None
            record.updated_at = updated_at or datetime.now()
            session.flush()
            session.expunge(record)
            return record

    def delete_chat_conversation(self, conversation_id: str) -> int:
        """删除对话会话及其消息。"""
        with self.session_scope() as session:
            session.execute(
                delete(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
            )
            result = session.execute(
                delete(ChatConversation).where(ChatConversation.id == conversation_id)
            )
            return result.rowcount or 0

    def get_chat_messages(self, conversation_id: str) -> List[ChatMessage]:
        """查询对话消息列表。"""
        with self.get_session() as session:
            records = session.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.sequence.asc(), ChatMessage.created_at.asc())
            ).scalars().all()
            for record in records:
                session.expunge(record)
            return list(records)

    def replace_chat_messages(
        self,
        conversation_id: str,
        messages: List[Dict[str, Any]],
        *,
        preview_text: Optional[str] = None,
        thread_state_json: Optional[str] = None,
        updated_at: Optional[datetime] = None,
    ) -> None:
        """用完整消息快照覆盖对话消息。"""
        timestamp = updated_at or datetime.now()

        def _normalize_message(raw: Dict[str, Any], sequence: int) -> ChatMessage:
            message_id = str(raw.get("id") or f"{conversation_id}-{sequence}")
            role = str(raw.get("role") or "user").strip() or "user"
            content = raw.get("content")
            if not isinstance(content, str):
                content = "" if content is None else str(content)
            created_at_raw = raw.get("created_at")
            created_at = timestamp
            if isinstance(created_at_raw, datetime):
                created_at = created_at_raw
            elif created_at_raw:
                try:
                    created_at = datetime.fromisoformat(str(created_at_raw).replace("Z", "+00:00"))
                except ValueError:
                    created_at = timestamp
            return ChatMessage(
                id=message_id[:64],
                conversation_id=conversation_id,
                role=role[:16],
                content=content,
                sequence=sequence,
                created_at=created_at,
            )

        with self.session_scope() as session:
            session.execute(
                delete(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
            )
            for index, message in enumerate(messages):
                if not isinstance(message, dict):
                    continue
                session.add(_normalize_message(message, index))

            record = session.execute(
                select(ChatConversation).where(ChatConversation.id == conversation_id)
            ).scalars().first()
            if record:
                record.preview_text = (preview_text or record.preview_text or None)
                if record.preview_text:
                    record.preview_text = record.preview_text[:200]
                if thread_state_json is not None:
                    record.thread_state_json = thread_state_json
                record.updated_at = timestamp

    def upsert_partial_assistant_message(
        self,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> None:
        """按 id upsert 单条"进行中"的 assistant 消息(增量持久化用)。

        不删除对话内的其他消息。若同 id 已存在则更新 content,否则追加。
        用于流式生成过程中周期性保存已生成的 assistant 文本,刷新后可恢复。
        """
        safe_id = message_id[:64]
        timestamp = datetime.now()
        with self.session_scope() as session:
            existing = session.execute(
                select(ChatMessage).where(ChatMessage.id == safe_id)
            ).scalars().first()
            if existing is not None:
                existing.content = content
                existing.created_at = timestamp
                return
            # 追加:sequence 取当前最大值 + 1
            max_seq = session.execute(
                select(ChatMessage.sequence)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.sequence.desc())
                .limit(1)
            ).scalars().first()
            session.add(
                ChatMessage(
                    id=safe_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=content,
                    sequence=(max_seq or -1) + 1,
                    created_at=timestamp,
                )
            )

    # ── Agent conversation messages ──────────────────────────────────────

    def save_conversation_message(self, session_id: str, role: str, content: str) -> None:
        """
        保存 Agent 对话消息
        """
        with self.session_scope() as session:
            msg = ConversationMessage(
                session_id=session_id,
                role=role,
                content=content,
            )
            session.add(msg)

    def get_conversation_history(self, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        获取 Agent 对话历史
        """
        with self.session_scope() as session:
            stmt = select(ConversationMessage).filter(
                ConversationMessage.session_id == session_id
            ).order_by(ConversationMessage.created_at.desc()).limit(limit)
            messages = session.execute(stmt).scalars().all()

            # 倒序返回，保证时间顺序
            return [{"role": msg.role, "content": msg.content} for msg in reversed(messages)]

    def conversation_session_exists(self, session_id: str) -> bool:
        """Return True when at least one message exists for the given session."""
        with self.session_scope() as session:
            stmt = (
                select(ConversationMessage.id)
                .where(ConversationMessage.session_id == session_id)
                .limit(1)
            )
            return session.execute(stmt).scalar() is not None

    def get_chat_sessions(
        self,
        limit: int = 50,
        session_prefix: Optional[str] = None,
        extra_session_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取聊天会话列表（从 conversation_messages 聚合）

        Args:
            limit: Maximum number of sessions to return.
            session_prefix: If provided, only return sessions whose session_id
                starts with this prefix.  Used for per-user isolation (e.g.
                ``"telegram_12345"``).
            extra_session_ids: Optional exact session ids to include in
                addition to the scoped prefix.

        Returns:
            按最近活跃时间倒序的会话列表，每条包含 session_id, title, message_count, last_active
        """
        with self.session_scope() as session:
            normalized_prefix = None
            if session_prefix:
                normalized_prefix = session_prefix if session_prefix.endswith(":") else f"{session_prefix}:"
            exact_ids = [sid for sid in (extra_session_ids or []) if sid]

            # 聚合每个 session 的消息数和最后活跃时间
            base = (
                select(
                    ConversationMessage.session_id,
                    func.count(ConversationMessage.id).label("message_count"),
                    func.min(ConversationMessage.created_at).label("created_at"),
                    func.max(ConversationMessage.created_at).label("last_active"),
                )
            )
            conditions = []
            if normalized_prefix:
                conditions.append(ConversationMessage.session_id.startswith(normalized_prefix))
            if exact_ids:
                conditions.append(ConversationMessage.session_id.in_(exact_ids))
            if conditions:
                base = base.where(or_(*conditions))
            stmt = (
                base
                .group_by(ConversationMessage.session_id)
                .order_by(desc(func.max(ConversationMessage.created_at)))
                .limit(limit)
            )
            rows = session.execute(stmt).all()

            results = []
            for row in rows:
                sid = row.session_id
                first_user_msg = session.execute(
                    select(ConversationMessage.content)
                    .where(
                        and_(
                            ConversationMessage.session_id == sid,
                            ConversationMessage.role == "user",
                        )
                    )
                    .order_by(ConversationMessage.created_at)
                    .limit(1)
                ).scalar()
                title = (first_user_msg or "新对话")[:60]

                results.append({
                    "session_id": sid,
                    "title": title,
                    "message_count": row.message_count,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "last_active": row.last_active.isoformat() if row.last_active else None,
                })
            return results

    def get_conversation_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        获取单个会话的完整消息列表（用于前端恢复历史）
        """
        with self.session_scope() as session:
            stmt = (
                select(ConversationMessage)
                .where(ConversationMessage.session_id == session_id)
                .order_by(ConversationMessage.created_at)
                .limit(limit)
            )
            messages = session.execute(stmt).scalars().all()
            return [
                {
                    "id": str(msg.id),
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                }
                for msg in messages
            ]

    def delete_conversation_session(self, session_id: str) -> int:
        """
        删除指定会话的所有消息

        Returns:
            删除的消息数
        """
        with self.session_scope() as session:
            result = session.execute(
                delete(ConversationMessage).where(
                    ConversationMessage.session_id == session_id
                )
            )
            return result.rowcount

    # ------------------------------------------------------------------
    # LLM usage tracking
    # ------------------------------------------------------------------

    def record_llm_usage(
        self,
        call_type: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        stock_code: Optional[str] = None,
    ) -> None:
        """Append one LLM call record to llm_usage."""
        row = LLMUsage(
            call_type=call_type,
            model=model or "unknown",
            stock_code=stock_code,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        with self.session_scope() as session:
            session.add(row)

    def get_llm_usage_summary(
        self,
        from_dt: datetime,
        to_dt: datetime,
    ) -> Dict[str, Any]:
        """Return aggregated token usage between from_dt and to_dt.

        Returns a dict with keys:
          total_calls, total_tokens,
          by_call_type: list of {call_type, calls, total_tokens},
          by_model:     list of {model, calls, total_tokens}
        """
        with self.session_scope() as session:
            base_filter = and_(
                LLMUsage.called_at >= from_dt,
                LLMUsage.called_at <= to_dt,
            )

            totals = session.execute(
                select(
                    func.count(LLMUsage.id).label("calls"),
                    func.coalesce(func.sum(LLMUsage.total_tokens), 0).label("tokens"),
                ).where(base_filter)
            ).one()

            by_type_rows = session.execute(
                select(
                    LLMUsage.call_type,
                    func.count(LLMUsage.id).label("calls"),
                    func.coalesce(func.sum(LLMUsage.total_tokens), 0).label("tokens"),
                )
                .where(base_filter)
                .group_by(LLMUsage.call_type)
                .order_by(desc(func.sum(LLMUsage.total_tokens)))
            ).all()

            by_model_rows = session.execute(
                select(
                    LLMUsage.model,
                    func.count(LLMUsage.id).label("calls"),
                    func.coalesce(func.sum(LLMUsage.total_tokens), 0).label("tokens"),
                )
                .where(base_filter)
                .group_by(LLMUsage.model)
                .order_by(desc(func.sum(LLMUsage.total_tokens)))
            ).all()

        return {
            "total_calls": totals.calls,
            "total_tokens": totals.tokens,
            "by_call_type": [
                {"call_type": r.call_type, "calls": r.calls, "total_tokens": r.tokens}
                for r in by_type_rows
            ],
            "by_model": [
                {"model": r.model, "calls": r.calls, "total_tokens": r.tokens}
                for r in by_model_rows
            ],
        }