"""Durable PDF knowledge-base metadata and upload lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.rag.pdf_processing import MAX_PDF_PAGES, MAX_UPLOAD_BYTES, PdfProcessingError, count_pdf_pages
from src.storage import DatabaseManager
from src.storage.models import (
    RagChunk,
    RagDocument,
    RagDocumentSource,
    RagIndexVersion,
    RagIngestionTask,
    RagKnowledgeBase,
    RagPage,
)

logger = logging.getLogger(__name__)


class RagServiceError(RuntimeError):
    def __init__(self, message: str, *, code: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def rag_storage_root() -> Path:
    root = Path(os.getenv("RAG_STORAGE_PATH", "data/rag")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_rag_blob_path(blob_path: str | Path) -> Path:
    """Resolve a portable blob key while retaining compatibility with legacy absolute paths."""
    candidate = Path(blob_path).expanduser()
    if candidate.is_absolute():
        return candidate
    root = rag_storage_root()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("RAG blob key escapes the configured storage root")
    return resolved


def _blob_storage_key(path: Path) -> str:
    return path.resolve().relative_to(rag_storage_root()).as_posix()


def _scope_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]


class RagKnowledgeBaseService:
    """Scope every operation to the tenant/owner already assigned to the request."""

    def __init__(self, db_manager: DatabaseManager | None = None) -> None:
        self.db = db_manager or DatabaseManager.get_instance()

    def _session(self) -> Session:
        return self.db.get_session()

    @staticmethod
    def _kb_query(tenant_id: str, owner_id: str):
        return select(RagKnowledgeBase).where(
            RagKnowledgeBase.tenant_id == tenant_id,
            RagKnowledgeBase.owner_id == owner_id,
            RagKnowledgeBase.status != "deleted",
        )

    def assert_knowledge_base(self, knowledge_base_id: str, *, tenant_id: str, owner_id: str) -> dict[str, Any]:
        session = self._session()
        try:
            item = session.scalar(
                self._kb_query(tenant_id, owner_id).where(RagKnowledgeBase.id == knowledge_base_id)
            )
            if item is None or item.status != "active":
                raise RagServiceError("知识库不存在或不可用。", code="knowledge_base_not_found", status_code=404)
            return self._kb_payload(item)
        finally:
            session.close()

    def create_knowledge_base(
        self,
        *,
        name: str,
        description: str = "",
        tenant_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        normalized_name = " ".join(str(name or "").split())
        if not normalized_name or len(normalized_name) > 160:
            raise RagServiceError("知识库名称长度需为 1–160 个字符。", code="invalid_name")
        kb = RagKnowledgeBase(
            id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            owner_id=owner_id,
            name=normalized_name,
            description=str(description or "").strip()[:2_000],
        )
        session = self._session()
        try:
            session.add(kb)
            session.commit()
            session.refresh(kb)
            return self._kb_payload(kb)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_knowledge_bases(self, *, tenant_id: str, owner_id: str) -> list[dict[str, Any]]:
        session = self._session()
        try:
            kbs = session.scalars(
                self._kb_query(tenant_id, owner_id).order_by(RagKnowledgeBase.updated_at.desc())
            ).all()
            payloads = [self._kb_payload(kb) for kb in kbs]
            if not payloads:
                return []
            counts = session.execute(
                select(
                    RagDocument.knowledge_base_id,
                    func.count(RagDocument.id),
                    func.sum(case((RagDocument.status == "ready", 1), else_=0)),
                )
                .where(
                    RagDocument.tenant_id == tenant_id,
                    RagDocument.owner_id == owner_id,
                    RagDocument.status != "deleted",
                    RagDocument.knowledge_base_id.in_([item["id"] for item in payloads]),
                )
                .group_by(RagDocument.knowledge_base_id)
            ).all()
            by_id = {str(kb_id): (int(count or 0), int(ready or 0)) for kb_id, count, ready in counts}
            for item in payloads:
                item["document_count"], item["ready_document_count"] = by_id.get(item["id"], (0, 0))
            return payloads
        finally:
            session.close()

    def list_searchable_knowledge_bases(
        self,
        *,
        tenant_id: str,
        owner_id: str,
    ) -> list[dict[str, Any]]:
        """Return only caller-owned libraries with at least one active PDF index."""
        candidates = [
            item
            for item in self.list_knowledge_bases(tenant_id=tenant_id, owner_id=owner_id)
            if item.get("status") == "active" and int(item.get("ready_document_count") or 0) > 0
        ]
        if not candidates:
            return []
        indexed_ids = {
            str(item.get("knowledge_base_id") or "")
            for item in self.active_index_versions(
                [str(item["id"]) for item in candidates],
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
            if str(item.get("knowledge_base_id") or "").strip()
        }
        return [
            {
                "id": str(item["id"]),
                "name": str(item.get("name") or "")[:160],
                "description": str(item.get("description") or "")[:500],
                "status": "searchable",
                "ready_document_count": int(item.get("ready_document_count") or 0),
            }
            for item in candidates
            if str(item.get("id") or "") in indexed_ids
        ]

    def list_documents(
        self,
        knowledge_base_id: str,
        *,
        tenant_id: str,
        owner_id: str,
    ) -> list[dict[str, Any]]:
        self.assert_knowledge_base(knowledge_base_id, tenant_id=tenant_id, owner_id=owner_id)
        session = self._session()
        try:
            documents = session.scalars(
                select(RagDocument)
                .where(
                    RagDocument.knowledge_base_id == knowledge_base_id,
                    RagDocument.tenant_id == tenant_id,
                    RagDocument.owner_id == owner_id,
                    RagDocument.status != "deleted",
                )
                .order_by(RagDocument.created_at.desc())
            ).all()
            return [self._document_payload(session, document) for document in documents]
        finally:
            session.close()

    def get_document(
        self,
        document_id: str,
        *,
        tenant_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        session = self._session()
        try:
            document = self._owned_document(session, document_id, tenant_id, owner_id)
            return self._document_payload(session, document, include_errors=True)
        finally:
            session.close()

    async def upload_pdf(
        self,
        knowledge_base_id: str,
        filename: str,
        stream: Any,
        *,
        tenant_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        self.assert_knowledge_base(knowledge_base_id, tenant_id=tenant_id, owner_id=owner_id)
        safe_filename = str(filename or "document.pdf").replace("\\", "/").rsplit("/", 1)[-1][:500]
        if not safe_filename.lower().endswith(".pdf"):
            raise RagServiceError("只支持上传 PDF 文件。", code="unsupported_file_type", status_code=415)
        root = rag_storage_root()
        incoming = root / ".incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=incoming)
        temp_path = Path(temp_name)
        digest = hashlib.sha256()
        size_bytes = 0
        prefix = bytearray()
        try:
            with os.fdopen(fd, "wb") as output:
                while True:
                    chunk = await stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > MAX_UPLOAD_BYTES:
                        raise RagServiceError("PDF 超过 50 MB 上传限制。", code="file_too_large", status_code=413)
                    if len(prefix) < 1_024:
                        prefix.extend(chunk[: 1_024 - len(prefix)])
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if b"%PDF-" not in prefix:
                raise RagServiceError("文件内容不是有效 PDF。", code="invalid_pdf_signature", status_code=415)
            if size_bytes < 16:
                raise RagServiceError("PDF 文件内容过短或已损坏。", code="invalid_pdf", status_code=400)
            page_count = await asyncio.to_thread(count_pdf_pages, temp_path, max_pages=MAX_PDF_PAGES)
            content_hash = digest.hexdigest()
            return await asyncio.to_thread(
                self._register_uploaded_file,
                temp_path,
                safe_filename,
                size_bytes,
                content_hash,
                page_count,
                knowledge_base_id,
                tenant_id,
                owner_id,
            )
        except PdfProcessingError as exc:
            raise RagServiceError(str(exc), code=exc.code, status_code=415) from exc
        finally:
            temp_path.unlink(missing_ok=True)

    def upload_pdf_path(
        self,
        knowledge_base_id: str,
        filename: str,
        source_path: Path,
        *,
        tenant_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        """Validate and register an already-streamed PDF without buffering it in memory."""
        self.assert_knowledge_base(knowledge_base_id, tenant_id=tenant_id, owner_id=owner_id)
        safe_filename = str(filename or "document.pdf").replace("\\", "/").rsplit("/", 1)[-1][:500]
        if not safe_filename.lower().endswith(".pdf"):
            raise RagServiceError("只支持上传 PDF 文件。", code="unsupported_file_type", status_code=415)
        path = Path(source_path)
        if not path.is_file():
            raise RagServiceError("下载的 PDF 原件不存在。", code="blob_missing", status_code=410)
        size_bytes = path.stat().st_size
        if size_bytes > MAX_UPLOAD_BYTES:
            raise RagServiceError("PDF 超过 50 MB 上传限制。", code="file_too_large", status_code=413)
        if size_bytes < 16:
            raise RagServiceError("PDF 文件内容过短或已损坏。", code="invalid_pdf", status_code=400)
        digest = hashlib.sha256()
        prefix = bytearray()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                if len(prefix) < 1_024:
                    prefix.extend(chunk[: 1_024 - len(prefix)])
                digest.update(chunk)
        if b"%PDF-" not in prefix:
            raise RagServiceError("文件内容不是有效 PDF。", code="invalid_pdf_signature", status_code=415)
        try:
            page_count = count_pdf_pages(path, max_pages=MAX_PDF_PAGES)
        except PdfProcessingError as exc:
            raise RagServiceError(str(exc), code=exc.code, status_code=415) from exc
        return self._register_uploaded_file(
            path,
            safe_filename,
            size_bytes,
            digest.hexdigest(),
            page_count,
            knowledge_base_id,
            tenant_id,
            owner_id,
        )

    def set_document_source(
        self,
        document_id: str,
        source: dict[str, Any],
        *,
        tenant_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        """Persist official exchange provenance after the original PDF is registered."""
        session = self._session()
        try:
            document = self._owned_document(session, document_id, tenant_id, owner_id)
            row = session.get(RagDocumentSource, document.id)
            if row is None:
                row = RagDocumentSource(document_id=document.id)
                session.add(row)
            for field in (
                "provider",
                "exchange",
                "security_code",
                "security_name",
                "report_type",
                "report_period",
                "announcement_id",
                "announcement_title",
                "published_at",
                "pdf_url",
                "announcement_url",
            ):
                setattr(row, field, str(source.get(field) or "")[:5_000])
            session.commit()
            return self._document_payload(session, document)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _register_uploaded_file(
        self,
        temp_path: Path,
        filename: str,
        size_bytes: int,
        content_hash: str,
        page_count: int,
        knowledge_base_id: str,
        tenant_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        session = self._session()
        final_path: Path | None = None
        created_blob = False
        try:
            kb = session.scalar(
                self._kb_query(tenant_id, owner_id).where(
                    RagKnowledgeBase.id == knowledge_base_id,
                    RagKnowledgeBase.status == "active",
                )
            )
            if kb is None:
                raise RagServiceError("知识库不存在或不可用。", code="knowledge_base_not_found", status_code=404)
            existing = session.scalar(
                select(RagDocument).where(
                    RagDocument.knowledge_base_id == knowledge_base_id,
                    RagDocument.content_hash == content_hash,
                    RagDocument.tenant_id == tenant_id,
                    RagDocument.owner_id == owner_id,
                )
            )
            if existing is not None and existing.status != "deleted":
                return {**self._document_payload(session, existing), "duplicate": True}

            final_path = (
                rag_storage_root()
                / "blobs"
                / _scope_hash(tenant_id)
                / _scope_hash(owner_id)
                / knowledge_base_id
                / f"{content_hash}.pdf"
            )
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.exists():
                existing_digest = hashlib.sha256()
                with final_path.open("rb") as stored_file:
                    for stored_chunk in iter(lambda: stored_file.read(1024 * 1024), b""):
                        existing_digest.update(stored_chunk)
                if final_path.stat().st_size != size_bytes or existing_digest.hexdigest() != content_hash:
                    raise RagServiceError("原文件存储冲突，任务未创建。", code="blob_conflict", status_code=500)
            else:
                os.replace(temp_path, final_path)
                created_blob = True

            now = datetime.now()
            document_id = existing.id if existing is not None else uuid.uuid4().hex
            task = RagIngestionTask(
                id=uuid.uuid4().hex,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                operation="ingest",
                status="queued",
                stage="queued",
                progress=0,
                created_at=now,
                updated_at=now,
            )
            if existing is not None:
                claimed = session.execute(
                    update(RagDocument)
                    .where(RagDocument.id == existing.id, RagDocument.status == "deleted")
                    .values(
                        filename=filename,
                        mime_type="application/pdf",
                        size_bytes=size_bytes,
                        blob_path=_blob_storage_key(final_path),
                        status="queued",
                        page_count=page_count,
                        text_length=0,
                        chunk_count=0,
                        active_index_version_id=None,
                        current_task_id=task.id,
                        error_code=None,
                        error_detail=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                if claimed.rowcount != 1:
                    session.rollback()
                    concurrent = session.scalar(
                        select(RagDocument).where(
                            RagDocument.knowledge_base_id == knowledge_base_id,
                            RagDocument.content_hash == content_hash,
                            RagDocument.tenant_id == tenant_id,
                            RagDocument.owner_id == owner_id,
                            RagDocument.status != "deleted",
                        )
                    )
                    if concurrent is not None:
                        return {**self._document_payload(session, concurrent), "duplicate": True}
                    raise RagServiceError(
                        "文档状态刚发生变化，请刷新后重试。",
                        code="upload_conflict",
                        status_code=409,
                    )
                document = session.get(RagDocument, existing.id)
                if document is None:
                    raise RagServiceError("文档状态刚发生变化，请刷新后重试。", code="upload_conflict", status_code=409)
            else:
                document = RagDocument(
                    id=document_id,
                    knowledge_base_id=knowledge_base_id,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    filename=filename,
                    mime_type="application/pdf",
                    size_bytes=size_bytes,
                    content_hash=content_hash,
                    blob_path=_blob_storage_key(final_path),
                    status="queued",
                    page_count=page_count,
                    created_at=now,
                    updated_at=now,
                )
                session.add(document)
            document.current_task_id = task.id
            session.add(task)
            session.commit()
            payload = {**self._document_payload(session, document), "duplicate": False}
            self._enqueue_task(task.id)
            return payload
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(RagDocument).where(
                    RagDocument.knowledge_base_id == knowledge_base_id,
                    RagDocument.content_hash == content_hash,
                    RagDocument.tenant_id == tenant_id,
                    RagDocument.owner_id == owner_id,
                    RagDocument.status != "deleted",
                )
            )
            if existing is not None:
                return {**self._document_payload(session, existing), "duplicate": True}
            raise
        except Exception:
            session.rollback()
            if created_blob and final_path is not None:
                final_path.unlink(missing_ok=True)
            raise
        finally:
            session.close()

    def retry_document(
        self,
        document_id: str,
        *,
        tenant_id: str,
        owner_id: str,
        rebuild: bool = False,
    ) -> dict[str, Any]:
        session = self._session()
        try:
            document = self._owned_document(session, document_id, tenant_id, owner_id)
            if document.status in {"deleting", "deleted"}:
                raise RagServiceError("该文档当前状态不支持重试。", code="invalid_document_state", status_code=409)
            if document.status == "unsupported" and document.error_code != "extraction_quality_low":
                raise RagServiceError(
                    "该文档无法通过重复解析恢复；请检查 PDF 页数或页面定位信息。",
                    code="invalid_document_state",
                    status_code=409,
                )
            if document.status == "processing":
                raise RagServiceError("文档正在处理中，不能重复提交任务。", code="task_in_progress", status_code=409)
            if not rebuild and document.status not in {"failed", "queued", "unsupported"}:
                raise RagServiceError("只有失败、识别未通过或排队中的文档可以重试。", code="invalid_document_state", status_code=409)
            if not resolve_rag_blob_path(document.blob_path).is_file():
                raise RagServiceError("原始 PDF 文件已不可用，无法重试。", code="blob_missing", status_code=410)
            task = self._new_task(session, document, operation="reindex" if rebuild else "ingest")
            if not rebuild or not document.active_index_version_id:
                document.status = "queued"
                document.error_code = None
                document.error_detail = None
            document.current_task_id = task.id
            session.commit()
            payload = self._document_payload(session, document)
            self._enqueue_task(task.id)
            return payload
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_document(self, document_id: str, *, tenant_id: str, owner_id: str) -> dict[str, Any]:
        session = self._session()
        try:
            document = self._owned_document(session, document_id, tenant_id, owner_id)
            task = self._active_delete_task(session, document)
            if task is not None:
                return self._document_payload(session, document)
            task = self._new_task(session, document, operation="delete")
            document.status = "deleting"
            document.current_task_id = task.id
            session.commit()
            payload = self._document_payload(session, document)
            self._enqueue_task(task.id)
            return payload
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_knowledge_base(self, knowledge_base_id: str, *, tenant_id: str, owner_id: str) -> dict[str, Any]:
        session = self._session()
        task_ids: list[str] = []
        try:
            kb = session.scalar(
                self._kb_query(tenant_id, owner_id).where(RagKnowledgeBase.id == knowledge_base_id)
            )
            if kb is None:
                raise RagServiceError("知识库不存在。", code="knowledge_base_not_found", status_code=404)
            kb.status = "deleting"
            documents = session.scalars(
                select(RagDocument).where(
                    RagDocument.knowledge_base_id == knowledge_base_id,
                    RagDocument.tenant_id == tenant_id,
                    RagDocument.owner_id == owner_id,
                    RagDocument.status != "deleted",
                )
            ).all()
            for document in documents:
                document.status = "deleting"
                if self._active_delete_task(session, document) is not None:
                    continue
                task = self._new_task(session, document, operation="delete")
                document.current_task_id = task.id
                task_ids.append(task.id)
            still_present = session.scalar(
                select(func.count(RagDocument.id)).where(
                    RagDocument.knowledge_base_id == knowledge_base_id,
                    RagDocument.tenant_id == tenant_id,
                    RagDocument.owner_id == owner_id,
                    RagDocument.status != "deleted",
                )
            )
            if not still_present:
                kb.status = "deleted"
            kb.updated_at = datetime.now()
            session.commit()
            for task_id in task_ids:
                self._enqueue_task(task_id)
            return {"id": knowledge_base_id, "status": kb.status, "queued_cleanup_count": len(task_ids)}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def document_preview(
        self,
        document_id: str,
        *,
        tenant_id: str,
        owner_id: str,
        page: int | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        session = self._session()
        try:
            document = self._owned_document(session, document_id, tenant_id, owner_id)
            if not document.active_index_version_id:
                return {"pages": [], "chunks": [], "status": document.status}
            page_query = select(RagPage).where(
                RagPage.document_id == document_id,
                RagPage.index_version_id == document.active_index_version_id,
            )
            if page is not None:
                page_query = page_query.where(RagPage.page_number == page)
            pages = session.scalars(page_query.order_by(RagPage.page_number).offset(offset).limit(limit)).all()
            chunk_query = select(RagChunk).where(
                RagChunk.document_id == document_id,
                RagChunk.index_version_id == document.active_index_version_id,
            )
            if page is not None:
                chunk_query = chunk_query.where(
                    RagChunk.page_start <= page,
                    RagChunk.page_end >= page,
                )
            chunks = session.scalars(
                chunk_query.order_by(RagChunk.chunk_index).offset(offset).limit(limit)
            ).all()
            return {
                "status": document.status,
                "page_count": document.page_count,
                "pages": [self._page_payload(item) for item in pages],
                "chunks": [self._chunk_payload(item) for item in chunks],
            }
        finally:
            session.close()

    def active_index_versions(
        self,
        knowledge_base_ids: list[str],
        *,
        tenant_id: str,
        owner_id: str,
    ) -> list[dict[str, Any]]:
        if not knowledge_base_ids:
            return []
        session = self._session()
        try:
            rows = session.execute(
                select(RagDocument, RagIndexVersion)
                .join(RagIndexVersion, RagIndexVersion.id == RagDocument.active_index_version_id)
                .join(RagKnowledgeBase, RagKnowledgeBase.id == RagDocument.knowledge_base_id)
                .where(
                    RagDocument.knowledge_base_id.in_(knowledge_base_ids),
                    RagDocument.tenant_id == tenant_id,
                    RagDocument.owner_id == owner_id,
                    RagDocument.status == "ready",
                    RagIndexVersion.status == "active",
                    RagKnowledgeBase.tenant_id == tenant_id,
                    RagKnowledgeBase.owner_id == owner_id,
                    RagKnowledgeBase.status == "active",
                )
            ).all()
            return [
                {
                    "document_id": document.id,
                    "knowledge_base_id": document.knowledge_base_id,
                    "index_version_id": index.id,
                    "collection": index.qdrant_collection,
                    "embedding_model": index.embedding_model,
                    "vector_dimension": index.vector_dimension,
                }
                for document, index in rows
            ]
        finally:
            session.close()

    def adjacent_chunks(
        self,
        document_id: str,
        index_version_id: str,
        chunk_index: int,
        *,
        before: int = 1,
        after: int = 1,
    ) -> list[dict[str, Any]]:
        session = self._session()
        try:
            items = session.scalars(
                select(RagChunk)
                .where(
                    RagChunk.document_id == document_id,
                    RagChunk.index_version_id == index_version_id,
                    RagChunk.chunk_index >= max(0, chunk_index - before),
                    RagChunk.chunk_index <= chunk_index + after,
                )
                .order_by(RagChunk.chunk_index)
            ).all()
            return [self._chunk_payload(item) for item in items]
        finally:
            session.close()

    def list_queued_tasks(self, *, stale_before: datetime, limit: int = 100) -> list[str]:
        session = self._session()
        try:
            return list(
                session.scalars(
                    select(RagIngestionTask.id)
                    .where(
                        RagIngestionTask.status == "queued",
                        RagIngestionTask.updated_at <= stale_before,
                    )
                    .order_by(RagIngestionTask.created_at)
                    .limit(limit)
                ).all()
            )
        finally:
            session.close()

    def _new_task(self, session: Session, document: RagDocument, *, operation: str) -> RagIngestionTask:
        task = RagIngestionTask(
            id=uuid.uuid4().hex,
            knowledge_base_id=document.knowledge_base_id,
            document_id=document.id,
            operation=operation,
            status="queued",
            stage="queued",
            progress=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        session.add(task)
        return task

    @staticmethod
    def _active_delete_task(session: Session, document: RagDocument) -> RagIngestionTask | None:
        if document.status != "deleting" or not document.current_task_id:
            return None
        task = session.get(RagIngestionTask, document.current_task_id)
        if task is None or task.operation != "delete" or task.status not in {"queued", "processing"}:
            return None
        return task

    def _enqueue_task(self, task_id: str) -> None:
        try:
            from src.rag.worker import dispatch_ingestion_task

            celery_task_id = dispatch_ingestion_task(task_id)
            session = self._session()
            try:
                task = session.get(RagIngestionTask, task_id)
                if task is not None:
                    task.celery_task_id = celery_task_id
                    task.error_code = None
                    task.error_detail = None
                    task.updated_at = datetime.now()
                    session.commit()
            finally:
                session.close()
        except Exception as exc:
            logger.warning("RAG ingestion enqueue deferred task_id=%s error_type=%s", task_id, type(exc).__name__)
            session = self._session()
            try:
                task = session.get(RagIngestionTask, task_id)
                if task is not None and task.status == "queued":
                    task.error_code = "broker_unavailable"
                    task.error_detail = "后台处理队列暂不可用；系统将自动重新分发。"
                    task.updated_at = datetime.now()
                    session.commit()
            finally:
                session.close()

    @staticmethod
    def _owned_document(session: Session, document_id: str, tenant_id: str, owner_id: str) -> RagDocument:
        document = session.scalar(
            select(RagDocument).where(
                RagDocument.id == document_id,
                RagDocument.tenant_id == tenant_id,
                RagDocument.owner_id == owner_id,
                RagDocument.status != "deleted",
            )
        )
        if document is None:
            raise RagServiceError("文档不存在。", code="document_not_found", status_code=404)
        return document

    @staticmethod
    def _kb_payload(kb: RagKnowledgeBase) -> dict[str, Any]:
        return {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
            "status": kb.status,
            "created_at": kb.created_at.isoformat() if kb.created_at else None,
            "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
        }

    @staticmethod
    def _document_payload(
        session: Session,
        document: RagDocument,
        *,
        include_errors: bool = True,
    ) -> dict[str, Any]:
        task = session.get(RagIngestionTask, document.current_task_id) if document.current_task_id else None
        version = (
            session.get(RagIndexVersion, document.active_index_version_id)
            if document.active_index_version_id
            else None
        )
        result = {
            "id": document.id,
            "knowledge_base_id": document.knowledge_base_id,
            "filename": document.filename,
            "mime_type": document.mime_type,
            "size_bytes": document.size_bytes,
            "content_hash": document.content_hash,
            "status": document.status,
            "page_count": document.page_count,
            "text_length": document.text_length,
            "chunk_count": document.chunk_count,
            "active_index_version_id": document.active_index_version_id,
            "index_version": version.version if version else None,
            "task": RagKnowledgeBaseService._task_payload(task),
            "created_at": document.created_at.isoformat() if document.created_at else None,
            "updated_at": document.updated_at.isoformat() if document.updated_at else None,
            "source": RagKnowledgeBaseService._source_payload(
                session.get(RagDocumentSource, document.id)
            ),
        }
        if include_errors:
            result["error_code"] = document.error_code
            result["error_detail"] = document.error_detail
        return result

    @staticmethod
    def _source_payload(source: RagDocumentSource | None) -> dict[str, Any] | None:
        if source is None:
            return None
        return {
            "provider": source.provider,
            "exchange": source.exchange,
            "security_code": source.security_code,
            "security_name": source.security_name,
            "report_type": source.report_type,
            "report_period": source.report_period,
            "announcement_id": source.announcement_id,
            "announcement_title": source.announcement_title,
            "published_at": source.published_at,
            "pdf_url": source.pdf_url,
            "announcement_url": source.announcement_url,
        }

    @staticmethod
    def _task_payload(task: RagIngestionTask | None) -> dict[str, Any] | None:
        if task is None:
            return None
        return {
            "id": task.id,
            "operation": task.operation,
            "status": task.status,
            "stage": task.stage,
            "progress": task.progress,
            "attempt": task.attempt,
            "error_code": task.error_code,
            "error_detail": task.error_detail,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        }

    @staticmethod
    def _page_payload(page: RagPage) -> dict[str, Any]:
        return {
            "page_number": page.page_number,
            "text": page.text_content,
            "text_length": page.text_length,
            "structure": _json_object(page.structure_json, []),
        }

    @staticmethod
    def _chunk_payload(chunk: RagChunk) -> dict[str, Any]:
        return {
            "id": chunk.id,
            "chunk_index": chunk.chunk_index,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "section": chunk.section,
            "text": chunk.text_content,
            "char_start": chunk.char_start,
            "char_end": chunk.char_end,
            "metadata": _json_object(chunk.metadata_json, {}),
        }


def _json_object(raw: str | None, fallback: Any) -> Any:
    import json

    try:
        return json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback


__all__ = ["RagKnowledgeBaseService", "RagServiceError", "rag_storage_root"]
