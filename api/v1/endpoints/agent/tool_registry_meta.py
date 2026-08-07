# -*- coding: utf-8 -*-
"""Read-only metadata for the atomic tool registry.

只读反射 src.tools.registry.ToolRegistry，供前端 /setting 页展示当前接入
LLM 模型的全部工具(GET /agent/tool-registry)。工具执行只允许经 LangGraph
策略与审批节点进入，不提供可绕过审批的调试执行端点。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from api.v1.endpoints.agent import router
from api.v1.schemas.tools_meta import (
    ToolCategory,
    ToolMeta,
    ToolParameterSpec,
    ToolRegistryResponse,
)
from src.tools.registry import ToolRegistry


_DESCRIPTION_MAX_LEN = 500
_VALID_CATEGORIES = {
    "data",
    "market",
    "financials",
    "sentiment",
    "macro",
    "search",
    "analysis",
    "research",
    "regulatory",
    "events",
    "risk",
    "action",
}
_registry = ToolRegistry()


def _truncate_description(text: str, limit: int = _DESCRIPTION_MAX_LEN) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _simplify_param_type(raw_type: Any) -> str:
    if isinstance(raw_type, list):
        for candidate in raw_type:
            if candidate != "null":
                return str(candidate)
        return "null"
    return str(raw_type) if raw_type is not None else "string"


def _flatten_parameters(parameters: Dict[str, Any]) -> List[ToolParameterSpec]:
    if not isinstance(parameters, dict):
        return []
    properties = parameters.get("properties") or {}
    required_set = set(parameters.get("required") or [])
    if not isinstance(properties, dict):
        return []

    specs: List[ToolParameterSpec] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        specs.append(
            ToolParameterSpec(
                name=name,
                type=_simplify_param_type(prop.get("type")),
                description=prop.get("description"),
                required=name in required_set,
                enum=prop.get("enum") if isinstance(prop.get("enum"), list) else None,
                default=prop.get("default"),
            )
        )
    return specs


def _build_tool_meta(tool_def: Any) -> ToolMeta:
    category = tool_def.category if tool_def.category in _VALID_CATEGORIES else "data"
    return ToolMeta(
        name=tool_def.name,
        category=category,  # type: ignore[arg-type]
        description=_truncate_description(tool_def.description or ""),
        retrieval_description=_truncate_description(tool_def.retrieval_text or tool_def.description or ""),
        effect=tool_def.effect,
        effect_mode=tool_def.effect_mode,
        approval_policy=tool_def.approval_policy,
        timeout_seconds=tool_def.timeout_seconds,
        max_attempts=tool_def.max_attempts,
        retry_backoff_seconds=tool_def.retry_backoff_seconds,
        idempotent=tool_def.idempotent,
        sensitive_fields=list(tool_def.sensitive_fields),
        parameters=_flatten_parameters(tool_def.model_parameters()),
        typed=(tool_def.args_model is not None and tool_def.result_model is not None),
        args_schema=tool_def.model_parameters(),
        result_schema=(tool_def.result_model.model_json_schema() if tool_def.result_model is not None else None),
    )


@router.get("/agent/tool-registry", response_model=ToolRegistryResponse)
def list_tool_registry(
    category: Optional[ToolCategory] = None,  # type: ignore[valid-type]
) -> ToolRegistryResponse:
    """返回当前 LLM 模型接入的全部工具元数据（只读）。"""
    tool_defs = list(_registry._tools.values())

    categories: Dict[str, int] = {}
    for td in tool_defs:
        cat = td.category if td.category in _VALID_CATEGORIES else "data"
        categories[cat] = categories.get(cat, 0) + 1

    tools: List[ToolMeta] = []
    for td in tool_defs:
        meta = _build_tool_meta(td)
        if category is not None and meta.category != category:
            continue
        tools.append(meta)

    return ToolRegistryResponse(
        total=len(tool_defs),
        categories=categories,
        tools=tools,
    )
