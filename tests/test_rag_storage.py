from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.storage.migrations import SCHEMA_VERSION, ensure_compatible_schema, get_schema_version
from src.storage.models import RagDocument, RagKnowledgeBase


def test_rag_schema_creation_is_idempotent_and_versioned() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        ensure_compatible_schema(engine)
        ensure_compatible_schema(engine)

        names = set(inspect(engine).get_table_names())
        assert {
            "rag_knowledge_bases",
            "rag_documents",
            "rag_document_sources",
            "rag_index_versions",
            "rag_ingestion_tasks",
            "rag_pages",
            "rag_chunks",
        }.issubset(names)
        assert get_schema_version(engine) == SCHEMA_VERSION
    finally:
        engine.dispose()


def test_rag_document_content_hash_is_unique_per_knowledge_base() -> None:
    engine = create_engine("sqlite:///:memory:")
    RagKnowledgeBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(RagKnowledgeBase(id="kb-1", name="research"))
        session.add(
            RagDocument(
                id="doc-1",
                knowledge_base_id="kb-1",
                filename="report.pdf",
                size_bytes=10,
                content_hash="a" * 64,
                blob_path="/tmp/report.pdf",
            )
        )
        session.commit()

        session.add(
            RagDocument(
                id="doc-2",
                knowledge_base_id="kb-1",
                filename="copy.pdf",
                size_bytes=10,
                content_hash="a" * 64,
                blob_path="/tmp/copy.pdf",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            RagKnowledgeBase(id="kb-2", name="other")
        )
        session.add(
            RagDocument(
                id="doc-3",
                knowledge_base_id="kb-2",
                filename="report.pdf",
                size_bytes=10,
                content_hash="a" * 64,
                blob_path="/tmp/report.pdf",
            )
        )
        session.commit()
