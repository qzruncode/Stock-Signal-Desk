"""Read stored text chunks from the same original file shown in chat."""

from __future__ import annotations

from typing import Any

from src.tools.base import (
    ToolSpec,
    current_tool_execution_context,
    object_schema,
)


def _coverage_digest(
    chunks: list[dict[str, Any]],
    *,
    character_budget: int = 36_000,
) -> dict[str, Any]:
    if not chunks:
        return {
            "text": "",
            "covered_chunk_indices": [],
            "coverage_complete": True,
        }
    per_chunk = max(
        80,
        min(900, character_budget // len(chunks)),
    )
    parts: list[str] = []
    covered: list[int] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        index = int(chunk.get("chunk_index") or 0)
        locator = (
            f"页 {chunk.get('page')}"
            if chunk.get("page")
            else str(chunk.get("section") or f"片段 {index}")
        )
        parts.append(
            f"[{locator} / chunk {index}] "
            + text[:per_chunk].rstrip()
        )
        covered.append(index)
    return {
        "text": "\n\n".join(parts),
        "covered_chunk_indices": covered,
        "coverage_complete": len(covered) == len(chunks),
    }


def read_text_document(
    resource_id: str,
    query: str = "",
    offset: int = 0,
    limit: int = 20,
    page_start: int | None = None,
    page_end: int | None = None,
    reading_mode: str = "targeted",
) -> dict[str, Any]:
    from hashlib import sha256

    from src.agent.rss_contracts import (
        EvidenceCollection,
        EvidenceRecord,
        RssItemRef,
    )
    from src.services.text_document_service import read_text_resource
    from src.storage import DatabaseManager

    normalized_mode = str(reading_mode or "targeted").strip().lower()
    if normalized_mode not in {"targeted", "complete"}:
        raise ValueError("reading_mode 必须为 targeted 或 complete")
    database = DatabaseManager.get_instance()
    context = current_tool_execution_context()
    conversation_id = str(context.get("conversation_id") or "")
    resource, chunks = read_text_resource(
        db=database,
        resource_id=str(resource_id or "").strip(),
        query=(
            str(query or "").strip()
            if normalized_mode == "targeted"
            else ""
        ),
        offset=max(0, int(offset or 0)),
        limit=max(1, min(int(limit or 20), 100)),
        page_start=page_start,
        page_end=page_end,
        conversation_id=conversation_id or None,
    )
    coverage_digest = None
    if normalized_mode == "complete":
        all_chunks: list[dict[str, Any]] = []
        chunk_offset = 0
        while True:
            page = database.get_text_document_chunks(
                str(resource_id or "").strip(),
                offset=chunk_offset,
                limit=500,
                page_start=page_start,
                page_end=page_end,
            )
            all_chunks.extend(page)
            if len(page) < 500:
                break
            chunk_offset += len(page)
        chunks = all_chunks
        coverage_digest = _coverage_digest(all_chunks)
    total = int(resource.get("chunk_count") or 0)
    returned = len(chunks)
    next_offset = offset + returned
    has_more = (
        normalized_mode == "targeted"
        and not query
        and page_start is None
        and page_end is None
        and next_offset < total
    )
    success = resource.get("extraction_status") == "extracted"
    errors = (
        []
        if success
        else [
            str(
                resource.get("error")
                or "原文件没有可提取的文本层"
            )
        ]
    )
    source_item_ref = None
    if isinstance(resource.get("source_item_ref"), dict):
        source_item_ref = RssItemRef.model_validate(
            resource["source_item_ref"]
        )
    evidence_records = tuple(
        EvidenceRecord(
            evidence_id=(
                "evidence_"
                + sha256(
                    (
                        str(resource.get("content_hash") or "")
                        + ":"
                        + str(chunk.get("chunk_index") or 0)
                        + ":"
                        + normalized_mode
                    ).encode("utf-8")
                ).hexdigest()[:32]
            ),
            source_type="text_document",
            title=str(resource.get("filename") or ""),
            source_url="",
            published=None,
            locator=(
                f"page:{chunk.get('page')}"
                if chunk.get("page")
                else str(
                    chunk.get("section")
                    or f"chunk:{chunk.get('chunk_index')}"
                )
            ),
            text=str(chunk.get("text") or ""),
            content_hash=sha256(
                str(chunk.get("text") or "").encode("utf-8")
            ).hexdigest(),
            item_ref=source_item_ref,
            resource_id=str(resource.get("resource_id") or ""),
        )
        for chunk in chunks
        if str(chunk.get("text") or "")
    )
    evidence_collection = EvidenceCollection(
        records=evidence_records,
        source_item_ref=source_item_ref,
        resource_ids=(str(resource.get("resource_id") or ""),),
        coverage_complete=(
            success
            and (
                normalized_mode == "targeted"
                or bool(
                    coverage_digest
                    and coverage_digest.get("coverage_complete")
                )
            )
        ),
    ).model_dump(mode="json")
    return {
        "success": success,
        "partial": False,
        "content_access": {
            "mode": "content_read",
            "content_read": success,
            "content_extracted": bool(chunks),
            "content_read_required": True,
            "content_length": sum(len(str(chunk.get("text") or "")) for chunk in chunks),
            "resource_id": str(resource.get("resource_id") or ""),
        },
        "resource": resource,
        "resources": [resource],
        "chunks": chunks,
        "evidence_collection": evidence_collection,
        "returned_count": returned,
        "total_chunk_count": total,
        "offset": int(offset or 0),
        "next_offset": next_offset if has_more else None,
        "has_more": has_more,
        "query": str(query or "").strip(),
        "reading_mode": normalized_mode,
        "coverage_digest": coverage_digest,
        "coverage": {
            "planned_sources": 1,
            "attempted_sources": 1,
            "successful_sources": 1 if evidence_records else 0,
            "item_count": len(evidence_records),
            "text_documents_found": 1,
            "text_documents_extracted": 1 if success else 0,
            "discarded_non_text": 0,
            "failures": errors,
        },
        "data_time": None,
        "is_stale": None,
        "freshness_unknown": True,
        "errors": errors,
        "warnings": errors,
    }


TOOL = ToolSpec(
    name="read_text_document",
    description=(
        "读取会话中 TextDocumentResource 的文本分块。可按查询、页码或顺序分页读取；"
        "resource_id 对应会话里展示的同一份原文件。"
    ),
    parameters=object_schema(
        {
            "resource_id": {"type": "string"},
            "query": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
            },
            "page_start": {"type": ["integer", "null"], "minimum": 1},
            "page_end": {"type": ["integer", "null"], "minimum": 1},
            "reading_mode": {
                "type": "string",
                "enum": ["targeted", "complete"],
                "default": "targeted",
            },
        },
        required=("resource_id",),
    ),
    executor=read_text_document,
    category="sentiment",
)


__all__ = ["TOOL", "read_text_document"]
