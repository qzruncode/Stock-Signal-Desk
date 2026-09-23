"""Resume LangGraph interrupts after an authenticated user decision."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.endpoints.agent.conversation_lifecycle import conversation_transition
from api.v1.endpoints.agent.chat_background_runner import _execute_background_agent_run
from src.agent.langgraph_runtime import agent_graph_runtime
from src.agent.run_registry import ActiveRun, RunBroadcaster, active_run_registry
from src.llm.anthropic_gateway import AnthropicGatewayConfigError, resolve_anthropic_gateway_config
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager


class InterruptDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=64)
    fingerprint: str = Field(min_length=32, max_length=128)
    decision: Literal["approve", "reject", "modify"]
    message: str | None = Field(default=None, max_length=8_000)


@router.post(
    "/agent/conversations/{conversation_id}/interrupts/{interrupt_id}/decision",
    status_code=202,
)
async def decide_agent_interrupt(
    conversation_id: str,
    interrupt_id: str,
    payload: InterruptDecisionRequest,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id = str(getattr(request.state, "tenant_id", "local"))
    owner_id = str(getattr(request.state, "owner_id", "admin"))
    service = ChatSessionService(db_manager, tenant_id=tenant_id, owner_id=owner_id)
    async with conversation_transition(db_manager, conversation_id):
        if not service.get_conversation(conversation_id):
            raise HTTPException(status_code=404, detail="对话不存在")

        pending = await agent_graph_runtime.pending_interrupt(conversation_id)
        if (
            pending is None
            or str(pending.get("interrupt_id") or "") != interrupt_id
            or str(pending.get("run_id") or "") != payload.run_id
            or str(pending.get("fingerprint") or "") != payload.fingerprint
        ):
            raise HTTPException(status_code=409, detail="审批已过期、已处理或指纹不匹配")
        if payload.decision == "modify" and not str(payload.message or "").strip():
            raise HTTPException(status_code=422, detail="修改 Goal 时必须提供新的目标内容")

        # The approval event is committed immediately before the durable run is
        # parked. Accommodate that tiny publication/transition window without
        # weakening the atomic status check used for duplicate decisions.
        durable = None
        for _ in range(20):
            durable = await asyncio.to_thread(db_manager.get_agent_run, run_id=payload.run_id)
            if durable is None or durable.get("status") != "running":
                break
            await asyncio.sleep(0.05)
        if (
            durable is None
            or durable.get("conversation_id") != conversation_id
            or durable.get("status") != "interrupted"
        ):
            raise HTTPException(status_code=409, detail="审批已过期或运行不再等待决定")

        active_run_registry.configure(db_manager)
        for _ in range(40):
            if active_run_registry.get(conversation_id) is None:
                break
            await asyncio.sleep(0.05)
        if active_run_registry.get(conversation_id) is not None:
            raise HTTPException(status_code=409, detail="审批运行正在切换状态，请稍后重试")

        run_request = durable.get("request") or {}
        messages = run_request.get("messages")
        body = run_request.get("body")
        if not isinstance(messages, list) or not isinstance(body, dict):
            raise HTTPException(status_code=409, detail="原运行上下文不可恢复")
        try:
            llm_cfg = resolve_anthropic_gateway_config()
        except AnthropicGatewayConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        resumed = await asyncio.to_thread(
            db_manager.resume_interrupted_agent_run,
            payload.run_id,
            worker_id=active_run_registry.worker_id,
        )
        if resumed is None:
            raise HTTPException(status_code=409, detail="审批已被处理或运行状态已变化")

        run = await active_run_registry.adopt_recovered(
            conversation_id=conversation_id,
            run_id=payload.run_id,
            event_cursor=int(resumed.get("event_cursor") or 0),
            attempt=int(resumed.get("attempt") or 1),
        )

        async def factory(
            broadcaster: RunBroadcaster,
            *,
            _run: ActiveRun = run,
        ) -> asyncio.Task:
            return asyncio.create_task(
                _execute_background_agent_run(
                    controller=broadcaster,
                    run=_run,
                    messages=[dict(item) for item in messages],
                    body=dict(body),
                    llm_cfg=llm_cfg,
                    conversation_id=conversation_id,
                    db_manager=db_manager,
                    session_service=service,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    resume_decision={
                    "interrupt_id": interrupt_id,
                    "fingerprint": payload.fingerprint,
                    "decision": payload.decision,
                    "message": payload.message or "",
                },
                )
            )

        try:
            await run.start(factory)
        except Exception as exc:
            await asyncio.to_thread(
                db_manager.release_agent_run_lease,
                payload.run_id,
                worker_id=active_run_registry.worker_id,
            )
            raise HTTPException(status_code=500, detail="恢复审批运行失败") from exc
        return JSONResponse(
            status_code=202,
            content={
                "accepted": True,
                "run_id": payload.run_id,
                "conversation_id": conversation_id,
                "decision": payload.decision,
            },
        )


__all__ = ["InterruptDecisionRequest", "decide_agent_interrupt"]
