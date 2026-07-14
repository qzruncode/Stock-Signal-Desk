# -*- coding: utf-8 -*-
"""Tool registry metadata API schemas.

只读反射 src.tools.registry.ToolRegistry 中的 ToolDef，供前端 /setting 页展示。
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


class ToolExecuteRequest(BaseModel):
    """POST /api/v1/agent/tool-registry/execute 请求。"""

    tool_name: str = Field(..., description="工具名(OpenAI function name)")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="工具参数")


class ToolExecuteResponse(BaseModel):
    """POST /api/v1/agent/tool-registry/execute 响应。

    success=False 时 result 为 None、error 填可读错误信息;
    success=True 时 result 为压缩 + 联网兜底后的 payload(与真实 agent 调用看到的相同)。
    """

    tool_name: str = Field(..., description="工具名")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="实际执行用的参数")
    success: bool = Field(..., description="是否执行成功")
    result: Optional[Any] = Field(None, description="压缩 + 兜底后的结果 payload")
    error: Optional[str] = Field(None, description="失败时的可读错误信息")
    duration_ms: int = Field(..., description="服务端执行耗时(毫秒)")
