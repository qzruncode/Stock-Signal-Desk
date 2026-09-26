"""Durable storage models for reusable PDF knowledge bases."""

from __future__ import annotations

import src.storage.models as _models

for _name, _value in vars(_models).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = [
    "RagKnowledgeBase",
    "RagDocument",
    "RagDocumentSource",
    "RagIndexVersion",
    "RagIngestionTask",
    "RagPage",
    "RagChunk",
]


class RagKnowledgeBase(Base):
    """A workspace-owned collection of reusable source documents."""

    __tablename__ = "rag_knowledge_bases"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=False, default="local", index=True)
    owner_id = Column(String(128), nullable=False, default="admin", index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text, nullable=False, default="")
    status = Column(String(24), nullable=False, default="active", index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        index=True,
    )

    __table_args__ = (
        Index("ix_rag_knowledge_bases_owner_status", "tenant_id", "owner_id", "status"),
    )


class RagDocument(Base):
    """Uploaded PDF metadata; the original binary is kept in durable storage."""

    __tablename__ = "rag_documents"

    id = Column(String(96), primary_key=True)
    knowledge_base_id = Column(
        String(64),
        ForeignKey("rag_knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(String(64), nullable=False, default="local", index=True)
    owner_id = Column(String(128), nullable=False, default="admin", index=True)
    filename = Column(String(500), nullable=False)
    mime_type = Column(String(120), nullable=False, default="application/pdf")
    size_bytes = Column(Integer, nullable=False, default=0)
    content_hash = Column(String(64), nullable=False)
    blob_path = Column(Text, nullable=False)
    status = Column(String(24), nullable=False, default="queued", index=True)
    page_count = Column(Integer, nullable=False, default=0)
    text_length = Column(Integer, nullable=False, default=0)
    chunk_count = Column(Integer, nullable=False, default=0)
    active_index_version_id = Column(String(64), index=True)
    current_task_id = Column(String(64), index=True)
    error_code = Column(String(80))
    error_detail = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "knowledge_base_id",
            "content_hash",
            name="uix_rag_document_knowledge_base_hash",
        ),
        Index("ix_rag_document_scope_status", "tenant_id", "owner_id", "status"),
    )


class RagDocumentSource(Base):
    """Verified official-source provenance for an imported PDF document."""

    __tablename__ = "rag_document_sources"

    document_id = Column(
        String(96),
        ForeignKey("rag_documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    provider = Column(String(120), nullable=False)
    exchange = Column(String(40), nullable=False)
    security_code = Column(String(16), nullable=False)
    security_name = Column(String(200), nullable=False)
    report_type = Column(String(24), nullable=False)
    report_period = Column(String(80), nullable=False)
    announcement_id = Column(String(120), nullable=False)
    announcement_title = Column(String(500), nullable=False)
    published_at = Column(String(40))
    pdf_url = Column(Text, nullable=False)
    announcement_url = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        Index("ix_rag_document_source_security", "security_code", "report_period"),
    )


class RagIndexVersion(Base):
    """One immutable parser/chunker/embedding index build for a PDF."""

    __tablename__ = "rag_index_versions"

    id = Column(String(64), primary_key=True)
    document_id = Column(
        String(96),
        ForeignKey("rag_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False, default="building", index=True)
    parser_name = Column(String(80), nullable=False, default="docling")
    parser_version = Column(String(40), nullable=False)
    chunking_version = Column(String(40), nullable=False)
    embedding_model = Column(String(200), nullable=False)
    vector_dimension = Column(Integer, nullable=False)
    qdrant_collection = Column(String(120), nullable=False)
    page_count = Column(Integer, nullable=False, default=0)
    chunk_count = Column(Integer, nullable=False, default=0)
    verification_json = Column(Text, nullable=False, default="{}")
    error_code = Column(String(80))
    error_detail = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    activated_at = Column(DateTime, index=True)

    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uix_rag_index_version_number"),
        Index("ix_rag_index_version_status_created", "status", "created_at"),
    )


class RagIngestionTask(Base):
    """Business-owned task receipt used for retries and worker recovery."""

    __tablename__ = "rag_ingestion_tasks"

    id = Column(String(64), primary_key=True)
    knowledge_base_id = Column(
        String(64),
        ForeignKey("rag_knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id = Column(
        String(96),
        ForeignKey("rag_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    operation = Column(String(24), nullable=False, default="ingest")
    status = Column(String(24), nullable=False, default="queued", index=True)
    stage = Column(String(40), nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    attempt = Column(Integer, nullable=False, default=0)
    celery_task_id = Column(String(128), index=True)
    error_code = Column(String(80))
    error_detail = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        index=True,
    )
    started_at = Column(DateTime, index=True)
    finished_at = Column(DateTime, index=True)

    __table_args__ = (
        Index("ix_rag_task_status_updated", "status", "updated_at"),
        Index("ix_rag_task_document_created", "document_id", "created_at"),
    )


class RagPage(Base):
    """Parsed page text and structural preview for one immutable index build."""

    __tablename__ = "rag_pages"

    id = Column(String(96), primary_key=True)
    document_id = Column(
        String(96),
        ForeignKey("rag_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    index_version_id = Column(
        String(64),
        ForeignKey("rag_index_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_number = Column(Integer, nullable=False)
    text_content = Column(Text, nullable=False, default="")
    text_length = Column(Integer, nullable=False, default=0)
    structure_json = Column(Text, nullable=False, default="[]")

    __table_args__ = (
        UniqueConstraint("index_version_id", "page_number", name="uix_rag_page_version_number"),
    )


class RagChunk(Base):
    """Stable locatable retrieval unit; body is intentionally not copied into logs."""

    __tablename__ = "rag_chunks"

    id = Column(String(128), primary_key=True)
    document_id = Column(
        String(96),
        ForeignKey("rag_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    index_version_id = Column(
        String(64),
        ForeignKey("rag_index_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index = Column(Integer, nullable=False)
    page_start = Column(Integer, nullable=False)
    page_end = Column(Integer, nullable=False)
    section = Column(String(500), nullable=False, default="")
    text_content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False, index=True)
    char_start = Column(Integer, nullable=False, default=0)
    char_end = Column(Integer, nullable=False, default=0)
    metadata_json = Column(Text, nullable=False, default="{}")

    __table_args__ = (
        UniqueConstraint("index_version_id", "chunk_index", name="uix_rag_chunk_version_index"),
        Index("ix_rag_chunk_document_page", "document_id", "page_start", "page_end"),
    )
