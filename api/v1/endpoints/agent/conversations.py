# -*- coding: utf-8 -*-
"""Agent conversation CRUD endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import Body, Depends, HTTPException, Query

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


@router.get("/agent/conversations")
def list_agent_conversations(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = ChatSessionService(db_manager)
    return service.list_conversations(page=page, limit=limit)


@router.post("/agent/conversations")
def create_agent_conversation(
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = ChatSessionService(db_manager)
    return service.create_conversation()


@router.get("/agent/conversations/{conversation_id}")
def get_agent_conversation(
    conversation_id: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = ChatSessionService(db_manager)
    conversation = service.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conversation


@router.patch("/agent/conversations/{conversation_id}")
def rename_agent_conversation(
    conversation_id: str,
    payload: Dict[str, Any] = Body(...),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title 不能为空")
    service = ChatSessionService(db_manager)
    conversation = service.rename_conversation(conversation_id, title)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conversation


@router.delete("/agent/conversations/{conversation_id}")
def delete_agent_conversation(
    conversation_id: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = ChatSessionService(db_manager)
    deleted = service.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="对话不存在")
    return {"deleted": deleted}


@router.put("/agent/conversations/{conversation_id}/snapshot")
def sync_agent_conversation_snapshot(
    conversation_id: str,
    payload: Dict[str, Any] = Body(...),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = ChatSessionService(db_manager)
    messages = payload.get("messages", [])
    thread_state = payload.get("thread_state")
    conversation = service.save_conversation_snapshot(
        conversation_id,
        messages if isinstance(messages, list) else [],
        thread_state=thread_state if isinstance(thread_state, dict) else None,
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conversation