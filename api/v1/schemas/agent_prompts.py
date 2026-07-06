# -*- coding: utf-8 -*-
"""Agent system prompt 模板 API schemas。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AgentPromptTemplateItem(BaseModel):
    """单个 prompt 模板。"""

    id: int = Field(..., description="模板 ID")
    name: str = Field(..., description="模板名称")
    content: str = Field(..., description="prompt 正文")
    is_active: bool = Field(False, description="是否当前生效")
    created_at: Optional[str] = Field(None, description="创建时间 ISO")
    updated_at: Optional[str] = Field(None, description="更新时间 ISO")


class AgentPromptListResponse(BaseModel):
    """GET /api/v1/agent/prompts 响应。"""

    templates: List[AgentPromptTemplateItem] = Field(default_factory=list, description="模板列表")


class CreateAgentPromptRequest(BaseModel):
    """POST /api/v1/agent/prompts 请求体。"""

    name: str = Field(..., min_length=1, max_length=120, description="模板名称")
    content: str = Field("", description="prompt 正文")
    is_active: bool = Field(False, description="是否新建即生效")


class UpdateAgentPromptRequest(BaseModel):
    """PUT /api/v1/agent/prompts/{id} 请求体。"""

    name: Optional[str] = Field(None, min_length=1, max_length=120, description="模板名称")
    content: Optional[str] = Field(None, description="prompt 正文")


class ActiveAgentPromptResponse(BaseModel):
    """GET /api/v1/agent/prompts/active 响应。"""

    content: str = Field(..., description="当前生效的 system prompt 文本")
    is_fallback: bool = Field(..., description="是否为回落到源码默认（DB 无生效模板或内容为空）")
    template_id: Optional[int] = Field(None, description="生效模板 ID（回退时为 null）")
    template_name: Optional[str] = Field(None, description="生效模板名称（回退时为 null）")
