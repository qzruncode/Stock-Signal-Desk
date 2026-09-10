"""Shared admission boundary for conversation execution and history changes."""

from contextlib import asynccontextmanager

from fastapi import HTTPException

from src.agent.resource_scheduler import ResourceCapacityExceeded, agent_conversation_lease


@asynccontextmanager
async def conversation_transition(database, conversation_id: str):
    """Reject overlapping transitions across workers using the existing lease API."""
    try:
        async with agent_conversation_lease(database, conversation_id):
            yield
    except ResourceCapacityExceeded as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "conversation_transition_in_progress",
                "message": "对话正在切换任务或更新历史，请稍后重试",
            },
        ) from exc
