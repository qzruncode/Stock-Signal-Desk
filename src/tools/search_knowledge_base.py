"""Search only the caller-authorized, currently searchable PDF libraries."""

from __future__ import annotations

from typing import Any

from src.rag.retrieval import RagSearchService
from src.tools.base import ToolSpec, current_tool_execution_context, object_schema


def _ids(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _tool_error(query: str, code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "query": str(query or "")[:500],
        "results": [],
        "result_items": [],
        "no_evidence": False,
        "retrieval_completed": False,
        "errors": [message],
        "error_code": code,
        "retryable": False,
    }


def search_knowledge_base(query: str) -> dict[str, Any]:
    context = current_tool_execution_context()
    selected_ids = _ids(str(context.get("knowledge_base_ids") or "").split(","))
    if not selected_ids:
        return _tool_error(
            query,
            "knowledge_base_scope_missing",
            "本轮没有用户授权的知识库范围，因此没有执行检索。请先在对话中选择知识库。",
        )

    tenant_id = str(context.get("tenant_id") or "local")
    owner_id = str(context.get("owner_id") or "admin")
    try:
        # RagSearchService revalidates every server-injected ID against the
        # current tenant/owner and active index. The model never supplies IDs.
        return RagSearchService().search(
            query,
            knowledge_base_ids=selected_ids,
            tenant_id=tenant_id,
            owner_id=owner_id,
            top_k=5,
        )
    except Exception as exc:
        # Do not leak gateway payloads or document content into run logs.
        return {
            "success": False,
            "query": str(query or "")[:500],
            "results": [],
            "result_items": [],
            "no_evidence": False,
            "retrieval_completed": False,
            "errors": [str(exc)[:500]],
            "error_code": str(getattr(exc, "code", "retrieval_failed")),
            "retryable": bool(getattr(exc, "retryable", False)),
        }


TOOL = ToolSpec(
    name="search_knowledge_base",
    description=(
        "当你判断当前问题需要本轮用户已授权知识库中的证据时调用；是否调用由你结合当前问题和已有证据决定。"
        "工具仅接收 query，知识库范围由服务端从当前会话选择中注入并在执行时重新校验；不要提供知识库或文档 ID。"
        "先结合本轮问题和对话上下文确定检索主题，"
        "再生成有针对性的 query，保留区分目标的实体、报告期/版本、指标/概念和口径；"
        "需要核对同表同期对比时，同时加入表名/章节名及列口径（如本报告期、上年同期、同比增减）；"
        "多个事实可能位于同一表格或章节时合并检索，只有分属不同章节或仍有明确证据缺口时才拆分；"
        "补查前检查已有 query 和命中，只查缺失事实或来源冲突，不重复等价 query。若证据已覆盖所问事实就停止检索。"
        "命中含文件名、页码和原文片段，"
        "由你判断其相关性及是否足以回答。读取结果后可改写 query，也可按证据缺口调用其他相关工具。"
        "success=false 表示检索未成功，不能据此断言知识库没有材料或重新获取已有文件；"
        "只有成功检索后的 no_evidence=true 才表示该查询未命中。"
        "搜索范围会在执行时按当前用户/租户重新校验；目录外、无权限或没有活动索引的目标不会执行。"
    ),
    parameters=object_schema(
        {
            "query": {"type": "string", "minLength": 1, "maxLength": 2_000},
        },
        required=("query",),
    ),
    executor=search_knowledge_base,
    category="research",
    web_fallback=False,
    # Retrieval owns its inference/network error handling. The generic
    # 120-second process deadline can kill a valid queued retrieval before
    # those operations return. The process runner still honors cancellation.
    timeout_seconds=None,
)


__all__ = ["TOOL", "search_knowledge_base"]
