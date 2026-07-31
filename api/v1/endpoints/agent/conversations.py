# -*- coding: utf-8 -*-
"""Agent conversation CRUD endpoints."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import Body, Depends, HTTPException, Query, Request

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from src.agent.run_registry import active_run_registry
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


def _session_service(
    request: Request,
    db_manager: DatabaseManager,
) -> ChatSessionService:
    return ChatSessionService(
        db_manager,
        tenant_id=str(getattr(request.state, "tenant_id", "local")),
        owner_id=str(getattr(request.state, "owner_id", "admin")),
    )


@router.get("/agent/conversations")
def list_agent_conversations(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = _session_service(request, db_manager)
    return service.list_conversations(page=page, limit=limit)


@router.post("/agent/conversations")
def create_agent_conversation(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = _session_service(request, db_manager)
    return service.create_conversation()


@router.get("/agent/conversations/{conversation_id}")
def get_agent_conversation(
    conversation_id: str,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = _session_service(request, db_manager)
    conversation = service.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    # 附加运行态:后端是否仍在生成该对话的回复(供前端刷新后判断是否续流)。
    active_run_registry.configure(db_manager)
    run = active_run_registry.get(conversation_id)
    durable_run = db_manager.get_agent_run(conversation_id=conversation_id)
    durable_status = durable_run.get("status") if durable_run else None
    durable_event_cursor = int(durable_run.get("event_cursor") or 0) if durable_run else 0
    durable_has_tool_events = (
        db_manager.agent_run_has_tool_events(str(durable_run.get("run_id") or ""))
        if durable_run and durable_event_cursor
        else False
    )
    is_generating = active_run_registry.is_active(conversation_id) or durable_status in {
        "queued",
        "running",
        "recovering",
    }
    trace = db_manager.get_latest_agent_run_trace(conversation_id)
    persisted_stage = (
        trace.get("latest_stage")
        if isinstance(trace, dict)
        and (
            (run is None and durable_run is None)
            or trace.get("run_id") == (run.run_id if run else durable_run.get("run_id"))
        )
        else None
    )
    reconciled_trace_status = trace.get("status") if isinstance(trace, dict) else None
    if (
        run is None
        and not is_generating
        and isinstance(trace, dict)
        and reconciled_trace_status in {"running", "compiled"}
    ):
        reconciled_trace_status = "failed"
        persisted_stage = {
            "event": "agent_stage_v2",
            "run_id": trace.get("run_id"),
            "stage": "completed",
            "status": "failed",
            "error_code": "tool_failed",
            "summary": "后台运行已经中断，没有仍在执行的任务",
        }
    conversation["is_generating"] = is_generating
    conversation["resume_state"] = {
        "run_id": (run.run_id if run else (durable_run.get("run_id") if durable_run else None)),
        "active": (
            is_generating or (run is not None and run.broadcaster.history_length > 0) or durable_event_cursor > 0
        ),
        "is_generating": is_generating,
        "status": (run.status if run else (durable_status or reconciled_trace_status)),
        # The durable event log is the authoritative presentation state.
        # Replaying from zero reconstructs tool cards even if the browser died
        # before its onFinish snapshot.
        "after_chunk_index": (run.broadcaster.history_length if run and not durable_run else 0),
        "event_cursor": (run.broadcaster.history_length if run else durable_event_cursor),
        "assistant_text": (
            run.broadcaster.assistant_text_snapshot
            if run
            else str(durable_run.get("final_text") or "") if durable_run else ""
        ),
        "has_tool_events": (run.broadcaster.has_tool_events if run else durable_has_tool_events),
        "latest_stage": persisted_stage,
    }
    return conversation


@router.patch("/agent/conversations/{conversation_id}")
def rename_agent_conversation(
    conversation_id: str,
    request: Request,
    payload: Dict[str, Any] = Body(...),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title 不能为空")
    service = _session_service(request, db_manager)
    conversation = service.rename_conversation(conversation_id, title)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conversation


@router.delete("/agent/conversations/{conversation_id}")
async def delete_agent_conversation(
    conversation_id: str,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = _session_service(request, db_manager)
    # Resolve the target before mutating run state.  A typo/non-existent id
    # must be a clean 404 and must never cancel an unrelated registry entry.
    if not service.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="对话不存在")

    # 必须先取消活跃 run 再删 DB:cancel 会 task.cancel() 唤醒后台 task 的
    # CancelledError 分支,该分支会 save_partial_assistant_text 落库。若先删
    # 会话记录,后写的 partial 会挂到已不存在的 conversation_id 上成为孤儿
    # 消息(FK 缺失时残留脏数据)。先 cancel 让 task 收尾、再删 DB。
    local_run = active_run_registry.get(conversation_id)
    cancelled = await active_run_registry.cancel(conversation_id)
    if cancelled:
        logger.info("[Agent] cancelled active run for deleted conversation %s", conversation_id)
    if cancelled and local_run is None:
        # A different worker owns the task. Its one-second durable cancel
        # watcher must publish the terminal state before the parent
        # conversation is removed, otherwise its partial/trace writes would
        # target a deleted row.
        deadline = asyncio.get_running_loop().time() + 10.0
        while asyncio.get_running_loop().time() < deadline:
            durable = await asyncio.to_thread(
                db_manager.get_agent_run,
                conversation_id=conversation_id,
            )
            if durable is None or durable.get("status") not in {
                "queued",
                "running",
                "recovering",
            }:
                break
            await asyncio.sleep(0.1)
        else:
            raise HTTPException(
                status_code=409,
                detail="任务正在其他节点停止，请稍后重试删除",
            )

    deleted = service.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="对话不存在")
    return {"deleted": deleted}


@router.post("/agent/conversations/{conversation_id}/cancel")
async def cancel_agent_conversation_run(
    conversation_id: str,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """Stop the backend Agent run and preserve the generated partial answer."""
    service = _session_service(request, db_manager)
    active_run_registry.configure(db_manager)
    if not service.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="对话不存在")
    cancelled = await active_run_registry.cancel(conversation_id, remove=False)
    return {"cancelled": cancelled}


@router.put("/agent/conversations/{conversation_id}/snapshot")
def sync_agent_conversation_snapshot(
    conversation_id: str,
    request: Request,
    payload: Dict[str, Any] = Body(...),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = _session_service(request, db_manager)
    raw_messages = payload.get("messages") if "messages" in payload else None
    messages = raw_messages if isinstance(raw_messages, list) else ([] if raw_messages is not None else None)
    thread_state = payload.get("thread_state")
    conversation = service.save_conversation_snapshot(
        conversation_id,
        messages,
        thread_state=thread_state if isinstance(thread_state, dict) else None,
        prune_agent_context_to_messages=bool(payload.get("prune_agent_context_to_messages")),
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conversation
