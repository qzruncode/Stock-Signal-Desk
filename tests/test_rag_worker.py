from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
import hashlib

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.rag.pdf_processing import CHUNKING_VERSION, ParsedPdf, PdfChunk, PdfPage
from src.storage.models import (
    Base,
    RagChunk,
    RagDocument,
    RagIndexVersion,
    RagIngestionTask,
    RagKnowledgeBase,
    RagPage,
)


def test_spawned_worker_process_initializes_celery_task_trace(monkeypatch):
    import celery
    from celery.app import trace
    from src.rag import worker

    current_process_app = object()
    initialized = []
    monkeypatch.setattr(
        celery,
        "current_app",
        SimpleNamespace(_get_current_object=lambda: current_process_app),
    )
    monkeypatch.setattr(
        trace,
        "setup_worker_optimizations",
        lambda app, hostname=None: initialized.append((app, hostname)),
    )

    worker._initialize_rag_worker_process()

    assert initialized == [(current_process_app, None)]


def _database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rag-worker.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


def test_delete_failure_keeps_document_excluded_from_retrieval(tmp_path, monkeypatch):
    from src.rag import worker

    engine, factory = _database(tmp_path)
    with factory() as session:
        session.add(RagKnowledgeBase(id="kb", name="research"))
        session.add(
            RagDocument(
                id="doc",
                knowledge_base_id="kb",
                filename="report.pdf",
                size_bytes=20,
                content_hash="a" * 64,
                blob_path=str(tmp_path / "report.pdf"),
                status="deleting",
                active_index_version_id="index-1",
                current_task_id="task-delete",
            )
        )
        session.add(
            RagIngestionTask(
                id="task-delete",
                knowledge_base_id="kb",
                document_id="doc",
                operation="delete",
                status="processing",
                stage="removing_vectors",
            )
        )
        session.commit()

    monkeypatch.setattr(worker.DatabaseManager, "get_instance", lambda: SimpleNamespace(get_session=factory))
    worker._write_failure("task-delete", RuntimeError("storage unavailable"))

    with factory() as session:
        document = session.get(RagDocument, "doc")
        task = session.get(RagIngestionTask, "task-delete")
        assert document.status == "deleting"
        assert document.active_index_version_id == "index-1"
        assert task.status == "failed"
    engine.dispose()


def test_reconciler_requeues_and_dispatches_abandoned_processing_task(tmp_path, monkeypatch):
    from src.rag import worker

    engine, factory = _database(tmp_path)
    stale = datetime.now() - timedelta(hours=1)
    with factory() as session:
        session.add(RagKnowledgeBase(id="kb", name="research"))
        session.add(
            RagDocument(
                id="doc",
                knowledge_base_id="kb",
                filename="report.pdf",
                size_bytes=20,
                content_hash="b" * 64,
                blob_path=str(tmp_path / "report.pdf"),
                status="processing",
                current_task_id="task-ingest",
            )
        )
        session.add(
            RagIngestionTask(
                id="task-ingest",
                knowledge_base_id="kb",
                document_id="doc",
                operation="ingest",
                status="processing",
                stage="embedding",
                updated_at=stale,
            )
        )
        session.commit()

    dispatched: list[str] = []
    monkeypatch.setattr(worker.DatabaseManager, "get_instance", lambda: SimpleNamespace(get_session=factory))
    monkeypatch.setattr(worker, "dispatch_ingestion_task", dispatched.append)

    recovered = worker.reconcile_queued_tasks.run()

    assert recovered == 1
    assert dispatched == ["task-ingest"]
    with factory() as session:
        task = session.get(RagIngestionTask, "task-ingest")
        assert task.status == "queued"
        assert task.stage == "worker_recovery"
        assert task.finished_at is None
    engine.dispose()


