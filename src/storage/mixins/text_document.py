"""Persistence for conversation-owned original documents and text chunks."""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import delete, select

from src.storage.models import (
    AgentTextChunk,
    AgentTextDocument,
    ChatConversation,
)


class TextDocumentMixin:
    def save_text_document(
        self,
        *,
        resource: dict[str, Any],
        chunks: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        conversation_id = str(resource["conversation_id"])
        content_hash = str(resource["content_hash"])
        chunk_values = list(chunks)
        with self.session_scope() as session:
            existing = (
                session.execute(
                    select(AgentTextDocument).where(
                        AgentTextDocument.conversation_id == conversation_id,
                        AgentTextDocument.content_hash == content_hash,
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return _document_dict(existing)
            record = AgentTextDocument(
                id=str(resource["resource_id"]),
                conversation_id=conversation_id,
                run_id=str(resource.get("run_id") or ""),
                source_url=str(resource.get("source_url") or ""),
                filename=str(resource.get("filename") or "document"),
                mime_type=str(
                    resource.get("mime_type")
                    or "application/octet-stream"
                ),
                size_bytes=int(resource.get("size_bytes") or 0),
                content_hash=content_hash,
                blob_path=str(resource.get("blob_path") or ""),
                extraction_status=str(
                    resource.get("extraction_status") or "pending"
                ),
                extraction_method=resource.get("extraction_method"),
                text_length=int(resource.get("text_length") or 0),
                chunk_count=len(chunk_values),
                source_item_json=json.dumps(
                    resource.get("source_item_ref") or {},
                    ensure_ascii=False,
                    default=str,
                ),
                error_detail=resource.get("error"),
            )
            session.add(record)
            session.flush()
            for value in chunk_values:
                index = int(value.get("chunk_index") or 0)
                session.add(
                    AgentTextChunk(
                        id=f"{record.id}:{index}",
                        resource_id=record.id,
                        chunk_index=index,
                        content_hash=str(value.get("content_hash") or ""),
                        page=value.get("page"),
                        section=value.get("section"),
                        char_start=int(value.get("char_start") or 0),
                        char_end=int(value.get("char_end") or 0),
                        text_content=str(value.get("text") or ""),
                    )
                )
            session.flush()
            return _document_dict(record)

    def get_text_document(
        self,
        resource_id: str,
        *,
        tenant_id: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.get_session() as session:
            statement = (
                select(AgentTextDocument)
                .join(
                    ChatConversation,
                    ChatConversation.id
                    == AgentTextDocument.conversation_id,
                )
                .where(AgentTextDocument.id == resource_id)
            )
            if tenant_id is not None:
                statement = statement.where(
                    ChatConversation.tenant_id == tenant_id
                )
            if owner_id is not None:
                statement = statement.where(
                    ChatConversation.owner_id == owner_id
                )
            record = session.execute(statement).scalars().first()
            return _document_dict(record) if record is not None else None

    def get_text_document_chunks(
        self,
        resource_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> list[dict[str, Any]]:
        with self.get_session() as session:
            statement = select(AgentTextChunk).where(
                AgentTextChunk.resource_id == resource_id
            )
            if page_start is not None:
                statement = statement.where(
                    AgentTextChunk.page >= page_start
                )
            if page_end is not None:
                statement = statement.where(
                    AgentTextChunk.page <= page_end
                )
            records = (
                session.execute(
                    statement.order_by(
                        AgentTextChunk.chunk_index.asc()
                    )
                    .offset(max(0, int(offset)))
                    .limit(max(1, min(int(limit), 500)))
                )
                .scalars()
                .all()
            )
            return [_chunk_dict(record) for record in records]

    def delete_text_documents(
        self,
        conversation_id: str,
    ) -> list[str]:
        """Delete database rows and return candidate blob paths for GC."""
        with self.session_scope() as session:
            records = (
                session.execute(
                    select(AgentTextDocument).where(
                        AgentTextDocument.conversation_id == conversation_id
                    )
                )
                .scalars()
                .all()
            )
            resource_ids = [record.id for record in records]
            blob_paths = [record.blob_path for record in records if record.blob_path]
            if resource_ids:
                session.execute(
                    delete(AgentTextChunk).where(
                        AgentTextChunk.resource_id.in_(resource_ids)
                    )
                )
            session.execute(
                delete(AgentTextDocument).where(
                    AgentTextDocument.conversation_id == conversation_id
                )
            )
            return blob_paths

    def text_document_blob_is_referenced(
        self,
        blob_path: str,
    ) -> bool:
        with self.get_session() as session:
            return (
                session.execute(
                    select(AgentTextDocument.id)
                    .where(AgentTextDocument.blob_path == blob_path)
                    .limit(1)
                ).first()
                is not None
            )


def _document_dict(record: AgentTextDocument) -> dict[str, Any]:
    try:
        source_item_ref = json.loads(record.source_item_json or "{}")
    except (TypeError, ValueError):
        source_item_ref = {}
    return {
        "resource_id": record.id,
        "conversation_id": record.conversation_id,
        "run_id": record.run_id,
        "source_url": record.source_url,
        "filename": record.filename,
        "mime_type": record.mime_type,
        "size_bytes": int(record.size_bytes or 0),
        "content_hash": record.content_hash,
        "blob_path": record.blob_path,
        "extraction_status": record.extraction_status,
        "extraction_method": record.extraction_method,
        "text_length": int(record.text_length or 0),
        "chunk_count": int(record.chunk_count or 0),
        "source_item_ref": source_item_ref or None,
        "error": record.error_detail,
    }


def _chunk_dict(record: AgentTextChunk) -> dict[str, Any]:
    return {
        "chunk_index": int(record.chunk_index),
        "text": record.text_content,
        "content_hash": record.content_hash,
        "page": record.page,
        "section": record.section,
        "char_start": int(record.char_start or 0),
        "char_end": int(record.char_end or 0),
    }


__all__ = ["TextDocumentMixin"]
