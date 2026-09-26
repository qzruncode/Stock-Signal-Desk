from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.services import rag_knowledge_base_service as rag_service_module
from src.services.rag_knowledge_base_service import (
    RagKnowledgeBaseService,
    RagServiceError,
    rag_storage_root,
    resolve_rag_blob_path,
)
from src.storage.models import Base, RagDocument, RagIndexVersion, RagIngestionTask, RagKnowledgeBase, RagPage


class AsyncBytes:
    def __init__(self, content: bytes):
        self.content = content
        self.offset = 0

    async def read(self, size: int) -> bytes:
        result = self.content[self.offset : self.offset + size]
        self.offset += len(result)
        return result


def test_blob_keys_are_portable_and_cannot_escape_storage_root(tmp_path, monkeypatch) -> None:
    storage_root = tmp_path / "persistent-volume" / "rag"
    monkeypatch.setenv("RAG_STORAGE_PATH", str(storage_root))

    assert resolve_rag_blob_path("documents/doc-1/source.pdf") == storage_root / "documents/doc-1/source.pdf"
    legacy_path = tmp_path / "legacy.pdf"
    assert resolve_rag_blob_path(legacy_path) == legacy_path
    with pytest.raises(ValueError, match="escapes"):
        resolve_rag_blob_path("../outside.pdf")

    assert rag_storage_root() == storage_root.resolve()


def test_document_preview_pages_are_offset_paginated(rag_service) -> None:
    service, factory = rag_service
    with factory() as session:
        session.add(
            RagDocument(
                id="doc-pages",
                knowledge_base_id="kb-1",
                tenant_id="local",
                owner_id="admin",
                filename="long-report.pdf",
                content_hash="sha256-pages",
                blob_path="documents/doc-pages/source.pdf",
                status="ready",
                page_count=25,
                active_index_version_id="index-pages",
            )
        )
        session.add(
            RagIndexVersion(
                id="index-pages",
                document_id="doc-pages",
                version=1,
                status="active",
                parser_version="1",
                chunking_version="1",
                embedding_model="embed@revision",
                vector_dimension=384,
                qdrant_collection="collection-pages",
                page_count=25,
            )
        )
        session.add_all(
            RagPage(
                id=f"index-pages:p:{page_number}",
                document_id="doc-pages",
                index_version_id="index-pages",
                page_number=page_number,
                text_content=f"原文第 {page_number} 页",
                text_length=10,
            )
            for page_number in range(1, 26)
        )
        session.commit()

    first_page = service.document_preview(
        "doc-pages", tenant_id="local", owner_id="admin", offset=0, limit=20
    )
    second_page = service.document_preview(
        "doc-pages", tenant_id="local", owner_id="admin", offset=20, limit=20
    )

    assert first_page["page_count"] == second_page["page_count"] == 25
    assert [page["page_number"] for page in first_page["pages"]] == list(range(1, 21))
    assert [page["page_number"] for page in second_page["pages"]] == list(range(21, 26))


