from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.storage import DatabaseManager
from src.agent.progress import is_non_answer_agent_message, strip_agent_progress
from src.agent.langgraph_runtime.evidence_identity import strip_internal_evidence_diagnostic


@dataclass(frozen=True)
class PreparedChatHistory:
    messages: List[Dict[str, Any]]
    replace_checkpoint: bool


class ChatSessionService:
    """对话会话管理服务。"""

    DEFAULT_TITLE = "新对话"
    AGENT_CONTEXT_KEY = "agent_context"

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        *,
        tenant_id: str = "local",
        owner_id: str = "admin",
    ):
        self.db = db_manager or DatabaseManager.get_instance()
        self.tenant_id = str(tenant_id or "local")[:64]
        self.owner_id = str(owner_id or "admin")[:128]

    @property
    def _tenant_id(self) -> str:
        # A few focused tests and legacy extensions construct the service
        # without invoking __init__. Defaulting preserves the single-user
        # boundary while normal request paths always set an explicit value.
        return str(getattr(self, "tenant_id", "local") or "local")[:64]

    @property
    def _owner_id(self) -> str:
        return str(getattr(self, "owner_id", "admin") or "admin")[:128]

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
        conversation = self.db.create_chat_conversation(
            uuid.uuid4().hex,
            title=self.DEFAULT_TITLE,
            tenant_id=self._tenant_id,
            owner_id=self._owner_id,
        )
        return conversation.to_dict()

    def list_conversations(self, page: int = 1, limit: int = 50) -> Dict[str, Any]:
        safe_page = max(page, 1)
        safe_limit = min(max(limit, 1), 100)
        offset = (safe_page - 1) * safe_limit
        items, total = self.db.list_chat_conversations(
            offset=offset,
            limit=safe_limit,
            tenant_id=self._tenant_id,
            owner_id=self._owner_id,
        )
        return {
            "items": [item.to_dict() for item in items],
            "total": total,
            "page": safe_page,
            "limit": safe_limit,
        }

    def list_conversation_ids(self) -> List[str]:
        """Return all visible conversation ids for this tenant and owner."""
        return self.db.list_chat_conversation_ids(
            tenant_id=self._tenant_id,
            owner_id=self._owner_id,
        )

    def get_conversation(
        self,
        conversation_id: str,
        *,
        include_thread_state: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Read the canonical transcript without reviving opaque UI state.

        The persisted ``thread_state_json`` is an old assistant-ui rendering
        export, not Agent state.  It can contain complete historical tool
        payloads, so active request paths must not deserialize it.  The opt-in
        flag retains a read-only compatibility escape hatch without letting
        that payload re-enter the live chat path.
        """
        conversation = self.db.get_chat_conversation(
            conversation_id,
            tenant_id=self._tenant_id,
            owner_id=self._owner_id,
        )
        if not conversation:
            return None
        messages = []
        for message in self.db.get_chat_messages(conversation_id):
            role = str(getattr(message, "role", ""))
            content = getattr(message, "content", "")
            if role == "assistant" and is_non_answer_agent_message(content):
                continue
            serialized = message.to_dict()
            if role == "assistant":
                serialized["content"] = strip_internal_evidence_diagnostic(content)
            messages.append(serialized)
        thread_state = None
        if include_thread_state and getattr(conversation, "thread_state_json", None):
            try:
                thread_state = json.loads(conversation.thread_state_json)
                if isinstance(thread_state, dict):
                    thread_state.pop(self.AGENT_CONTEXT_KEY, None)
            except (TypeError, ValueError):
                thread_state = None
        payload = {
            **conversation.to_dict(),
            "messages": messages,
        }
        if include_thread_state:
            payload["thread_state"] = thread_state
        return payload

    def get_agent_context(self, conversation_id: str) -> Dict[str, Any]:
        """Legacy orchestration JSON is intentionally never executable.

        The native LangGraph checkpointer is the only control-state source.
        This compatibility method remains for older callers and therefore
        always returns an empty context.
        """
        del conversation_id
        return {}

    def rename_conversation(self, conversation_id: str, title: str) -> Optional[Dict[str, Any]]:
        if not self.get_conversation(conversation_id):
            return None
        conversation = self.db.update_chat_conversation(
            conversation_id,
            title=self._normalize_title(title),
            title_source="manual",
        )
        return conversation.to_dict() if conversation else None

    def delete_conversation(self, conversation_id: str) -> int:
        if not self.get_conversation(conversation_id):
            return 0
        from src.services.text_document_service import (
            delete_conversation_document_blobs,
        )

        delete_conversation_document_blobs(
            self.db,
            conversation_id,
        )
        return self.db.delete_chat_conversation(conversation_id)

    def ensure_conversation(self, conversation_id: Optional[str]) -> Dict[str, Any]:
        if conversation_id:
            existing = self.db.get_chat_conversation(
                conversation_id,
                tenant_id=self._tenant_id,
                owner_id=self._owner_id,
            )
            if existing:
                return existing.to_dict()
        return self.create_conversation()

    def compose_request_with_server_history(
        self,
        conversation_id: str,
        incoming_messages: List[Dict[str, Any]],
        *,
        parent_message_id: Optional[str] = None,
        edit_message_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Compatibility facade for callers that only need the transcript."""
        return self.prepare_request_with_server_history(
            conversation_id,
            incoming_messages,
            parent_message_id=parent_message_id,
            edit_message_id=edit_message_id,
        ).messages

    def prepare_request_with_server_history(
        self,
        conversation_id: str,
        incoming_messages: List[Dict[str, Any]],
        *,
        parent_message_id: Optional[str] = None,
        edit_message_id: Optional[str] = None,
    ) -> PreparedChatHistory:
        """Resolve transcript and checkpoint branch from one canonical read.

        Reload reuses the original user id; edit supplies its source id. Both
        replace that turn and its descendants, including derived graph state.
        """
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
                and not str(message.get("id") or "").endswith("-assistant-pending")
                and not (
                    str(message.get("role") or "") == "assistant"
                    and is_non_answer_agent_message(message.get("content", ""))
                )
            )
        ]
        clean_parent = str(parent_message_id or "").strip()
        clean_edit = str(edit_message_id or "").strip()
        existing_user_ids = {message["id"] for message in historical if message["role"] == "user"}
        if not clean_edit:
            clean_edit = next(
                (
                    message_id
                    for message in reversed(incoming_messages)
                    if str(message.get("role") or "").lower() == "user"
                    and (message_id := str(message.get("id") or message.get("unstable_id") or "").strip())
                    and message_id in existing_user_ids
                ),
                "",
            )
        original_length = len(historical)
        if clean_edit:
            edit_index = next(
                (index for index, message in enumerate(historical) if message["id"] == clean_edit),
                None,
            )
            if edit_index is not None:
                historical = historical[:edit_index]
            elif clean_parent:
                # A repeated edit can arrive after an earlier edit already
                # replaced the source id.  Preserve the same branch boundary
                # rather than appending a duplicate turn.
                parent_index = next(
                    (index for index, message in enumerate(historical) if message["id"] == clean_parent),
                    None,
                )
                historical = historical[: parent_index + 1] if parent_index is not None else []
            else:
                historical = []
        elif clean_parent:
            parent_index = next(
                (index for index, message in enumerate(historical) if message["id"] == clean_parent),
                None,
            )
            if parent_index is not None:
                historical = historical[: parent_index + 1]
        elif parent_message_id is not None:
            historical = []

        incoming_ids = {
            str(message.get("id") or message.get("unstable_id") or "").strip()
            for message in incoming_messages
            if isinstance(message, dict)
        }
        if incoming_ids:
            historical = [message for message in historical if message["id"] not in incoming_ids]
        return PreparedChatHistory(
            messages=[*historical, *incoming_messages],
            replace_checkpoint=bool(clean_edit) or len(historical) < original_length,
        )

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
        conversation = self.db.get_chat_conversation(
            conversation_id,
            tenant_id=self._tenant_id,
            owner_id=self._owner_id,
        )
        if not conversation:
            return None

        normalized_messages = self._normalize_messages(messages) if messages is not None else None
        effective_thread_state: Optional[Dict[str, Any]] = None
        if thread_state is not None:
            effective_thread_state = dict(thread_state)
            legacy_context_value = effective_thread_state.pop(self.AGENT_CONTEXT_KEY, ...)
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
            # LangGraph checkpoints are the only orchestration state. Legacy
            # context JSON remains readable with old history but is never
            # migrated, pruned, or re-entered into a new turn.
            next_agent_context = {}

        agent_context_json = None
        if next_agent_context is not None:
            try:
                agent_context_json = json.dumps(
                    next_agent_context,
                    ensure_ascii=False,
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
                if is_non_answer_agent_message(content):
                    continue
            if not content:
                continue
            normalized_messages.append(
                {
                    "id": str(raw.get("id") or raw.get("unstable_id") or uuid.uuid4().hex),
                    "role": role,
                    "content": content,
                    "created_at": raw.get("created_at") or datetime.now().isoformat(),
                }
            )
        return normalized_messages

    def normalize_messages(
        self,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Public normalization boundary for atomic runtime commits."""
        return self._normalize_messages(messages)

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
