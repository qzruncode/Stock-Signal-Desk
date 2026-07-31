# -*- coding: utf-8 -*-
"""Resume/attachment route implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from assistant_stream.serialization.data_stream import DataStreamResponse
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.agent.run_registry import active_run_registry
from src.agent.run_streaming import durable_subscriber_stream, subscriber_stream
from src.agent.runtime_safety import AgentRequestValidationError, validate_chat_request_body
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

async def agent_chat_resume_impl(
    request: Request,
    db_manager: DatabaseManager,
):
    """续流端点:attach 到进行中的 run;无活跃 run 返回 {active: false}。"""
    try:
        body = await request.json()
        body_for_validation = dict(body) if isinstance(body, dict) else body
        if isinstance(body_for_validation, dict):
            body_for_validation["resume_existing"] = True
        _, conversation_id, _ = validate_chat_request_body(body_for_validation)
    except AgentRequestValidationError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": str(exc)},
        )
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": "请求体不是有效 JSON"},
        )
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required")
    active_run_registry.configure(db_manager)
    session_service = ChatSessionService(
        db_manager,
        tenant_id=str(getattr(request.state, "tenant_id", "local")),
        owner_id=str(getattr(request.state, "owner_id", "admin")),
    )
    if not session_service.get_conversation(conversation_id):
        # This endpoint is an idempotent attachment probe.  A deleted or stale
        # conversation has no resumable run, which is equivalent to inactive.
        return JSONResponse(status_code=200, content={"active": False})
    run = active_run_registry.get(conversation_id)
    durable_run = await asyncio.to_thread(
        db_manager.get_agent_run,
        conversation_id=conversation_id,
    )
    if run is None or not run.is_running:
        if durable_run is None or durable_run.get("status") not in {
            "queued",
            "running",
            "recovering",
        }:
            return JSONResponse(status_code=200, content={"active": False})
        try:
            replay_from = max(0, int(body.get("after_chunk_index") or 0))
        except (TypeError, ValueError):
            replay_from = 0
        return DataStreamResponse(
            durable_subscriber_stream(
                db_manager,
                durable_run,
                replay_from=replay_from,
            )
        )
    try:
        replay_from = max(0, int(body.get("after_chunk_index") or 0))
    except (TypeError, ValueError):
        replay_from = 0
    if durable_run is not None:
        return DataStreamResponse(
            durable_subscriber_stream(
                db_manager,
                durable_run,
                replay_from=replay_from,
            )
        )
    return DataStreamResponse(subscriber_stream(run, replay_from=replay_from))


__all__ = ["agent_chat_resume_impl"]
