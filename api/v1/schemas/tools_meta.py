# -*- coding: utf-8 -*-
"""Tool registry metadata API schemas.

只读反射 src.agent.tool_registry.ToolRegistry 中的 ToolDef，供前端 /setting 页展示。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


ToolCategory = Literal[
    "data",
    "market",
    "financials",
    "sentiment",
    "macro",
    "search",
    "analysis",
]


class ToolParameterSpec(BaseModel):
    """单个工具参数的扁平化展示项。"""

    name: str = Field(..., description="参数名")
    type: str = Field(..., description="JSON Schema 简化类型")
    description: Optional[str] = Field(None, description="参数描述")
    required: bool = Field(False, description="是否必填")
    enum: Optional[List[Any]] = Field(None, description="可选值（仅当 JSON Schema 含 enum 时）")
    default: Optional[Any] = Field(None, description="默认值（仅当 JSON Schema 含 default 时）")


class ToolMeta(BaseModel):
    """单个工具的元数据。"""

    name: str = Field(..., description="工具名（OpenAI function name）")
    category: ToolCategory = Field(..., description="工具分类")
    description: str = Field(..., description="工具描述（原文，超过 500 字截断）")
    parameters: List[ToolParameterSpec] = Field(default_factory=list, description="参数列表")


class ToolRegistryResponse(BaseModel):
    """GET /api/v1/agent/tool-registry 响应。"""

    total: int = Field(..., description="工具总数")
    categories: Dict[str, int] = Field(default_factory=dict, description="各 category 的工具计数")
    tools: List[ToolMeta] = Field(default_factory=list, description="工具列表")