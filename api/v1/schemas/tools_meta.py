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
    "research",
    "regulatory",
    "events",
    "risk",
    "action",
    "source_read",
    "source_catalog",
    "source_search",
    "deterministic_calculation",
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
    retrieval_description: str = Field(
        ...,
        description="完整模型工具目录中使用的说明；字段名仅为接口兼容保留",
    )
    effect: Literal["read", "side_effect"] = Field(..., description="调用是否产生副作用")
    effect_mode: Literal["fixed", "argument_dependent"] = Field(
        ...,
        description="副作用是否由调用参数动态判定",
    )
    approval_policy: Literal["required_for_side_effect"] = Field(..., description="审批策略")
    timeout_seconds: Optional[float] = Field(None, description="单次调用超时")
    max_attempts: int = Field(..., ge=1, description="最大尝试次数；副作用运行时强制为一次")
    retry_backoff_seconds: float = Field(..., ge=0, description="只读调用的重试退避")
    idempotent: bool = Field(..., description="是否支持幂等重放")
    sensitive_fields: List[str] = Field(default_factory=list, description="审批卡片需要脱敏的字段")
    parameters: List[ToolParameterSpec] = Field(default_factory=list, description="参数列表")
    typed: bool = Field(False, description="参数和结果是否都由同源运行时模型校验")
    args_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="运行时参数模型生成的完整 JSON Schema",
    )
    result_schema: Optional[Dict[str, Any]] = Field(
        None,
        description="运行时结果模型生成的完整 JSON Schema",
    )
    source_catalog: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="该通用操作可选择的完整数据源目录；source_id 只能取其中 id",
    )


class ToolRegistryResponse(BaseModel):
    """GET /api/v1/agent/tool-registry 响应。"""

    total: int = Field(..., description="工具总数")
    categories: Dict[str, int] = Field(default_factory=dict, description="各 category 的工具计数")
    tools: List[ToolMeta] = Field(default_factory=list, description="工具列表")
