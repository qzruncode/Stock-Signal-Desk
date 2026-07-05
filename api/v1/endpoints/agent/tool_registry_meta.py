# -*- coding: utf-8 -*-
"""Tool registry metadata endpoint.

只读反射 src.agent.tool_registry.ToolRegistry，供前端 /setting 页展示当前接入
LLM 模型的全部工具。不修改 ToolRegistry 自身的注册逻辑。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from api.v1.endpoints.agent import router
from api.v1.schemas.tools_meta import (
    ToolCategory,
    ToolMeta,
    ToolParameterSpec,
    ToolRegistryResponse,
)
from src.agent.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


_DESCRIPTION_MAX_LEN = 500
_VALID_CATEGORIES = {"data", "market", "financials", "sentiment", "macro", "search", "analysis"}

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
        parameters=_flatten_parameters(tool_def.parameters or {}),
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