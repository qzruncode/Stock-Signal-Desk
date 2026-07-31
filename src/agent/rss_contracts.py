"""Strongly typed resources shared by RSS tools, workflows, and the UI."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictResource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class RssSourceRef(_StrictResource):
    route_path: str = Field(min_length=1, max_length=500)
    namespace: str = Field(default="", max_length=120)
    name: str = Field(default="", max_length=500)
    categories: tuple[str, ...] = ()
    parameters: tuple[dict[str, Any], ...] = ()
    features: dict[str, Any] = Field(default_factory=dict)
    readiness: Literal[
        "available",
        "requires_configuration",
        "degraded",
        "unavailable",
    ] = "available"
    auto_recommended: bool = True
    readiness_reason: str | None = Field(default=None, max_length=1000)


class RssItemRef(_StrictResource):
    route_path: str = Field(min_length=1, max_length=500)
    params: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    namespace: str = Field(default="", max_length=120)
    item_id: str = Field(default="", max_length=2000)
    title: str = Field(default="", max_length=2000)
    link: str = Field(default="", max_length=4000)
    content_hash: str = Field(min_length=64, max_length=64)


class TextChunk(_StrictResource):
    chunk_index: int = Field(ge=0)
    text: str
    content_hash: str = Field(min_length=64, max_length=64)
    page: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, max_length=500)
    char_start: int = Field(default=0, ge=0)
    char_end: int = Field(default=0, ge=0)


class TextDocumentResource(_StrictResource):
    resource_id: str = Field(min_length=1, max_length=96)
    filename: str = Field(default="document", max_length=500)
    mime_type: str = Field(default="application/octet-stream", max_length=255)
    size_bytes: int = Field(default=0, ge=0)
    content_hash: str = Field(min_length=64, max_length=64)
    preview_url: str = Field(default="", max_length=2000)
    download_url: str = Field(default="", max_length=2000)
    extraction_status: Literal[
        "pending",
        "extracted",
        "empty_text_layer",
        "unsupported",
        "failed",
    ] = "pending"
    extraction_method: str | None = Field(default=None, max_length=120)
    text_length: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    source_item_ref: RssItemRef | None = None
    error: str | None = Field(default=None, max_length=2000)


class EvidenceRecord(_StrictResource):
    evidence_id: str = Field(min_length=1, max_length=96)
    source_type: Literal["rss_item", "text_document"]
    title: str = Field(default="", max_length=2000)
    source_url: str = Field(default="", max_length=4000)
    published: str | None = Field(default=None, max_length=120)
    locator: str = Field(default="", max_length=500)
    text: str
    content_hash: str = Field(min_length=64, max_length=64)
    item_ref: RssItemRef | None = None
    resource_id: str | None = Field(default=None, max_length=96)


class EvidenceCollection(_StrictResource):
    records: tuple[EvidenceRecord, ...] = ()
    source_item_ref: RssItemRef | None = None
    resource_ids: tuple[str, ...] = ()
    coverage_complete: bool = False


class RssCoverage(_StrictResource):
    planned_sources: int = Field(default=0, ge=0)
    attempted_sources: int = Field(default=0, ge=0)
    successful_sources: int = Field(default=0, ge=0)
    item_count: int = Field(default=0, ge=0)
    text_documents_found: int = Field(default=0, ge=0)
    text_documents_extracted: int = Field(default=0, ge=0)
    discarded_non_text: int = Field(default=0, ge=0)
    failures: tuple[str, ...] = ()


__all__ = [
    "EvidenceCollection",
    "EvidenceRecord",
    "RssCoverage",
    "RssItemRef",
    "RssSourceRef",
    "TextChunk",
    "TextDocumentResource",
]