def test_index_version_flushes_parent_before_foreign_key_rows(tmp_path, monkeypatch):
    from src.rag import worker

    engine = create_engine(f"sqlite:///{tmp_path / 'rag-worker-fk.db'}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(RagKnowledgeBase(id="kb-fk", name="research"))
        session.flush()
        session.add(
            RagDocument(
                id="doc-fk",
                knowledge_base_id="kb-fk",
                filename="report.pdf",
                size_bytes=20,
                content_hash="c" * 64,
                blob_path="report.pdf",
            )
        )
        session.commit()

    monkeypatch.setattr(
        worker.DatabaseManager,
        "get_instance",
        lambda: SimpleNamespace(get_session=factory),
    )
    parser_version = "2.129.0+rapidocr-3.9.2-ch-v1"
    monkeypatch.setattr(worker, "pdf_parser_version", lambda: parser_version)
    text = "A locatable PDF passage."
    parsed = ParsedPdf(
        pages=[PdfPage(page_number=1, text=text, structure=[])],
        chunks=[
            PdfChunk(
                chunk_index=0,
                page_start=1,
                page_end=1,
                section="Introduction",
                text=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                char_start=0,
                char_end=len(text),
                metadata={},
            )
        ],
        page_count=1,
        text_length=len(text),
        parser_version=parser_version,
    )

    version, chunks = worker._new_index_version(
        {"document_id": "doc-fk"},
        parsed,
        model="qwen3-embedding:0.6b@revision",
        dimension=1024,
    )

    with factory() as session:
        assert session.get(RagIndexVersion, version.id).parser_name == "docling"
        assert session.get(RagIndexVersion, version.id).parser_version == parser_version
        assert session.query(RagPage).filter_by(index_version_id=version.id).count() == 1
        assert session.query(RagChunk).filter_by(index_version_id=version.id).count() == 1
        assert len(chunks) == 1
    engine.dispose()


def test_reindex_clones_complete_pdf_parse_without_reparsing(tmp_path, monkeypatch):
    from src.rag import worker

    engine, factory = _database(tmp_path)
    with factory() as session:
        session.add(RagKnowledgeBase(id="kb-cache", name="research"))
        session.add(
            RagDocument(
                id="doc-cache",
                knowledge_base_id="kb-cache",
                filename="report.pdf",
                size_bytes=20,
                content_hash="d" * 64,
                blob_path="report.pdf",
                page_count=1,
                chunk_count=2,
            )
        )
        for index_id, version_number, parser_name, parser_version in (
            ("matching-parse", 1, "docling", "2.129.0+rapidocr-3.9.2-ch-v1"),
            ("old-text-only-parse", 2, "docling", "2.129.0"),
            ("wrong-parser", 3, "pdfplumber", "0.11.10"),
            ("wrong-parser-version", 4, "docling", "2.128.0"),
        ):
            session.add(
                RagIndexVersion(
                    id=index_id,
                    document_id="doc-cache",
                    version=version_number,
                    status="failed",
                    parser_name=parser_name,
                    parser_version=parser_version,
                    chunking_version=CHUNKING_VERSION,
                    embedding_model="old-model@revision",
                    vector_dimension=1024,
                    qdrant_collection="old-collection",
                    page_count=1,
                    chunk_count=2,
                )
            )
            session.add(
                RagPage(
                    id=f"{index_id}:p:1",
                    document_id="doc-cache",
                    index_version_id=index_id,
                    page_number=1,
                    text_content="The complete page text.",
                    text_length=24,
                    structure_json='[{"kind":"paragraph"}]',
                )
            )
            for chunk_index, text in enumerate(("first passage", "second passage")):
                session.add(
                    RagChunk(
                        id=f"{index_id}:chunk-{chunk_index}",
                        document_id="doc-cache",
                        index_version_id=index_id,
                        chunk_index=chunk_index,
                        page_start=1,
                        page_end=1,
                        section="Section",
                        text_content=text,
                        content_hash=hashlib.sha256(text.encode()).hexdigest(),
                        char_start=chunk_index * 13,
                        char_end=(chunk_index + 1) * 13,
                        metadata_json='{"kind":"text"}',
                    )
                )
        session.commit()

    monkeypatch.setattr(worker.DatabaseManager, "get_instance", lambda: SimpleNamespace(get_session=factory))
    parser_version = "2.129.0+rapidocr-3.9.2-ch-v1"
    monkeypatch.setattr(worker, "pdf_parser_version", lambda: parser_version)
    version, chunks = worker._clone_latest_parsed_index(
        {"document_id": "doc-cache"},
        model="local-fastembed@pinned-revision",
        dimension=384,
    )

    with factory() as session:
        assert version.id not in {"matching-parse", "wrong-parser", "wrong-parser-version"}
        assert version.status == "building"
        assert version.parser_name == "docling"
        assert version.parser_version == parser_version
        assert version.embedding_model == "local-fastembed@pinned-revision"
        assert version.vector_dimension == 384
        assert session.query(RagPage).filter_by(index_version_id=version.id).count() == 1
        assert session.query(RagChunk).filter_by(index_version_id=version.id).count() == 2
        cloned_page = session.query(RagPage).filter_by(index_version_id=version.id).one()
        assert cloned_page.text_content == "The complete page text."
        cloned_chunks = (
            session.query(RagChunk)
            .filter_by(index_version_id=version.id)
            .order_by(RagChunk.chunk_index)
            .all()
        )
        assert [item.text_content for item in cloned_chunks] == ["first passage", "second passage"]
        assert [item.page_start for item in cloned_chunks] == [1, 1]
    assert [item["text"] for item in chunks] == ["first passage", "second passage"]
    engine.dispose()
