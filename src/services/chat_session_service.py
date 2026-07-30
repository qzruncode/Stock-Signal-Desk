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
                if isinstance(thread_state, dict):
                    thread_state.pop(self.AGENT_CONTEXT_KEY, None)
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
        if not conversation:
            return {}
        raw_context = getattr(conversation, "agent_context_json", None)
        if raw_context:
            try:
                value = json.loads(raw_context)
            except (TypeError, ValueError):
                value = None
            if isinstance(value, dict):
                return value

        raw = getattr(conversation, "thread_state_json", None)
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

    def compose_request_with_server_history(
        self,
        conversation_id: str,
        incoming_messages: List[Dict[str, Any]],
        *,
        parent_message_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Attach a fresh client turn to the canonical server transcript."""
        detail = self.get_conversation(conversation_id) or {}
        historical = [
            {
                "id": str(message.get("id") or ""),
                "role": str(message.get("role") or ""),
                "content": str(message.get("content") or ""),
                "created_at": message.get("created_at"),
            }
            for message in detail.get("messages") or []
            if (
                isinstance(message, dict)
                and not str(message.get("id") or "").endswith(
                    "-assistant-pending"
                )
            )
        ]
        clean_parent = str(parent_message_id or "").strip()
        if clean_parent:
            parent_index = next(
                (
                    index
                    for index, message in enumerate(historical)
                    if message["id"] == clean_parent
                ),
                None,
            )
            if parent_index is not None:
                historical = historical[:parent_index + 1]
        elif parent_message_id is not None:
            historical = []

        incoming_ids = {
            str(message.get("id") or message.get("unstable_id") or "").strip()
            for message in incoming_messages
            if isinstance(message, dict)
        }
        if incoming_ids:
            historical = [
                message
                for message in historical
                if message["id"] not in incoming_ids
            ]
        return [*historical, *incoming_messages]

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
        messages: Optional[List[Dict[str, Any]]],
        *,
        thread_state: Optional[Dict[str, Any]] = None,
        agent_context: Optional[Dict[str, Any]] = None,
        prune_agent_context_to_messages: bool = False,
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

        normalized_messages = (
            self._normalize_messages(messages)
            if messages is not None
            else None
        )
        effective_thread_state: Optional[Dict[str, Any]] = None
        if thread_state is not None:
            effective_thread_state = dict(thread_state)
            legacy_context_value = effective_thread_state.pop(
                self.AGENT_CONTEXT_KEY, ...
            )
            if legacy_context_value is None:
                prune_agent_context_to_messages = True

        thread_state_json = None
        if effective_thread_state is not None:
            try:
                thread_state_json = json.dumps(effective_thread_state, ensure_ascii=False)
            except (TypeError, ValueError):
                thread_state_json = None

        next_agent_context = agent_context
        artifact_ids_to_keep: set[str] | None = None
        run_ids_to_keep: set[str] | None = None
        if next_agent_context is None and prune_agent_context_to_messages:
            existing_context = self.get_agent_context(conversation_id)
            if (
                isinstance(existing_context, dict)
                and str(existing_context.get("version") or "") == "3"
            ):
                from src.agent.orchestrator_v2.state import ConversationContextV2

                parsed_context = ConversationContextV2.from_value(existing_context)
                next_agent_context = parsed_context.retain_for_messages(
                    normalized_messages or []
                ).model_dump(mode="json")
                artifact_ids_to_keep = {
                    artifact_id
                    for turn in next_agent_context.get("turns") or []
                    for task in turn.get("tasks") or []
                    for artifact_id in task.get("artifact_refs") or []
                } | {
                    str(reference.get("artifact_id") or "")
                    for turn in next_agent_context.get("turns") or []
                    for reference in turn.get("terminal_artifacts") or []
                    if str(reference.get("artifact_id") or "")
                }
                run_ids_to_keep = {
                    str(turn.get("run_id") or "")
                    for turn in next_agent_context.get("turns") or []
                    if str(turn.get("run_id") or "")
                }
            else:
                from src.agent.conversation_context import (
                    ConversationContext,
                    recover_context_from_thread_state,
                )

                if existing_context:
                    parsed_context = ConversationContext.from_value(existing_context)
                else:
                    parsed_context = recover_context_from_thread_state(
                        thread_state or existing_thread_state
                    )
                next_agent_context = parsed_context.retain_for_messages(
                    normalized_messages or []
                ).model_dump()

        agent_context_json = None
        if next_agent_context is not None:
            try:
                agent_context_json = json.dumps(
                    next_agent_context, ensure_ascii=False,
                )
            except (TypeError, ValueError):
                agent_context_json = None

        # Thread state is a presentation snapshot. When the caller does not
        # explicitly provide messages, it must never replace the canonical
        # backend transcript produced by the Agent run.
        if messages is None:
            if thread_state_json is not None:
                self.db.update_chat_thread_state(
                    conversation_id,
                    thread_state_json,
                    agent_context_json=agent_context_json,
                    updated_at=datetime.now(),
                )
            elif agent_context_json is not None:
                self.db.update_chat_agent_context(
                    conversation_id,
                    agent_context_json,
                    updated_at=datetime.now(),
                )
            if artifact_ids_to_keep is not None:
                self.db.prune_agent_artifacts(
                    conversation_id,
                    artifact_ids_to_keep,
                )
                self.db.prune_agent_run_traces(
                    conversation_id,
                    tuple(run_ids_to_keep or ()),
                )
            return self.get_conversation(conversation_id)

        assert normalized_messages is not None

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
            agent_context_json=agent_context_json,
            updated_at=datetime.now(),
        )
        if artifact_ids_to_keep is not None:
            self.db.prune_agent_artifacts(
                conversation_id,
                artifact_ids_to_keep,
            )
            self.db.prune_agent_run_traces(
                conversation_id,
                tuple(run_ids_to_keep or ()),
            )

        if not skip_title and getattr(conversation, "title_source", None) != "manual":
            self.db.update_chat_conversation(
                conversation_id,
                title=self.generate_title(first_user_text) if first_user_text else self.DEFAULT_TITLE,
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
                    "id": str(
                        raw.get("id")
                        or raw.get("unstable_id")
                        or uuid.uuid4().hex
                    ),
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
