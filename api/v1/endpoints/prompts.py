# -*- coding: utf-8 -*-
"""Prompt template CRUD API."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.prompt_templates import get_prompt_template_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["prompts"])


class PromptTemplateItem(BaseModel):
    id: str
    name: str
    content: str
    is_default: bool
    created_at: str
    updated_at: str


class PromptTemplateListResponse(BaseModel):
    templates: list[PromptTemplateItem]


class CreatePromptTemplateRequest(BaseModel):
    name: str = Field(..., description="模板名称")
    content: str = Field(..., description="System Prompt 内容")
    is_default: bool = Field(False, description="是否设为默认模板")


class UpdatePromptTemplateRequest(BaseModel):
    name: Optional[str] = Field(None, description="模板名称")
    content: Optional[str] = Field(None, description="System Prompt 内容")
    is_default: Optional[bool] = Field(None, description="是否设为默认模板")


@router.get("", response_model=PromptTemplateListResponse)
async def list_prompt_templates():
    store = get_prompt_template_store()
    templates = store.load_all()
    return PromptTemplateListResponse(templates=[PromptTemplateItem(**t) for t in templates])


@router.get("/{template_id}", response_model=PromptTemplateItem)
async def get_prompt_template(template_id: str):
    store = get_prompt_template_store()
    template = store.get(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return PromptTemplateItem(**template)


@router.post("", response_model=PromptTemplateItem, status_code=201)
async def create_prompt_template(request: CreatePromptTemplateRequest):
    store = get_prompt_template_store()
    template = store.create(
        name=request.name,
        content=request.content,
        is_default=request.is_default,
    )
    return PromptTemplateItem(**template)


@router.put("/{template_id}", response_model=PromptTemplateItem)
async def update_prompt_template(template_id: str, request: UpdatePromptTemplateRequest):
    store = get_prompt_template_store()
    kwargs = {}
    if request.name is not None:
        kwargs["name"] = request.name
    if request.content is not None:
        kwargs["content"] = request.content
    if request.is_default is not None:
        kwargs["is_default"] = request.is_default
    template = store.update(template_id, **kwargs)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return PromptTemplateItem(**template)


@router.delete("/{template_id}", status_code=204)
async def delete_prompt_template(template_id: str):
    store = get_prompt_template_store()
    if not store.delete(template_id):
        raise HTTPException(status_code=404, detail="模板不存在")
