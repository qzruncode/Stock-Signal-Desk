# -*- coding: utf-8 -*-
"""Agent system prompt 模板 CRUD 端点。

管理 AI 助手（/api/v1/agent/chat）的 system prompt 模板，
支持多模板、切换生效、空值回落源码默认。
"""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.schemas.agent_prompts import (
    ActiveAgentPromptResponse,
    CreateAgentPromptRequest,
    UpdateAgentPromptRequest,
)
from src.services.agent_prompt_service import AgentPromptService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


@router.get("/agent/prompts")
def list_agent_prompts(
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """列出全部 prompt 模板，生效的排最前。"""
    service = AgentPromptService(db_manager)
    templates = service.list_templates()
    return {"templates": templates}


# 注意：/active 必须在 /{template_id} 之前注册，否则 'active' 会被当路径参数匹配。
@router.get("/agent/prompts/active")
def get_active_agent_prompt(
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """返回当前生效的 system prompt 文本（含是否回退标记）。"""
    service = AgentPromptService(db_manager)
    content, is_fallback = service.get_active_system_prompt()
    template_id = None
    template_name = None
    if not is_fallback:
        record = service.db.get_active_agent_prompt()
        if record:
            template_id = record.id
            template_name = record.name
    return ActiveAgentPromptResponse(
        content=content,
        is_fallback=is_fallback,
        template_id=template_id,
        template_name=template_name,
    ).model_dump()


@router.post("/agent/prompts")
def create_agent_prompt(
    payload: CreateAgentPromptRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """新建一个 prompt 模板。"""
    service = AgentPromptService(db_manager)
    return service.create_template(
        name=payload.name,
        content=payload.content,
        is_active=payload.is_active,
    )


@router.put("/agent/prompts/{template_id}")
def update_agent_prompt(
    template_id: int,
    payload: UpdateAgentPromptRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """更新模板名称或内容。"""
    service = AgentPromptService(db_manager)
    result = service.update_template(
        template_id,
        name=payload.name,
        content=payload.content,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return result


@router.delete("/agent/prompts/{template_id}")
def delete_agent_prompt(
    template_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """删除模板。删除当前生效模板后自动回落源码默认。"""
    service = AgentPromptService(db_manager)
    deleted = service.delete_template(template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"deleted": deleted}


@router.post("/agent/prompts/{template_id}/activate")
def activate_agent_prompt(
    template_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """将指定模板设为生效（同时把其余模板置为非生效）。"""
    service = AgentPromptService(db_manager)
    result = service.set_active(template_id)
    if result is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return result