@pytest.fixture
def rag_service(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rag-service.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(
            RagKnowledgeBase(
                id="kb-1",
                tenant_id="local",
                owner_id="admin",
                name="研究资料",
            )
        )
        session.commit()
    service = RagKnowledgeBaseService(SimpleNamespace(get_session=factory))
    yield service, factory
    engine.dispose()


def test_upload_is_hash_idempotent_and_lists_document_counts(
    rag_service, tmp_path, monkeypatch
) -> None:
    service, factory = rag_service
    monkeypatch.setattr(rag_service_module, "rag_storage_root", lambda: tmp_path / "rag-data")
    monkeypatch.setattr(rag_service_module, "count_pdf_pages", lambda *_args, **_kwargs: 1)
    dispatched: list[str] = []
    monkeypatch.setattr(service, "_enqueue_task", dispatched.append)
    content = b"%PDF-1.7\nrepresentative text pdf payload"

    async def upload_twice():
        first_result = await service.upload_pdf(
            "kb-1", "annual-report.pdf", AsyncBytes(content), tenant_id="local", owner_id="admin"
        )
        second_result = await service.upload_pdf(
            "kb-1", "renamed-copy.pdf", AsyncBytes(content), tenant_id="local", owner_id="admin"
        )
        return first_result, second_result

    first, second = asyncio.run(upload_twice())

    assert first["status"] == "queued"
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["id"] == second["id"]
    assert len(dispatched) == 1
    assert service.list_knowledge_bases(tenant_id="local", owner_id="admin")[0]["document_count"] == 1
    with factory() as session:
        documents = session.query(RagDocument).all()
        assert len(documents) == 1
        assert not Path(documents[0].blob_path).is_absolute()
        assert resolve_rag_blob_path(documents[0].blob_path).is_file()


def test_upload_rejects_non_pdf_and_unowned_knowledge_base(rag_service, monkeypatch) -> None:
    service, _factory = rag_service
    async def exercise_rejections():
        with pytest.raises(RagServiceError) as signature_error:
            await service.upload_pdf(
                "kb-1", "fake.pdf", AsyncBytes(b"not a pdf file payload"), tenant_id="local", owner_id="admin"
            )
        assert signature_error.value.code == "invalid_pdf_signature"

        with pytest.raises(RagServiceError) as scope_error:
            await service.upload_pdf(
                "kb-1", "real.pdf", AsyncBytes(b"%PDF-1.7\nrepresentative text pdf payload"), tenant_id="other", owner_id="admin"
            )
        assert scope_error.value.status_code == 404

    asyncio.run(exercise_rejections())


def test_upload_pdf_path_streams_to_blob_and_exposes_official_source_metadata(
    rag_service, tmp_path, monkeypatch
) -> None:
    service, factory = rag_service
    monkeypatch.setattr(rag_service_module, "rag_storage_root", lambda: tmp_path / "rag-data")
    monkeypatch.setattr(rag_service_module, "count_pdf_pages", lambda *_args, **_kwargs: 18)
    dispatched: list[str] = []
    monkeypatch.setattr(service, "_enqueue_task", dispatched.append)
    source_path = tmp_path / "exchange-report.part"
    content = b"%PDF-1.7\nrepresentative official report payload"
    source_path.write_bytes(content)

    document = service.upload_pdf_path(
        "kb-1", "新强联_2026年半年度报告.pdf", source_path, tenant_id="local", owner_id="admin"
    )
    source = {
        "provider": "RSSHub/深交所",
        "exchange": "深交所",
        "security_code": "300850",
        "security_name": "新强联",
        "report_type": "semiannual",
        "report_period": "2026年半年度",
        "announcement_id": "szse-1",
        "announcement_title": "新强联：2026年半年度报告",
        "published_at": "2026-08-29",
        "pdf_url": "https://disc.static.szse.cn/download/report.PDF",
        "announcement_url": "https://www.szse.cn/disclosure/item",
    }
    with_source = service.set_document_source(
        document["id"], source, tenant_id="local", owner_id="admin"
    )
    listed = service.list_documents("kb-1", tenant_id="local", owner_id="admin")[0]

    assert not source_path.exists()
    assert document["status"] == "queued"
    assert with_source["source"]["pdf_url"] == source["pdf_url"]
    assert listed["source"]["announcement_title"] == source["announcement_title"]
    with factory() as session:
        stored = session.get(RagDocument, document["id"])
        assert resolve_rag_blob_path(stored.blob_path).read_bytes() == content
    assert len(dispatched) == 1


def test_delete_requests_are_idempotent_and_failed_cleanup_can_be_retried(rag_service, tmp_path, monkeypatch) -> None:
    service, factory = rag_service
    monkeypatch.setattr(rag_service_module, "rag_storage_root", lambda: tmp_path / "rag-data")
    dispatched: list[str] = []
    monkeypatch.setattr(service, "_enqueue_task", dispatched.append)
    content = b"%PDF-1.7\nrepresentative text pdf payload"
    monkeypatch.setattr(rag_service_module, "count_pdf_pages", lambda *_args, **_kwargs: 1)

    document = asyncio.run(
        service.upload_pdf("kb-1", "report.pdf", AsyncBytes(content), tenant_id="local", owner_id="admin")
    )
    first = service.delete_document(document["id"], tenant_id="local", owner_id="admin")
    second = service.delete_document(document["id"], tenant_id="local", owner_id="admin")
    assert first["status"] == second["status"] == "deleting"
    assert len(dispatched) == 2  # one ingest task and one delete task

    with factory() as session:
        delete_task = session.get(RagIngestionTask, first["task"]["id"])
        delete_task.status = "failed"
        session.commit()

    retried = service.delete_document(document["id"], tenant_id="local", owner_id="admin")
    assert retried["status"] == "deleting"
    assert len(dispatched) == 3


def test_low_extraction_quality_document_can_be_reprocessed_without_reupload(rag_service, tmp_path, monkeypatch) -> None:
    service, factory = rag_service
    monkeypatch.setattr(rag_service_module, "rag_storage_root", lambda: tmp_path / "rag-data")
    monkeypatch.setattr(rag_service_module, "count_pdf_pages", lambda *_args, **_kwargs: 1)
    dispatched: list[str] = []
    monkeypatch.setattr(service, "_enqueue_task", dispatched.append)
    document = asyncio.run(
        service.upload_pdf(
            "kb-1", "report.pdf", AsyncBytes(b"%PDF-1.7\nrepresentative text pdf payload"),
            tenant_id="local", owner_id="admin",
        )
    )
    with factory() as session:
        stored = session.get(RagDocument, document["id"])
        stored.status = "unsupported"
        stored.error_code = "extraction_quality_low"
        stored.error_detail = "解析未达到文本质量阈值。"
        session.commit()

    retried = service.retry_document(
        document["id"], tenant_id="local", owner_id="admin", rebuild=False
    )

    assert retried["status"] == "queued"
    assert retried["error_code"] is None
    assert retried["task"]["operation"] == "ingest"
    assert retried["task"]["attempt"] == 0
    assert len(dispatched) == 2


def test_structurally_unsupported_document_cannot_be_reprocessed(rag_service, monkeypatch) -> None:
    service, factory = rag_service
    monkeypatch.setattr(rag_service_module, "count_pdf_pages", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(service, "_enqueue_task", lambda *_args: None)
    document = asyncio.run(
        service.upload_pdf(
            "kb-1", "report.pdf", AsyncBytes(b"%PDF-1.7\nrepresentative text pdf payload"),
            tenant_id="local", owner_id="admin",
        )
    )
    with factory() as session:
        stored = session.get(RagDocument, document["id"])
        stored.status = "unsupported"
        stored.error_code = "page_limit_exceeded"
        session.commit()

    with pytest.raises(RagServiceError) as error:
        service.retry_document(document["id"], tenant_id="local", owner_id="admin")

    assert error.value.code == "invalid_document_state"


def test_reupload_after_completed_delete_reuses_hash_tombstone(rag_service, tmp_path, monkeypatch) -> None:
    service, factory = rag_service
    monkeypatch.setattr(rag_service_module, "rag_storage_root", lambda: tmp_path / "rag-data")
    monkeypatch.setattr(rag_service_module, "count_pdf_pages", lambda *_args, **_kwargs: 1)
    dispatched: list[str] = []
    monkeypatch.setattr(service, "_enqueue_task", dispatched.append)
    content = b"%PDF-1.7\nrepresentative text pdf payload"

    first = asyncio.run(
        service.upload_pdf("kb-1", "report.pdf", AsyncBytes(content), tenant_id="local", owner_id="admin")
    )
    with factory() as session:
        document = session.get(RagDocument, first["id"])
        document.status = "deleted"
        session.commit()

    restored = asyncio.run(
        service.upload_pdf("kb-1", "report.pdf", AsyncBytes(content), tenant_id="local", owner_id="admin")
    )

    assert restored["id"] == first["id"]
    assert restored["status"] == "queued"
    assert restored["duplicate"] is False
    assert len(dispatched) == 2


def test_repeated_knowledge_base_delete_does_not_duplicate_active_cleanup_tasks(rag_service, tmp_path, monkeypatch) -> None:
    service, _factory = rag_service
    monkeypatch.setattr(rag_service_module, "rag_storage_root", lambda: tmp_path / "rag-data")
    monkeypatch.setattr(rag_service_module, "count_pdf_pages", lambda *_args, **_kwargs: 1)
    dispatched: list[str] = []
    monkeypatch.setattr(service, "_enqueue_task", dispatched.append)
    asyncio.run(
        service.upload_pdf(
            "kb-1", "report.pdf", AsyncBytes(b"%PDF-1.7\nrepresentative text pdf payload"),
            tenant_id="local", owner_id="admin"
        )
    )

    first = service.delete_knowledge_base("kb-1", tenant_id="local", owner_id="admin")
    second = service.delete_knowledge_base("kb-1", tenant_id="local", owner_id="admin")
    assert first["queued_cleanup_count"] == 1
    assert second["queued_cleanup_count"] == 0
    assert len(dispatched) == 2
