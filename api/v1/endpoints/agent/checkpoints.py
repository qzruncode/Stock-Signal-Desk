"""Read-only LangGraph checkpoint inspection endpoints."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Query, Request

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from src.agent.langgraph_runtime import agent_graph_runtime
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager


logger = logging.getLogger(__name__)


@router.get("/agent/conversations/{conversation_id}/checkpoints")
async def list_agent_checkpoints(
    conversation_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    before_checkpoint_id: str | None = Query(None, min_length=1, max_length=256),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """Inspect graph revisions without changing the conversation or Run."""
    service = ChatSessionService(
        db_manager,
        tenant_id=str(getattr(request.state, "tenant_id", "local")),
        owner_id=str(getattr(request.state, "owner_id", "admin")),
    )
    if not service.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="对话不存在")
    try:
        return await agent_graph_runtime.get_state_history(
            conversation_id,
            limit=limit,
            before_checkpoint_id=before_checkpoint_id,
        )
    except RuntimeError as exc:
        logger.warning(
            "[Agent] LangGraph checkpoint history unavailable conversation_id=%s",
            conversation_id,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="LangGraph checkpoint 暂不可用") from exc


__all__ = ["list_agent_checkpoints"]
