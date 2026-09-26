"""Search only the knowledge bases attached by the server to this run."""

from __future__ import annotations

from typing import Any

from src.rag.retrieval import RagSearchService
from src.tools.base import ToolSpec, current_tool_execution_context, object_schema


def search_knowledge_base(query: str) -> dict[str, Any]:
    context = current_tool_execution_context()
    knowledge_base_ids = [
        item
        for item in str(context.get("knowledge_base_ids") or "").split(",")
        if item.strip()
    ]
    if not knowledge_base_ids:
        return {
            "success": True,
            "query": str(query or ""),
            "results": [],
            "result_items": [],
            "no_evidence": True,
            "message": "本轮对话未启用知识库。",
            "errors": [],
        }
    try:
        return RagSearchService().search(
            query,
            knowledge_base_ids=knowledge_base_ids,
            tenant_id=str(context.get("tenant_id") or "local"),
            owner_id=str(context.get("owner_id") or "admin"),
            top_k=5,
        )
    except Exception as exc:
        # Do not leak gateway payloads or document content into run logs.
        return {
            "success": False,
            "query": str(query or "")[:500],
            "results": [],
            "result_items": [],
            "no_evidence": True,
            "errors": [str(exc)[:500]],
            "error_code": str(getattr(exc, "code", "retrieval_failed")),
            "retryable": bool(getattr(exc, "retryable", False)),
        }


TOOL = ToolSpec(
    name="search_knowledge_base",
    description=(
        "在本轮用户已选择的 PDF 知识库中检索证据。知识库范围由服务器限定，不得询问或传入知识库/文档标识；"
        "命中结果含文件名、页码和原文片段，可用于回答并提供可核验引用。"
    ),
    parameters=object_schema(
        {"query": {"type": "string", "minLength": 1, "maxLength": 2_000}},
        required=("query",),
    ),
    executor=search_knowledge_base,
    category="research",
    web_fallback=False,
)


__all__ = ["TOOL", "search_knowledge_base"]
