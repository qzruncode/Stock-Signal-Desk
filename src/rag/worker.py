"""Dedicated, durable Celery worker for PDF knowledge-base indexing."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from celery import Celery
from celery.signals import worker_process_init
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from src.rag.model_adapters import (
    DEFAULT_EMBEDDING_BASE_URL,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REVISION,
    DEFAULT_VECTOR_DIMENSION,
    LocalFastEmbedAdapter,
    RAGProviderError,
    model_version_tag,
)
from src.rag.pdf_processing import (
    CHUNKING_VERSION,
    PDF_PARSER_NAME,
    PdfProcessingError,
    parse_pdf,
    pdf_parser_version,
)
from src.rag.qdrant_store import QdrantStore, collection_name
from src.services.rag_knowledge_base_service import resolve_rag_blob_path
from src.storage import DatabaseManager
from src.storage.models import (
    RagChunk,
    RagDocument,
    RagIndexVersion,
    RagIngestionTask,
    RagKnowledgeBase,
    RagPage,
)

from src.config import setup_env

setup_env()

logger = logging.getLogger(__name__)

_DEFAULT_BROKER = "redis://127.0.0.1:6382/0"
_TASK_SOFT_LIMIT_SECONDS = 10_500
_TASK_HARD_LIMIT_SECONDS = 10_800
_RECONCILE_AFTER_SECONDS = 180
# PDF parsing refreshes task.updated_at after each bounded page batch. A
# 30-minute stale heartbeat therefore indicates a failed worker, not one slow
# page conversion.
_PROCESSING_RECOVERY_AFTER_SECONDS = 1_800


def _broker_url() -> str:
    return str(os.getenv("RAG_CELERY_BROKER_URL") or _DEFAULT_BROKER).strip()


celery_app = Celery("daily_stock_rag", broker=_broker_url())
celery_app.conf.update(
    task_default_queue="rag",
    task_routes={"src.rag.worker.*": {"queue": "rag"}},
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=_TASK_SOFT_LIMIT_SECONDS,
    task_time_limit=_TASK_HARD_LIMIT_SECONDS,
    task_track_started=True,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": _TASK_HARD_LIMIT_SECONDS * 2},
    beat_schedule={
        "rag-ingestion-reconcile": {
            "task": "src.rag.worker.reconcile_queued_tasks",
            "schedule": 60.0,
        }
    },
)


def _initialize_rag_worker_process(**_kwargs: Any) -> None:
    """Initialize Celery's fast task registry in every prefork child.

    macOS starts Billiard children with ``spawn``. Celery's normal fork path
    inherits this registry from the parent, but spawned children need to build
    it in their own process before ``fast_trace_task`` handles a message.
    """
    from celery import current_app
    from celery.app.trace import setup_worker_optimizations

    setup_worker_optimizations(current_app._get_current_object())


worker_process_init.connect(
    _initialize_rag_worker_process,
    weak=False,
    dispatch_uid="src.rag.worker.initialize_spawned_prefork_trace",
)


def dispatch_ingestion_task(task_id: str) -> str:
    """Publish one durable business task using its stable idempotency id."""
    task_identifier = str(task_id).strip()
    if not task_identifier:
        raise ValueError("RAG task id must not be empty")
    result = process_ingestion_task.apply_async(
        args=[task_identifier],
        task_id=task_identifier,
        queue="rag",
    )
    return str(result.id or task_identifier)


def _runtime_model_config() -> dict[str, str]:
    from src.core.config_manager import ConfigManager

    values = ConfigManager().read_config_map()
    # Worker processes are separate from the API process. Explicit runtime
    # environment overrides take precedence over
    # the shared .env file just as they do during application startup.
    for key in (
        "RAG_EMBEDDING_BASE_URL",
        "RAG_EMBEDDING_MODEL_ID",
        "RAG_EMBEDDING_MODEL_REVISION",
        "RAG_VECTOR_DIMENSION",
        "RAG_MODEL_TIMEOUT_SECONDS",
    ):
        if key in os.environ:
            values[key] = str(os.environ[key])
    return values


def _update_task(task_id: str, *, stage: str, progress: int, status: str | None = None) -> None:
    session = DatabaseManager.get_instance().get_session()
    try:
        task = session.get(RagIngestionTask, task_id)
        if task is None:
            return
        task.stage = stage
        task.progress = max(0, min(100, int(progress)))
        task.updated_at = datetime.now()
        if status is not None:
            task.status = status
        session.commit()
    finally:
        session.close()


def _claim_task(task_id: str) -> dict[str, Any] | None:
    session = DatabaseManager.get_instance().get_session()
    try:
        now = datetime.now()
        claimed = session.execute(
            update(RagIngestionTask)
            .where(RagIngestionTask.id == task_id, RagIngestionTask.status == "queued")
            .values(
                status="processing",
                stage="starting",
                started_at=now,
                finished_at=None,
                error_code=None,
                error_detail=None,
                attempt=RagIngestionTask.attempt + 1,
                updated_at=now,
            )
        )
        if claimed.rowcount != 1:
            session.rollback()
            return None
        task = session.get(RagIngestionTask, task_id)
        document = session.get(RagDocument, task.document_id) if task else None
        if task is None or document is None:
            session.rollback()
            return None
        document.error_code = None
        document.error_detail = None
        document.updated_at = now
        payload = {
            "task_id": task.id,
            "operation": task.operation,
            "document_id": document.id,
            "knowledge_base_id": document.knowledge_base_id,
            "tenant_id": document.tenant_id,
            "owner_id": document.owner_id,
            "blob_path": document.blob_path,
            "filename": document.filename,
            "active_index_version_id": document.active_index_version_id,
        }
        session.commit()
        return payload
    finally:
        session.close()


def _write_failure(task_id: str, error: BaseException, *, unsupported: bool = False) -> None:
    session = DatabaseManager.get_instance().get_session()
    try:
        task = session.get(RagIngestionTask, task_id)
        if task is None:
            return
        document = session.get(RagDocument, task.document_id)
        task.status = "failed"
        task.stage = "failed"
        task.error_code = (
            str(getattr(error, "code", "processing_failed"))[:80]
            if not unsupported
            else str(getattr(error, "code", "unsupported_pdf"))[:80]
        )
        task.error_detail = str(error)[:1_000]
        task.updated_at = datetime.now()
        task.finished_at = datetime.now()
        if document is not None:
            if task.operation == "delete":
                # Keep the document excluded from retrieval until blob and
                # vectors have actually been removed. A later delete request
                # can create a new idempotent cleanup task.
                document.status = "deleting"
            else:
                document.status = "unsupported" if unsupported else (
                    "ready" if document.active_index_version_id else "failed"
                )
            document.error_code = task.error_code
            document.error_detail = task.error_detail
            document.updated_at = datetime.now()
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Failed to persist RAG task failure task_id=%s", task_id)
    finally:
        session.close()


def _finish_task(task_id: str, *, stage: str = "complete") -> None:
    session = DatabaseManager.get_instance().get_session()
    try:
        task = session.get(RagIngestionTask, task_id)
        if task is not None:
            task.status = "succeeded"
            task.stage = stage
            task.progress = 100
            task.error_code = None
            task.error_detail = None
            task.updated_at = datetime.now()
            task.finished_at = datetime.now()
            session.commit()
    finally:
        session.close()


def _process_delete(payload: dict[str, Any], task_id: str) -> None:
    session = DatabaseManager.get_instance().get_session()
    try:
        versions = session.scalars(
            select(RagIndexVersion).where(RagIndexVersion.document_id == payload["document_id"])
        ).all()
        version_rows = [(item.id, item.qdrant_collection) for item in versions]
    finally:
        session.close()

    store = QdrantStore()
    try:
        for version_id, collection in version_rows:
            store.delete_index_version(collection, version_id)
    finally:
        store.close()
    _update_task(task_id, stage="removing_file", progress=85)
    resolve_rag_blob_path(payload["blob_path"]).unlink(missing_ok=True)

    session = DatabaseManager.get_instance().get_session()
    try:
        document = session.get(RagDocument, payload["document_id"])
        task = session.get(RagIngestionTask, task_id)
        if document is not None:
            session.query(RagPage).filter(RagPage.document_id == document.id).delete(synchronize_session=False)
            session.query(RagChunk).filter(RagChunk.document_id == document.id).delete(synchronize_session=False)
            for version_id, _collection in version_rows:
                current = session.get(RagIndexVersion, version_id)
                if current is not None:
                    current.status = "deleted"
            document.status = "deleted"
            document.active_index_version_id = None
            document.error_code = None
            document.error_detail = None
            document.updated_at = datetime.now()
        if task is not None:
            task.status = "succeeded"
            task.stage = "complete"
            task.progress = 100
            task.finished_at = datetime.now()
            task.updated_at = datetime.now()
        kb = session.get(RagKnowledgeBase, payload["knowledge_base_id"])
        if kb is not None and kb.status == "deleting":
            remaining = session.scalar(
                select(func.count(RagDocument.id)).where(
                    RagDocument.knowledge_base_id == kb.id,
                    RagDocument.status != "deleted",
                )
            )
            if not remaining:
                kb.status = "deleted"
                kb.updated_at = datetime.now()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _new_index_version(payload: dict[str, Any], parsed: Any, model: str, dimension: int) -> RagIndexVersion:
    session = DatabaseManager.get_instance().get_session()
    try:
        version_number = int(
            session.scalar(
                select(func.max(RagIndexVersion.version)).where(
                    RagIndexVersion.document_id == payload["document_id"]
                )
            )
            or 0
        ) + 1
        index_id = uuid.uuid4().hex
        version = RagIndexVersion(
            id=index_id,
            document_id=payload["document_id"],
            version=version_number,
            status="building",
            parser_name=PDF_PARSER_NAME,
            parser_version=parsed.parser_version,
            chunking_version=CHUNKING_VERSION,
            embedding_model=model,
            vector_dimension=dimension,
            qdrant_collection=collection_name(model, dimension),
            page_count=parsed.page_count,
            chunk_count=len(parsed.chunks),
            verification_json="{}",
            created_at=datetime.now(),
        )
        session.add(version)
        # This model graph has no ORM relationships. Flush the parent version
        # explicitly before pages/chunks so SQLite's enabled foreign keys are
        # respected on both fresh and production databases.
        session.flush()
        for page in parsed.pages:
            session.add(
                RagPage(
                    id=f"{index_id}:p:{page.page_number}",
                    document_id=payload["document_id"],
                    index_version_id=index_id,
                    page_number=page.page_number,
                    text_content=page.text,
                    text_length=len(page.text),
                    structure_json=json.dumps(page.structure, ensure_ascii=False),
                )
            )
        chunk_rows: list[dict[str, Any]] = []
        for chunk in parsed.chunks:
            chunk_id = str(uuid.uuid5(uuid.UUID(index_id), str(chunk.chunk_index)))
            session.add(
                RagChunk(
                    id=chunk_id,
                    document_id=payload["document_id"],
                    index_version_id=index_id,
                    chunk_index=chunk.chunk_index,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    section=chunk.section,
                    text_content=chunk.text,
                    content_hash=chunk.content_hash,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    metadata_json=json.dumps(chunk.metadata, ensure_ascii=False),
                )
            )
            chunk_rows.append(
                {
                    "id": chunk_id,
                    "chunk_index": chunk.chunk_index,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section": chunk.section,
                    "text": chunk.text,
                }
            )
        document = session.get(RagDocument, payload["document_id"])
        if document is not None:
            document.text_length = parsed.text_length
            document.page_count = parsed.page_count
            document.chunk_count = len(parsed.chunks)
            document.updated_at = datetime.now()
        session.commit()
        return version, chunk_rows
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _clone_latest_parsed_index(
    payload: dict[str, Any], model: str, dimension: int
) -> tuple[RagIndexVersion, list[dict[str, Any]]] | None:
    """Reuse only a complete parse from the exact parser and chunker versions."""
    parser_version = pdf_parser_version()
    session = DatabaseManager.get_instance().get_session()
    try:
        candidates = session.scalars(
            select(RagIndexVersion)
            .where(
                RagIndexVersion.document_id == payload["document_id"],
                RagIndexVersion.parser_name == PDF_PARSER_NAME,
                RagIndexVersion.parser_version == parser_version,
                RagIndexVersion.chunking_version == CHUNKING_VERSION,
                RagIndexVersion.status.in_(("failed", "active", "superseded")),
                RagIndexVersion.page_count > 0,
                RagIndexVersion.chunk_count > 0,
            )
            .order_by(RagIndexVersion.version.desc())
        ).all()
        source = None
        source_pages: list[RagPage] = []
        source_chunks: list[RagChunk] = []
        for candidate in candidates:
            pages = session.scalars(
                select(RagPage)
                .where(RagPage.index_version_id == candidate.id)
                .order_by(RagPage.page_number)
            ).all()
            chunks = session.scalars(
                select(RagChunk)
                .where(RagChunk.index_version_id == candidate.id)
                .order_by(RagChunk.chunk_index)
            ).all()
            if len(pages) == candidate.page_count and len(chunks) == candidate.chunk_count:
                source, source_pages, source_chunks = candidate, pages, chunks
                break
        if source is None:
            return None

        version_number = int(
            session.scalar(
                select(func.max(RagIndexVersion.version)).where(
                    RagIndexVersion.document_id == payload["document_id"]
                )
            )
            or 0
        ) + 1
        index_id = uuid.uuid4().hex
        version = RagIndexVersion(
            id=index_id,
            document_id=payload["document_id"],
            version=version_number,
            status="building",
            parser_name=source.parser_name,
            parser_version=source.parser_version,
            chunking_version=source.chunking_version,
            embedding_model=model,
            vector_dimension=dimension,
            qdrant_collection=collection_name(model, dimension),
            page_count=source.page_count,
            chunk_count=source.chunk_count,
            verification_json="{}",
            created_at=datetime.now(),
        )
        session.add(version)
        session.flush()
        for page in source_pages:
            session.add(
                RagPage(
                    id=f"{index_id}:p:{page.page_number}",
                    document_id=payload["document_id"],
                    index_version_id=index_id,
                    page_number=page.page_number,
                    text_content=page.text_content,
                    text_length=page.text_length,
                    structure_json=page.structure_json,
                )
            )
        chunk_rows: list[dict[str, Any]] = []
        for chunk in source_chunks:
            chunk_id = str(uuid.uuid5(uuid.UUID(index_id), str(chunk.chunk_index)))
            session.add(
                RagChunk(
                    id=chunk_id,
                    document_id=payload["document_id"],
                    index_version_id=index_id,
                    chunk_index=chunk.chunk_index,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    section=chunk.section,
                    text_content=chunk.text_content,
                    content_hash=chunk.content_hash,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    metadata_json=chunk.metadata_json,
                )
            )
            chunk_rows.append(
                {
                    "id": chunk_id,
                    "chunk_index": chunk.chunk_index,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section": chunk.section,
                    "text": chunk.text_content,
                }
            )
        document = session.get(RagDocument, payload["document_id"])
        if document is not None:
            document.page_count = source.page_count
            document.chunk_count = source.chunk_count
            document.updated_at = datetime.now()
        session.commit()
        return version, chunk_rows
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _publish_index(payload: dict[str, Any], task_id: str, version_id: str, expected_count: int) -> None:
    session = DatabaseManager.get_instance().get_session()
    try:
        version = session.get(RagIndexVersion, version_id)
        document = session.get(RagDocument, payload["document_id"])
        task = session.get(RagIngestionTask, task_id)
        if version is None or document is None or task is None:
            raise RuntimeError("索引发布前业务记录已不存在。")
        if document.status in {"deleting", "deleted"}:
            raise RuntimeError("文档在索引构建期间已进入删除流程。")
        old_version = (
            session.get(RagIndexVersion, document.active_index_version_id)
            if document.active_index_version_id
            else None
        )
        if old_version is not None:
            old_version.status = "superseded"
        version.status = "active"
        version.activated_at = datetime.now()
        version.verification_json = json.dumps(
            {"qdrant_point_count": expected_count, "expected_chunk_count": expected_count},
            ensure_ascii=False,
        )
        document.active_index_version_id = version.id
        document.status = "ready"
        document.error_code = None
        document.error_detail = None
        document.updated_at = datetime.now()
        task.status = "succeeded"
        task.stage = "complete"
        task.progress = 100
        task.error_code = None
        task.error_detail = None
        task.updated_at = datetime.now()
        task.finished_at = datetime.now()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _process_ingest(payload: dict[str, Any], task_id: str) -> None:
    _cleanup_abandoned_index_builds(payload["document_id"])
    blob_path = resolve_rag_blob_path(payload["blob_path"])
    if not blob_path.is_file():
        raise PdfProcessingError("原始 PDF 文件不存在，无法处理。", code="blob_missing")
    config = _runtime_model_config()
    model = str(config.get("RAG_EMBEDDING_MODEL_ID") or DEFAULT_EMBEDDING_MODEL).strip()
    revision = str(config.get("RAG_EMBEDDING_MODEL_REVISION") or DEFAULT_EMBEDDING_REVISION).strip()
    model_version = model_version_tag(model, revision)
    dimension = int(config.get("RAG_VECTOR_DIMENSION") or DEFAULT_VECTOR_DIMENSION)
    reusable = _clone_latest_parsed_index(payload, model_version, dimension)
    if reusable is not None:
        version, chunks = reusable
        _update_task(task_id, stage="embedding", progress=30)
        logger.info(
            "RAG reused verified PDF parse task_id=%s index_version_id=%s pages=%s chunks=%s",
            task_id,
            version.id,
            version.page_count,
            version.chunk_count,
        )
    else:
        _update_task(task_id, stage="parsing", progress=10)
        last_reported_progress = 10

        def report_parse_progress(current_page: int, total_pages: int) -> None:
            nonlocal last_reported_progress
            progress = 10 + round(20 * current_page / max(1, total_pages))
            progress = min(30, max(last_reported_progress, progress))
            if progress > last_reported_progress:
                last_reported_progress = progress
                try:
                    _update_task(task_id, stage="parsing", progress=progress)
                except Exception as exc:
                    logger.warning(
                        "RAG parse progress update failed task_id=%s error_type=%s",
                        task_id,
                        type(exc).__name__,
                    )

        parsed = parse_pdf(blob_path, progress_callback=report_parse_progress)
        version, chunks = _new_index_version(payload, parsed, model_version, dimension)
    store: QdrantStore | None = None
    adapter: LocalFastEmbedAdapter | None = None
    try:
        store = QdrantStore()
        adapter = LocalFastEmbedAdapter(
            base_url=str(config.get("RAG_EMBEDDING_BASE_URL") or DEFAULT_EMBEDDING_BASE_URL),
            model=model,
            dimension=dimension,
            timeout=float(config.get("RAG_MODEL_TIMEOUT_SECONDS") or 300),
        )
        _update_task(task_id, stage="embedding", progress=35)
        vectors: list[list[float]] = []
        embedding_batch_size = 16
        for start in range(0, len(chunks), embedding_batch_size):
            batch = chunks[start : start + embedding_batch_size]
            vectors.extend(adapter.embed_documents([chunk["text"] for chunk in batch]))
            progress = 35 + round(35 * len(vectors) / max(1, len(chunks)))
            _update_task(task_id, stage="embedding", progress=progress)
            logger.info(
                "RAG embedding progress task_id=%s completed=%s total=%s",
                task_id,
                len(vectors),
                len(chunks),
            )
        if len(vectors) != len(chunks):
            raise RuntimeError("Embedding 返回数量与文本分块数量不一致。")
        _update_task(task_id, stage="indexing", progress=72)
        written = store.upsert_chunks(
            collection=version.qdrant_collection,
            dimension=dimension,
            tenant_id=payload["tenant_id"],
            owner_id=payload["owner_id"],
            knowledge_base_id=payload["knowledge_base_id"],
            document_id=payload["document_id"],
            index_version_id=version.id,
            chunks=chunks,
            vectors=vectors,
        )
        if written != len(chunks):
            raise RuntimeError("Qdrant 写入数量与索引分块数量不一致。")
        _update_task(task_id, stage="verifying", progress=90)
        indexed = store.count_index_version(version.qdrant_collection, version.id)
        if indexed != len(chunks):
            raise RuntimeError(f"Qdrant 索引核验不通过（{indexed}/{len(chunks)}）。")
        _publish_index(payload, task_id, version.id, indexed)
    except Exception:
        if store is not None:
            try:
                store.delete_index_version(version.qdrant_collection, version.id)
            except Exception:
                logger.warning("Failed to remove partial RAG index version_id=%s", version.id)
        session = DatabaseManager.get_instance().get_session()
        try:
            failed = session.get(RagIndexVersion, version.id)
            if failed is not None and failed.status == "building":
                failed.status = "failed"
                failed.error_code = "index_build_failed"
                failed.error_detail = "索引构建或核验失败。"
                session.commit()
        finally:
            session.close()
        raise
    finally:
        if adapter is not None:
            adapter.close()
        if store is not None:
            store.close()


def _cleanup_abandoned_index_builds(document_id: str) -> None:
    """Remove partial vectors left by a worker killed outside Python cleanup."""
    session = DatabaseManager.get_instance().get_session()
    try:
        rows = session.execute(
            select(RagIndexVersion.id, RagIndexVersion.qdrant_collection).where(
                RagIndexVersion.document_id == document_id,
                RagIndexVersion.status == "building",
            )
        ).all()
        versions = [(str(version_id), str(collection)) for version_id, collection in rows]
    finally:
        session.close()
    if not versions:
        return

    store = QdrantStore()
    try:
        for version_id, collection in versions:
            store.delete_index_version(collection, version_id)
    finally:
        store.close()

    session = DatabaseManager.get_instance().get_session()
    try:
        for version_id, _collection in versions:
            version = session.get(RagIndexVersion, version_id)
            if version is not None and version.status == "building":
                version.status = "failed"
                version.error_code = "worker_interrupted"
                version.error_detail = "上一次索引构建被中断，已在重试前清理。"
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _run_task(task_id: str) -> None:
    payload = _claim_task(task_id)
    if payload is None:
        return
    if payload["operation"] == "delete":
        _process_delete(payload, task_id)
    elif payload["operation"] in {"ingest", "reindex"}:
        _process_ingest(payload, task_id)
    else:
        raise ValueError(f"未知的 RAG 入库任务类型: {payload['operation']}")


@celery_app.task(
    bind=True,
    name="src.rag.worker.process_ingestion_task",
    max_retries=5,
    default_retry_delay=30,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_ingestion_task(task, task_id: str) -> dict[str, Any]:
    """Process one durable task; transient failures use bounded Celery retry."""
    try:
        _run_task(task_id)
        return {"task_id": task_id, "status": "complete"}
    except PdfProcessingError as exc:
        _write_failure(task_id, exc, unsupported=exc.unsupported)
        return {"task_id": task_id, "status": "unsupported" if exc.unsupported else "failed"}
    except Exception as exc:
        retries = int(getattr(task.request, "retries", 0) or 0)
        retryable = not isinstance(
            exc,
            (ValueError, KeyError, FileNotFoundError, IntegrityError),
        )
        if isinstance(exc, RAGProviderError):
            retryable = exc.retryable
        if retryable and retries < int(task.max_retries or 5):
            _update_task(task_id, stage="retry_wait", progress=0, status="queued")
            raise task.retry(exc=exc, countdown=min(300, 15 * (2 ** retries)))
        _write_failure(task_id, exc)
        logger.warning(
            "RAG task failed task_id=%s error_type=%s retry_count=%s",
            task_id,
            type(exc).__name__,
            retries,
        )
        return {"task_id": task_id, "status": "failed"}


@celery_app.task(name="src.rag.worker.reconcile_queued_tasks", ignore_result=True)
def reconcile_queued_tasks() -> int:
    """Re-publish queued work and recover tasks abandoned by a dead worker."""
    database = DatabaseManager.get_instance()
    session = database.get_session()
    try:
        cutoff = datetime.now() - timedelta(seconds=_RECONCILE_AFTER_SECONDS)
        abandoned_cutoff = datetime.now() - timedelta(seconds=_PROCESSING_RECOVERY_AFTER_SECONDS)
        recovered_ids = list(
            session.scalars(
                select(RagIngestionTask.id).where(
                    RagIngestionTask.status == "processing",
                    RagIngestionTask.updated_at <= abandoned_cutoff,
                )
            ).all()
        )
        if recovered_ids:
            session.execute(
                update(RagIngestionTask)
                .where(
                    RagIngestionTask.id.in_(recovered_ids),
                    RagIngestionTask.status == "processing",
                )
                .values(
                    status="queued",
                    stage="worker_recovery",
                    updated_at=datetime.now(),
                    finished_at=None,
                )
            )
            session.commit()
        identifiers = list(
            session.scalars(
                select(RagIngestionTask.id)
                .where(
                    RagIngestionTask.status == "queued",
                    RagIngestionTask.updated_at <= cutoff,
                )
                .order_by(RagIngestionTask.created_at)
                .limit(100)
            ).all()
        )
        identifiers = list(dict.fromkeys([*recovered_ids, *identifiers]))[:100]
    finally:
        session.close()
    dispatched = 0
    for identifier in identifiers:
        try:
            dispatch_ingestion_task(identifier)
            dispatched += 1
        except Exception as exc:
            logger.warning("RAG task reconciliation deferred task_id=%s error_type=%s", identifier, type(exc).__name__)
    return dispatched


__all__ = [
    "celery_app",
    "dispatch_ingestion_task",
    "process_ingestion_task",
    "reconcile_queued_tasks",
]
