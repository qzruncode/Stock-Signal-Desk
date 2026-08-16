"""Resolve the body of one exact RSS item without recursive attachment work."""

from __future__ import annotations

from hashlib import sha256
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


def _bind_registered_source_ref(ref: RssItemRef) -> RssItemRef:
    """Bind an Agent item reference back to one fixed registered RSS source.

    ``read_rss_item`` is also used by legacy HTTP compatibility helpers, where
    arbitrary historical RSSHub routes remain acceptable.  The model-visible
    wrapper below is stricter: a model may only follow an item emitted by one
    of the explicit ``read_rss_*`` source tools.  This prevents an item-detail
    action from becoming a disguised route-selection adapter.
    """
    from src.tools.rss_sources import RSS_SOURCE_DEFINITIONS

    matches = [
        source
        for source in RSS_SOURCE_DEFINITIONS
        if source.route_path == ref.route_path
    ]
    if len(matches) != 1:
        raise ValueError(
            "item_ref 必须来自一个已注册的 read_rss_* 固定来源工具"
        )
    source = matches[0]
    allowed_parameters = {parameter.name for parameter in source.parameters}
    unexpected_parameters = sorted(set(ref.params) - allowed_parameters)
    if unexpected_parameters:
        raise ValueError(
            "item_ref 包含该固定来源不接受的路径参数: "
            + ", ".join(unexpected_parameters)
        )
    missing_required = [
        parameter.name
        for parameter in source.parameters
        if parameter.required and ref.params.get(parameter.name) in (None, "")
    ]
    if missing_required:
        raise ValueError(
            "item_ref 缺少该固定来源的必要路径参数: "
            + ", ".join(missing_required)
        )
    if ref.namespace and ref.namespace != source.namespace:
        raise ValueError("item_ref 的 namespace 与固定来源不一致")
    return ref.model_copy(update={"namespace": source.namespace})


def _verified_list_item_for_ref(
    ref: RssItemRef,
    item: dict[str, Any] | str | None,
) -> dict[str, Any]:
    """Accept list-body fallback text only when it is the referenced item."""
    raw_item = object_value(item, "item")
    if not raw_item:
        return {}
    from api.v1.endpoints._rss_text import item_content_hash, normalize_text_item

    normalized = normalize_text_item(raw_item)
    content_hash = str(normalized.get("content_hash") or item_content_hash(normalized))
    if content_hash != ref.content_hash:
        raise ValueError(
            "item 的内容哈希与 item_ref 不一致，不能将模型提供的正文作为来源证据"
        )
    return normalized


def read_rss_item(
    item_ref: dict[str, Any] | str,
    item: dict[str, Any] | str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    from api.v1.endpoints._rss_text import normalize_text_item
    from api.v1.endpoints.rss import (
        FeedItemDetailRequest,
        get_rss_feed_item_detail,
    )

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
    content_origin = str(detail.get("_content_origin") or "fulltext_refetch")
    if content_origin not in {"fulltext_refetch", "list_item_fallback"}:
        content_origin = "unknown"
    used_list_item_fallback = content_origin == "list_item_fallback"
    content_text = html_text(
        detail.get("content_html") or detail.get("summary") or ""
    )
    chunks = _content_chunks(content_text)
    attachments = [
        value
        for value in detail.get("attachments") or []
        if isinstance(value, dict)
    ]
    success = bool(content_text or detail.get("link"))
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
    evidence_collection = EvidenceCollection(
        records=tuple(evidence_records),
        source_item_ref=ref,
        resource_ids=(),
        # Attachment bytes have not been fetched or interpreted by this tool.
        # A list-item fallback stays tied to the same source and item, but it
        # is not proof that the full article was retrieved.  Likewise,
        # attachment metadata is not attachment text coverage.
        coverage_complete=success and not attachments and not used_list_item_fallback,
    ).model_dump(mode="json")
    report_tool_progress("RSS 条目正文已读取", progress=100)
    return {
        "success": success,
        "partial": used_list_item_fallback,
        "content_access": {
            "mode": "content_read",
            "content_read": success,
            "content_extracted": bool(content_text),
            "content_read_required": True,
            "content_length": len(content_text),
            "requested_url": str(detail.get("link") or ref.link or ""),
        },
        "item_ref": ref.model_dump(mode="json"),
        "title": detail.get("title") or ref.title,
        "link": detail.get("link") or ref.link,
        "published": detail.get("published"),
        "author": detail.get("author"),
        "tags": detail.get("tags") or [],
        "content_text": content_text,
        "content_length": len(content_text),
        "content_chunks": chunks,
        "content_origin": content_origin,
        "fallback_used": used_list_item_fallback,
        "evidence_collection": evidence_collection,
        "attachments": attachments,
        "coverage": {
            "planned_sources": 1,
            "attempted_sources": 1,
            "successful_sources": 1 if success else 0,
            "item_count": 1,
            "text_documents_found": len(attachments),
            "text_documents_extracted": 0,
            "discarded_non_text": int(
                detail.get("_discarded_non_text") or 0
            ),
            "failures": (
                ["全文重取未返回可用正文，保留同一条目此前的列表正文"]
                if used_list_item_fallback
                else []
            ),
        },
        "data_time": detail.get("published"),
        "is_stale": None,
        "freshness_unknown": not bool(detail.get("published")),
        "errors": [],
        "warnings": [
            *(
                [
                    "全文重取未返回可用正文；当前内容来自同一条目的列表正文，"
                    "不能视为完整文章覆盖。"
                ]
                if used_list_item_fallback
                else []
            ),
            *(
                ["条目含文本附件；本工具仅返回附件元数据，如需读取请对其 URL 单独调用网页抓取工具。"]
                if attachments
                else []
            ),
        ],
    }


def read_registered_rss_item(
    item_ref: dict[str, Any] | str,
    item: dict[str, Any] | str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Model-visible wrapper that preserves one-source RSS item identity."""
    ref = _bind_registered_source_ref(
        RssItemRef.model_validate(object_value(item_ref, "item_ref"))
    )
    trusted_item = _verified_list_item_for_ref(ref, item)
    return read_rss_item(
        ref.model_dump(mode="json"),
        trusted_item or None,
        force=force,
    )


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
        "按任一 read_rss_* 来源工具返回的稳定 item_ref 读取同一条目的正文。"
        "只读取该条目本身，不解析附件，也不会根据标题重新猜测其他条目；"
        "若源站全文重取失败，会保留同条目的列表正文并明确标记为部分覆盖。"
    ),
    parameters=object_schema(
        {
            "item_ref": _ITEM_REF_SCHEMA,
            "item": {
                "type": "object",
                "description": "read_rss_* 来源工具返回的原条目，用于可靠全文回退",
                "additionalProperties": True,
            },
            "force": {"type": "boolean", "default": False},
        },
        required=("item_ref",),
    ),
    executor=read_registered_rss_item,
    category="sentiment",
)


__all__ = ["TOOL", "read_registered_rss_item", "read_rss_item"]
