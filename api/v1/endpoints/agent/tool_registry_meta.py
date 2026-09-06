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
    "source_read",
    "source_catalog",
    "source_search",
    "news_source",
    "deterministic_calculation",
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


def _parameter_shape(
    prop: Dict[str, Any],
    definitions: Dict[str, Any],
) -> Dict[str, Any]:
    """Unwrap Pydantic's nullable ``anyOf`` for the compact metadata view."""
    if prop.get("type") is not None:
        return prop
    choices = prop.get("anyOf") or prop.get("oneOf") or []
    non_null = [
        item
        for item in choices
        if isinstance(item, dict) and item.get("type") != "null"
    ]
    shape = {**prop, **non_null[0]} if len(non_null) == 1 else prop
    ref = str(shape.get("$ref") or "")
    prefix = "#/$defs/"
    if ref.startswith(prefix):
        resolved = definitions.get(ref[len(prefix) :])
        if isinstance(resolved, dict):
            shape = {**shape, **resolved}
    return shape


def _flatten_parameters(parameters: Dict[str, Any]) -> List[ToolParameterSpec]:
    if not isinstance(parameters, dict):
        return []
    properties = parameters.get("properties") or {}
    required_set = set(parameters.get("required") or [])
    definitions = parameters.get("$defs") or {}
    if not isinstance(properties, dict):
        return []
    if not isinstance(definitions, dict):
        definitions = {}

    specs: List[ToolParameterSpec] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        shape = _parameter_shape(prop, definitions)
        specs.append(
            ToolParameterSpec(
                name=name,
                type=_simplify_param_type(shape.get("type")),
                description=shape.get("description"),
                required=name in required_set,
                enum=shape.get("enum") if isinstance(shape.get("enum"), list) else None,
                default=shape.get("default"),
            )
        )
    return specs


def _build_tool_meta(tool_def: Any) -> ToolMeta:
    category = tool_def.category if tool_def.category in _VALID_CATEGORIES else "data"
    result_schema = (
        tool_def.result_model.model_json_schema()
        if tool_def.result_model is not None
        else None
    )
    return ToolMeta(
        name=tool_def.name,
        category=category,  # type: ignore[arg-type]
        description=_truncate_description(tool_def.description or ""),
        # Compatibility field retained for the settings API. The runtime no
        # longer has a separate retrieval index; every model sees the same
        # complete compact description catalog.
        retrieval_description=_truncate_description(tool_def.description or ""),
        effect=tool_def.effect,
        effect_mode=tool_def.effect_mode,
        approval_policy=tool_def.approval_policy,
        timeout_seconds=tool_def.timeout_seconds,
        max_attempts=tool_def.max_attempts,
        retry_backoff_seconds=tool_def.retry_backoff_seconds,
        idempotent=tool_def.idempotent,
        sensitive_fields=list(tool_def.sensitive_fields),
        parameters=_flatten_parameters(tool_def.model_parameters()),
        typed=bool(
            tool_def.args_model is not None
            and result_schema is not None
            and result_schema.get("additionalProperties") is False
        ),
        args_schema=tool_def.model_parameters(),
        result_schema=result_schema,
        source_catalog=[dict(item) for item in tool_def.source_catalog],
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
