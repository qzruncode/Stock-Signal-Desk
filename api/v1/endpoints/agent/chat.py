"""Assistant-stream routes backed exclusively by the LangGraph runtime."""

from __future__ import annotations

from fastapi import Depends, Request

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.endpoints.agent.chat_background_runner import _execute_background_agent_run
from api.v1.endpoints.agent.chat_recovery import recover_interrupted_agent_runs as _recover_runs
from api.v1.endpoints.agent.chat_route_resume import agent_chat_resume_impl
from api.v1.endpoints.agent.chat_route_start import agent_chat_impl
from api.v1.endpoints.agent.tools import _compact_tool_result
from src.agent.langgraph_runtime import agent_graph_runtime
from src.llm.anthropic_gateway import resolve_anthropic_gateway_config
from src.storage import DatabaseManager


_get_llm_config = resolve_anthropic_gateway_config
_registry = agent_graph_runtime.registry
agent_graph_runtime.configure_tool_projection(
    compact_result=_compact_tool_result,
)


async def recover_interrupted_agent_runs(
    db_manager: DatabaseManager,
    *,
    limit: int = 20,
) -> int:
    return await _recover_runs(
        db_manager,
        limit=limit,
        config_loader=_get_llm_config,
        background_runner=_execute_background_agent_run,
    )


@router.post("/agent/chat")
async def agent_chat(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    return await agent_chat_impl(
        request,
        db_manager,
        background_runner=_execute_background_agent_run,
        config_loader=_get_llm_config,
    )


@router.post("/agent/chat/resume")
async def agent_chat_resume(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    return await agent_chat_resume_impl(request, db_manager)


__all__ = [
    "_execute_background_agent_run",
    "_registry",
    "agent_chat",
    "agent_chat_resume",
    "recover_interrupted_agent_runs",
]
