from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.storage import DatabaseManager
from src.agent.progress import strip_agent_progress


class ChatSessionService:
    """对话会话管理服务。"""

    DEFAULT_TITLE = "新对话"
    AGENT_CONTEXT_KEY = "agent_context"

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    @staticmethod
    def _normalize_title(title: Optional[str]) -> str:
        text = re.sub(r"\s+", " ", str(title or "")).strip()
        return (text or ChatSessionService.DEFAULT_TITLE)[:120]

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    chunks.append(str(item.get("text") or ""))
                elif isinstance(item, str):
                    chunks.append(item)
            return "\n".join(chunk for chunk in chunks if chunk).strip()
        return str(content or "").strip()

    @classmethod
    def generate_title(cls, user_text: str) -> str:
        text = cls._extract_text(user_text)
        text = re.sub(r"```[\s\S]*?```", " ", text)
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s+", " ", text).strip(" ，。；：,.!?！？-")
        if not text:
            return cls.DEFAULT_TITLE
        first_clause = re.split(r"[。！？!?；;\n]", text, maxsplit=1)[0].strip()
        return (first_clause or text)[:24]

    def create_conversation(self) -> Dict[str, Any]:
        conversation = self.db.create_chat_conversation(uuid.uuid4().hex, title=self.DEFAULT_TITLE)
        return conversation.to_dict()

    def list_conversations(self, page: int = 1, limit: int = 50) -> Dict[str, Any]:
        safe_page = max(page, 1)
        safe_limit = min(max(limit, 1), 100)
        offset = (safe_page - 1) * safe_limit
        items, total = self.db.list_chat_conversations(offset=offset, limit=safe_limit)
        return {
            "items": [item.to_dict() for item in items],
            "total": total,
            "page": safe_page,
            "limit": safe_limit,
        }

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        conversation = self.db.get_chat_conversation(conversation_id)
        if not conversation:
            return None
        messages = self.db.get_chat_messages(conversation_id)
        thread_state = None
        if getattr(conversation, "thread_state_json", None):
            try:
                thread_state = json.loads(conversation.thread_state_json)
            except (TypeError, ValueError):
                thread_state = None
        return {
            **conversation.to_dict(),
            "messages": [message.to_dict() for message in messages],
            "thread_state": thread_state,
        }

    def get_agent_context(self, conversation_id: str) -> Dict[str, Any]:
        """Read server-owned semantic context without inspecting rendered answers."""
        conversation = self.db.get_chat_conversation(conversation_id)
        raw = getattr(conversation, "thread_state_json", None) if conversation else None
        if not raw:
            return {}
        try:
            thread_state = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(thread_state, dict):
            return {}
        value = thread_state.get(self.AGENT_CONTEXT_KEY)
        if isinstance(value, dict):
            return value
        from src.agent.conversation_context import recover_context_from_thread_state

        recovered = recover_context_from_thread_state(thread_state)
        return recovered.model_dump() if recovered.turns else {}

    def rename_conversation(self, conversation_id: str, title: str) -> Optional[Dict[str, Any]]:
        conversation = self.db.update_chat_conversation(
            conversation_id,
            title=self._normalize_title(title),
            title_source="manual",
        )
        return conversation.to_dict() if conversation else None

    def delete_conversation(self, conversation_id: str) -> int:
        return self.db.delete_chat_conversation(conversation_id)

    def ensure_conversation(self, conversation_id: Optional[str]) -> Dict[str, Any]:
        if conversation_id:
            existing = self.db.get_chat_conversation(conversation_id)
            if existing:
                return existing.to_dict()
        return self.create_conversation()

    def save_partial_assistant_text(self, conversation_id: str, assistant_text: str) -> None:
        """增量保存"进行中"的 assistant 文本(刷新后可恢复已生成部分)。

        用固定 message_id(upsert)反复更新同一条记录,不删既有消息。
        生成完成后,save_conversation_snapshot 的全量覆盖会修正/替换它。
        """
        text = (assistant_text or "").strip()
        if not text:
            return
        message_id = f"{conversation_id}-assistant-pending"
        self.db.upsert_partial_assistant_message(conversation_id, message_id, text)

    def save_conversation_snapshot(
        self,
        conversation_id: str,
        messages: List[Dict[str, Any]],
        *,
        thread_state: Optional[Dict[str, Any]] = None,
        agent_context: Optional[Dict[str, Any]] = None,
        skip_title: bool = False,
    ) -> Optional[Dict[str, Any]]:
        conversation = self.db.get_chat_conversation(conversation_id)
        if not conversation:
            return None

        existing_thread_state: Dict[str, Any] = {}
        if getattr(conversation, "thread_state_json", None):
            try:
                parsed = json.loads(conversation.thread_state_json)
                if isinstance(parsed, dict):
                    existing_thread_state = parsed
            except (TypeError, ValueError):
                existing_thread_state = {}

        effective_thread_state: Optional[Dict[str, Any]] = None
        if thread_state is not None:
            effective_thread_state = dict(thread_state)
            previous_agent_context = existing_thread_state.get(self.AGENT_CONTEXT_KEY)
            if self.AGENT_CONTEXT_KEY not in effective_thread_state and isinstance(
                previous_agent_context, dict
            ):
                effective_thread_state[self.AGENT_CONTEXT_KEY] = previous_agent_context
        elif agent_context is not None:
            effective_thread_state = dict(existing_thread_state)
        if effective_thread_state is not None and agent_context is not None:
            effective_thread_state[self.AGENT_CONTEXT_KEY] = agent_context

        normalized_messages: List[Dict[str, Any]] = []
        if thread_state is not None and effective_thread_state:
            normalized_messages = self._normalize_thread_state_messages(effective_thread_state)
        if not normalized_messages:
            normalized_messages = self._normalize_messages(messages)

        thread_state_json = None
        if effective_thread_state is not None:
            try:
                thread_state_json = json.dumps(effective_thread_state, ensure_ascii=False)
            except (TypeError, ValueError):
                thread_state_json = None

        first_user_text = ""
        latest_preview = ""
        for message in normalized_messages:
            role = message["role"]
            content = message["content"]
            if role == "user" and not first_user_text:
                first_user_text = content
            latest_preview = content

        self.db.replace_chat_messages(
            conversation_id,
            normalized_messages,
            preview_text=(latest_preview[:200] if latest_preview else None),
            thread_state_json=thread_state_json,
            updated_at=datetime.now(),
        )

        if not skip_title and getattr(conversation, "title_source", None) != "manual" and first_user_text:
            self.db.update_chat_conversation(
                conversation_id,
                title=self.generate_title(first_user_text),
                title_source="auto",
                preview_text=(latest_preview[:200] if latest_preview else None),
                updated_at=datetime.now(),
            )

        return self.get_conversation(conversation_id)

    def _normalize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized_messages: List[Dict[str, Any]] = []
        for raw in messages:
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "user").strip() or "user"
            content = self._extract_text(raw.get("content"))
            if role == "assistant":
                content = strip_agent_progress(content)
            if not content:
                continue
            normalized_messages.append(
                {
                    "id": str(raw.get("id") or uuid.uuid4().hex),
                    "role": role,
                    "content": content,
                    "created_at": raw.get("created_at") or datetime.now().isoformat(),
                }
            )
        return normalized_messages

    def _normalize_thread_state_messages(self, thread_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        entries = thread_state.get("messages")
        if not isinstance(entries, list):
            return []

        normalized_messages: List[Dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            message = item.get("message")
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").strip() or "user"
            content = self._extract_thread_message_text(message)
            if role == "assistant":
                content = strip_agent_progress(content)
            if not content:
                continue
            normalized_messages.append(
                {
                    "id": str(message.get("id") or uuid.uuid4().hex),
                    "role": role,
                    "content": content,
                    "created_at": message.get("createdAt") or datetime.now().isoformat(),
                }
            )
        return normalized_messages

    def _extract_thread_message_text(self, message: Dict[str, Any]) -> str:
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""

        chunks: List[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    chunks.append(text)
        return "\n".join(chunks).strip()
