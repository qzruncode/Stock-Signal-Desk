"""Resolve one exact RSS item and materialize its text-bearing documents."""

from __future__ import annotations

from hashlib import sha256
import re
from typing import Any

from src.agent.rss_contracts import (
    EvidenceCollection,
    EvidenceRecord,
    RssItemRef,
    TextChunk,
)
from src.tools._rss_agent import (
    endpoint_value,
    html_text,
    object_value,
    rss_options_schema,
)
from src.tools.base import (
    ToolSpec,
    current_tool_execution_context,
    object_schema,
    report_tool_progress,
)


def _content_chunks(text: str) -> list[dict[str, Any]]:
    normalized = str(text or "").strip()
    chunks: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(normalized):
        hard_end = min(len(normalized), cursor + 4_000)
        end = hard_end
        if hard_end < len(normalized):
            boundary = max(
                normalized.rfind("\n\n", cursor, hard_end),
                normalized.rfind("。", cursor, hard_end),
            )
            if boundary > cursor + 2_000:
                end = boundary + 1
        value = normalized[cursor:end].strip()
        if value:
            chunks.append(
                TextChunk(
                    chunk_index=len(chunks),
                    text=value,
                    content_hash=sha256(value.encode("utf-8")).hexdigest(),
                    char_start=cursor,
                    char_end=end,
                ).model_dump(mode="json")
            )
        if end >= len(normalized):
            break
        cursor = max(cursor + 1, end - 300)
    return chunks


def read_rss_item(
    item_ref: dict[str, Any] | str,
    item: dict[str, Any] | str | None = None,
    include_documents: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    from api.v1.endpoints._rss_text import (
        is_text_document_reference,
        normalize_text_item,
    )
    from api.v1.endpoints.rss import (
        FeedItemDetailRequest,
        get_rss_feed_item_detail,
    )
    from src.services.text_document_service import (
        materialize_text_documents,
    )
    from src.storage import DatabaseManager

    ref = RssItemRef.model_validate(
        object_value(item_ref, "item_ref")
    )
    list_item = object_value(item, "item")
    report_tool_progress("正在提取 RSS 条目正文", progress=20)
    body = FeedItemDetailRequest(
        route_path=ref.route_path,
        params=ref.params,
        options=ref.options,
        namespace=ref.namespace or None,
        item_id=ref.item_id,
        title=ref.title,
        link=ref.link,
        force=bool(force),
        content_html=str(list_item.get("content_html") or ""),
        summary=str(list_item.get("summary") or ""),
        published=str(list_item.get("published") or ""),
        author=str(list_item.get("author") or ""),
        tags=[
            str(value) for value in list_item.get("tags") or []
        ],
        attachments=[
            value
            for value in list_item.get("attachments") or []
            if isinstance(value, dict)
        ],
    )
    detail = normalize_text_item(
        endpoint_value(lambda: get_rss_feed_item_detail(body))
    )
    content_text = html_text(
        detail.get("content_html") or detail.get("summary") or ""
    )
    chunks = _content_chunks(content_text)
    attachments = [
        value
        for value in detail.get("attachments") or []
        if isinstance(value, dict)
    ]
    if (
        ref.link
        and is_text_document_reference(
            url=ref.link,
            mime_type="",
            title=ref.title,
        )
        and not any(
            str(value.get("url") or "") == ref.link
            for value in attachments
        )
    ):
        attachments.insert(
            0,
            {
                "url": ref.link,
                "mime_type": "",
                "title": ref.title,
            },
        )
    resources: list[dict[str, Any]] = []
    document_evidence: list[dict[str, Any]] = []
    document_chunk_records: list[dict[str, Any]] = []
    document_errors: list[str] = []
    context = current_tool_execution_context()
    if include_documents and attachments:
        report_tool_progress("正在解析原始文本文件", progress=55)
        conversation_id = context.get("conversation_id") or ""
        if conversation_id:
            resources, document_errors = materialize_text_documents(
                db=DatabaseManager.get_instance(),
                conversation_id=conversation_id,
                run_id=context.get("run_id") or "",
                attachments=attachments,
                source_item_ref=ref.model_dump(mode="json"),
            )
            for resource in resources:
                if resource.get("extraction_status") == "extracted":
                    continue
                document_errors.append(
                    (
                        f"{resource.get('filename') or resource.get('resource_id')}: "
                        + str(
                            resource.get("error")
                            or (
                                "原文件没有可提取的文本层"
                                if resource.get("extraction_status")
                                == "empty_text_layer"
                                else "文本提取失败"
                            )
                        )
                    )
                )
            from src.tools.read_text_document import _coverage_digest

            database = DatabaseManager.get_instance()
            for resource in resources:
                if resource.get("extraction_status") != "extracted":
                    continue
                resource_chunks: list[dict[str, Any]] = []
                chunk_offset = 0
                while True:
                    page = database.get_text_document_chunks(
                        str(resource.get("resource_id") or ""),
                        offset=chunk_offset,
                        limit=500,
                    )
                    resource_chunks.extend(page)
                    if len(page) < 500:
                        break
                    chunk_offset += len(page)
                digest = _coverage_digest(
                    resource_chunks,
                    character_budget=24_000,
                )
                document_chunk_records.extend(
                    {
                        "resource_id": resource.get("resource_id"),
                        "filename": resource.get("filename"),
                        "content_hash": resource.get("content_hash"),
                        "chunk": resource_chunk,
                    }
                    for resource_chunk in resource_chunks
                )
                document_evidence.append(
                    {
                        "resource_id": resource.get("resource_id"),
                        "filename": resource.get("filename"),
                        "content_hash": resource.get("content_hash"),
                        "chunk_count": len(resource_chunks),
                        **digest,
                    }
                )
        else:
            document_errors.append(
                "当前调用没有会话上下文，未持久化原始文档"
            )
    successful_documents = sum(
        1
        for resource in resources
        if resource.get("extraction_status") == "extracted"
    )
    errors = list(document_errors)
    success = bool(content_text or resources or detail.get("link"))
    evidence_records: list[EvidenceRecord] = []
    for chunk in chunks:
        locator = (
            f"page:{chunk.get('page')}"
            if chunk.get("page")
            else f"chars:{chunk.get('char_start')}-{chunk.get('char_end')}"
        )
        evidence_records.append(
            EvidenceRecord(
                evidence_id=(
                    "evidence_"
                    + sha256(
                        (
                            ref.content_hash
                            + ":"
                            + str(chunk.get("chunk_index") or 0)
                        ).encode("utf-8")
                    ).hexdigest()[:32]
                ),
                source_type="rss_item",
                title=str(detail.get("title") or ref.title),
                source_url=str(detail.get("link") or ref.link),
                published=(
                    str(detail.get("published"))
                    if detail.get("published")
                    else None
                ),
                locator=locator,
                text=str(chunk.get("text") or ""),
                content_hash=str(chunk.get("content_hash") or ""),
                item_ref=ref,
            )
        )
    for value in document_chunk_records:
        chunk = value.get("chunk")
        if not isinstance(chunk, dict):
            continue
        text = str(chunk.get("text") or "")
        if not text:
            continue
        locator = (
            f"page:{chunk.get('page')}"
            if chunk.get("page")
            else str(
                chunk.get("section")
                or (
                    f"chars:{chunk.get('char_start')}-"
                    f"{chunk.get('char_end')}"
                )
            )
        )
        evidence_records.append(
            EvidenceRecord(
                evidence_id=(
                    "evidence_"
                    + sha256(
                        (
                            str(value.get("content_hash") or "")
                            + ":"
                            + str(chunk.get("chunk_index") or 0)
                        ).encode("utf-8")
                    ).hexdigest()[:32]
                ),
                source_type="text_document",
                title=str(value.get("filename") or ""),
                source_url="",
                published=(
                    str(detail.get("published"))
                    if detail.get("published")
                    else None
                ),
                locator=locator,
                text=text,
                content_hash=str(
                    chunk.get("content_hash")
                    or sha256(text.encode("utf-8")).hexdigest()
                ),
                item_ref=ref,
                resource_id=str(value.get("resource_id") or ""),
            )
        )
    evidence_collection = EvidenceCollection(
        records=tuple(evidence_records),
        source_item_ref=ref,
        resource_ids=tuple(
            str(resource.get("resource_id") or "")
            for resource in resources
            if resource.get("resource_id")
        ),
        coverage_complete=(
            not document_errors
            and all(
                resource.get("extraction_status") == "extracted"
                for resource in resources
            )
            and all(
                value.get("coverage_complete") is True
                for value in document_evidence
            )
            and len(document_evidence) == successful_documents
        ),
    ).model_dump(mode="json")
    report_tool_progress("正文与文档证据已生成", progress=100)
    return {
        "success": success,
        "partial": success and bool(errors),
        "item_ref": ref.model_dump(mode="json"),
        "title": detail.get("title") or ref.title,
        "link": detail.get("link") or ref.link,
        "published": detail.get("published"),
        "author": detail.get("author"),
        "tags": detail.get("tags") or [],
        "content_text": content_text,
        "content_length": len(content_text),
        "content_chunks": chunks,
        "resources": resources,
        "document_evidence": document_evidence,
        "evidence_collection": evidence_collection,
        "attachments": attachments,
        "coverage": {
            "planned_sources": 1,
            "attempted_sources": 1,
            "successful_sources": 1 if success else 0,
            "item_count": 1,
            "text_documents_found": len(attachments),
            "text_documents_extracted": successful_documents,
            "discarded_non_text": int(
                detail.get("_discarded_non_text") or 0
            ),
            "failures": errors,
        },
        "data_time": detail.get("published"),
        "is_stale": None,
        "freshness_unknown": not bool(detail.get("published")),
        "errors": errors,
        "warnings": errors,
    }


_ITEM_REF_SCHEMA = {
    "type": "object",
    "properties": {
        "route_path": {"type": "string"},
        "params": {"type": "object", "additionalProperties": True},
        "options": rss_options_schema(),
        "namespace": {"type": "string"},
        "item_id": {"type": "string"},
        "title": {"type": "string"},
        "link": {"type": "string"},
        "content_hash": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
        },
    },
    "required": ["route_path", "content_hash"],
    "additionalProperties": False,
}


TOOL = ToolSpec(
    name="read_rss_item",
    description=(
        "按 read_rss_feed 返回的稳定 item_ref 读取准确条目全文，并按需解析其中的 PDF、"
        "Office 和纯文本附件。不会根据标题重新猜测其他条目。"
    ),
    parameters=object_schema(
        {
            "item_ref": _ITEM_REF_SCHEMA,
            "item": {
                "type": "object",
                "description": "read_rss_feed 返回的原条目，用于可靠全文回退",
                "additionalProperties": True,
            },
            "include_documents": {"type": "boolean", "default": True},
            "force": {"type": "boolean", "default": False},
        },
        required=("item_ref",),
    ),
    executor=read_rss_item,
    category="sentiment",
)


__all__ = ["TOOL", "read_rss_item"]
