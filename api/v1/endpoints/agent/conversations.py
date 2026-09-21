# -*- coding: utf-8 -*-
"""Agent conversation CRUD endpoints."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Mapping

from fastapi import Body, Depends, HTTPException, Query, Request

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.endpoints.agent.conversation_lifecycle import conversation_transition
from src.agent.run_registry import active_run_registry
from src.agent.langgraph_runtime import agent_graph_runtime
from src.agent.langgraph_runtime.answer_contract import project_structured_answer
from src.agent.langgraph_runtime.events import project_stage_history_for_client
from src.agent.langgraph_runtime.evidence_identity import prepare_answer_for_client
from src.tools.base import evidence_record_is_eligible
from src.agent.langgraph_runtime.presentation import enrich_execution_trace_with_result_previews
from src.agent.runtime_safety import AgentRequestValidationError, validate_conversation_snapshot_body
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


def _client_evidence_is_resolvable(item: Mapping[str, Any]) -> bool:
    """Allow only evidence records that satisfy the current result contract."""
    return evidence_record_is_eligible(item)


def _stage_history_from_events(
    db_manager: DatabaseManager,
    run_id: str,
) -> list[dict[str, Any]]:
    """Recover stage-only progress when a run has not reached terminal trace commit."""
    if not run_id:
        return []
    try:
        events = db_manager.list_agent_run_events(
            run_id,
            after_sequence=0,
            limit=10_000,
        )
    except Exception:
        logger.warning("[Agent] unable to read stage history run_id=%s", run_id, exc_info=True)
        return []
    stages: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("event_type") or "") != "data":
            continue
        payload = event.get("payload")
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if isinstance(data, Mapping) and data.get("event") == "agent_stage":
            stages.append(dict(data))
    return stages


def _execution_trace_for_run(
    *,
    db_manager: DatabaseManager,
    trace: Mapping[str, Any] | None,
    run: Any | None,
    durable_run: Mapping[str, Any] | None,
    quality_projection: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    run_id = str(run.run_id if run is not None else (durable_run or {}).get("run_id") or "")
    if not run_id:
        return None
    if not isinstance(trace, Mapping) or str(trace.get("run_id") or "") != run_id:
        persisted: dict[str, Any] = {}
    else:
        candidate = trace.get("execution_trace")
        persisted = dict(candidate) if isinstance(candidate, Mapping) else {}

    # A recovered run's broadcaster starts at the durable event cursor and
    # therefore contains only new chunks. Read the ordered event log as the
    # base, then merge the local committed suffix for the tiny publication
    # window before the next database read.
    stages = _stage_history_from_events(db_manager, run_id)
    if run is not None:
        snapshot = getattr(run.broadcaster, "stage_history_snapshot", None)
        local_stages = snapshot() if callable(snapshot) else []
        seen = {
            "|".join(
                str(item.get(key) or "")
                for key in ("run_id", "stage", "status", "action_id", "occurred_at", "summary")
            )
            for item in stages
        }
        for item in local_stages:
            identity = "|".join(
                str(item.get(key) or "")
                for key in ("run_id", "stage", "status", "action_id", "occurred_at", "summary")
            )
            if identity not in seen:
                stages.append(dict(item))
                seen.add(identity)
    if stages:
        persisted["stages"] = project_stage_history_for_client(stages)
    if isinstance(quality_projection, Mapping):
        projection_evidence = (
            quality_projection.get("evidence")
            if isinstance(quality_projection.get("evidence"), list)
            else ()
        )
        projection_tool_results = (
            quality_projection.get("tool_results")
            if isinstance(quality_projection.get("tool_results"), list)
            else ()
        )
        persisted = enrich_execution_trace_with_result_previews(
            persisted,
            tool_results=projection_tool_results,
            evidence=(
                quality_projection.get("evidence")
                if isinstance(quality_projection.get("evidence"), list)
                else ()
            ),
        )
    else:
        projection_evidence = (
            persisted.get("evidence")
            if isinstance(persisted.get("evidence"), list)
            else ()
        )
        projection_tool_results = (
            persisted.get("tool_results")
            if isinstance(persisted.get("tool_results"), list)
            else ()
        )
    structured_answer_candidate = (
        quality_projection.get("structured_answer")
        if isinstance(quality_projection, Mapping)
        and isinstance(quality_projection.get("structured_answer"), Mapping)
        else persisted.get("structured_answer")
    )
    if isinstance(structured_answer_candidate, Mapping):
        safe_structured_answer = project_structured_answer(
            structured_answer_candidate,
            projection_evidence,
            projection_tool_results,
        )
        if safe_structured_answer:
            persisted["structured_answer"] = safe_structured_answer
        else:
            persisted.pop("structured_answer", None)
    if isinstance(persisted.get("evidence"), list):
        persisted["evidence"] = [
            item
            for item in persisted["evidence"]
            if isinstance(item, Mapping) and _client_evidence_is_resolvable(item)
        ]
    return persisted or None


def _session_service(
    request: Request,
    db_manager: DatabaseManager,
) -> ChatSessionService:
    return ChatSessionService(
        db_manager,
        tenant_id=str(getattr(request.state, "tenant_id", "local")),
        owner_id=str(getattr(request.state, "owner_id", "admin")),
    )


def _conversation_presentation(
    conversation: Mapping[str, Any],
    *,
    evidence: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    """Return the canonical conversation and execution trace for the browser.

    Canonical messages plus the durable LangGraph trace are the display
    contract.  The assistant-ui snapshot is not part of the conversation
    response and is never reintroduced into the current rendering path.
    """
    payload = dict(conversation)
    payload.pop("thread_state", None)
    messages = payload.get("messages")
    if isinstance(messages, list):
        visible_messages: list[dict[str, Any]] = []
        for raw_message in messages:
            if not isinstance(raw_message, Mapping):
                continue
            message = dict(raw_message)
            if str(message.get("role") or "") == "assistant":
                message["content"], _ = prepare_answer_for_client(
                    message.get("content"),
                    evidence,
                )
            visible_messages.append(message)
        payload["messages"] = visible_messages
    return payload


async def _cancel_conversation_run_before_history_change(
    conversation_id: str,
    db_manager: DatabaseManager,
    *,
    timeout_seconds: float = 10.0,
) -> None:
    """Wait for the writer to finish while the caller holds the transition lease."""
    active_run_registry.configure(db_manager)
    cancelled = await active_run_registry.cancel(conversation_id)
    if cancelled:
        logger.info("[Agent] requested cancellation before history change %s", conversation_id)

    # A retained local handle does not prove that another worker is stopped.
    # Terminal publication commits the transcript before releasing the active
    # slot; always consult that durable authority before replacing history.
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        durable = await asyncio.to_thread(
            db_manager.get_agent_run,
            conversation_id=conversation_id,
        )
        if durable is None or durable.get("status") not in {
            "queued",
            "running",
            "recovering",
            "interrupted",
        }:
            return
        if durable.get("status") == "interrupted":
            # A worker can park for approval just after the cancel request.
            await asyncio.to_thread(db_manager.request_agent_run_cancel, conversation_id)
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.1)
    raise HTTPException(
        status_code=409,
        detail="任务尚未停止，历史未修改，请稍后重试",
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


@router.delete("/agent/conversations")
async def clear_agent_conversations(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """Clear all user-visible conversations in the current ownership scope."""
    service = _session_service(request, db_manager)
    conversation_ids = service.list_conversation_ids()
    deleted = 0
    for conversation_id in conversation_ids:
        async with conversation_transition(db_manager, conversation_id):
            await _cancel_conversation_run_before_history_change(conversation_id, db_manager)
            await agent_graph_runtime.delete_thread(conversation_id)
            deleted += service.delete_conversation(conversation_id)
    return {"deleted": deleted}


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
    execution_trace = _execution_trace_for_run(
        db_manager=db_manager,
        trace=trace,
        run=run,
        durable_run=durable_run,
        quality_projection=(
            trace.get("quality_projection")
            if isinstance(trace, Mapping)
            and isinstance(trace.get("quality_projection"), Mapping)
            else None
        ),
    )
    trace_evidence = (
        execution_trace.get("evidence")
        if isinstance(execution_trace, Mapping)
        and isinstance(execution_trace.get("evidence"), list)
        else []
    )
    trace_evidence = [item for item in trace_evidence if _client_evidence_is_resolvable(item)]
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
            "event": "agent_stage",
            "engine": "langgraph_agent_loop",
            "run_id": trace.get("run_id"),
            "stage": "completed",
            "status": "failed",
            "error_code": "tool_failed",
            "summary": "后台运行已经中断，没有仍在执行的任务",
        }
    assistant_text = (
        run.broadcaster.assistant_text_snapshot
        if is_generating and run
        else str(durable_run.get("final_text") or "") if durable_run else ""
    )
    assistant_text, _ = prepare_answer_for_client(assistant_text, trace_evidence)

    # A conversation can contain several completed turns. Expose a bounded
    # per-run projection so the client can attach each execution process to
    # the assistant message that produced it. A failure to read historical
    # observability data must never prevent the conversation answer itself
    # from loading.
    execution_trace_history: list[dict[str, Any]] = []
    try:
        trace_history = db_manager.list_agent_run_trace_history(
            conversation_id,
            limit=32,
        )
    except Exception:
        logger.warning(
            "[Agent] unable to read trace history conversation_id=%s",
            conversation_id,
            exc_info=True,
        )
        trace_history = []

    if isinstance(trace_history, list):
        for historical_record in trace_history:
            if not isinstance(historical_record, Mapping):
                continue
            historical_run_id = str(historical_record.get("run_id") or "").strip()
            if not historical_run_id:
                continue
            historical_quality_projection = historical_record.get("quality_projection")
            historical_trace = _execution_trace_for_run(
                db_manager=db_manager,
                trace=historical_record,
                run=None,
                durable_run={"run_id": historical_run_id},
                quality_projection=(
                    historical_quality_projection
                    if isinstance(historical_quality_projection, Mapping)
                    else None
                ),
            )
            if not historical_trace:
                continue
            historical_evidence = (
                historical_trace.get("evidence")
                if isinstance(historical_trace.get("evidence"), list)
                else []
            )
            historical_evidence = [
                item
                for item in historical_evidence
                if isinstance(item, Mapping) and _client_evidence_is_resolvable(item)
            ]
            historical_final_text, _ = prepare_answer_for_client(
                str(historical_record.get("final_text") or ""),
                historical_evidence,
            )
            execution_trace_history.append(
                {
                    "run_id": historical_run_id,
                    "status": historical_record.get("status"),
                    "error_code": historical_record.get("error_code"),
                    "latest_stage": historical_record.get("latest_stage"),
                    "final_text": historical_final_text,
                    "created_at": historical_record.get("created_at"),
                    "updated_at": historical_record.get("updated_at"),
                    "execution_trace": historical_trace,
                }
            )

    # During the small window before the trace row is committed, the active
    # broadcaster/event log can still provide a valid execution projection.
    # Add that projection once, so refresh/replay does not lose the current
    # process merely because durable trace persistence is a few milliseconds
    # behind the conversation response.
    current_trace_run_id = str(
        (trace or {}).get("run_id")
        if isinstance(trace, Mapping)
        else ""
    ).strip()
    if not current_trace_run_id:
        current_trace_run_id = str(
            run.run_id if is_generating and run
            else (durable_run.get("run_id") if durable_run else "")
        ).strip()
    if (
        current_trace_run_id
        and (execution_trace or persisted_stage)
        and not any(
            item.get("run_id") == current_trace_run_id
            for item in execution_trace_history
        )
    ):
        execution_trace_history.append(
            {
                "run_id": current_trace_run_id,
                "status": (
                    run.status if is_generating and run
                    else durable_status or reconciled_trace_status
                ),
                "error_code": (
                    trace.get("error_code")
                    if isinstance(trace, Mapping)
                    else None
                ),
                "latest_stage": persisted_stage,
                "final_text": assistant_text,
                "created_at": None,
                "updated_at": None,
                "execution_trace": execution_trace or {"stages": [persisted_stage]},
            }
        )
    conversation["is_generating"] = is_generating
    conversation["execution_trace"] = execution_trace
    conversation["execution_traces"] = execution_trace_history
    context_snapshot = durable_run.get("context_snapshot") if durable_run else None
    pending_interrupt = (
        context_snapshot.get("pending_interrupt")
        if durable_status == "interrupted"
        and isinstance(context_snapshot, dict)
        and context_snapshot.get("engine") == "langgraph_agent_loop"
        and isinstance(context_snapshot.get("pending_interrupt"), dict)
        else None
    )
    conversation["resume_state"] = {
        "run_id": (
            run.run_id
            if is_generating and run
            else (durable_run.get("run_id") if durable_run else (run.run_id if run else None))
        ),
        # `active` is an attachment contract, not a history-exists flag.  A
        # terminal run can have a long durable event log, but replaying it as
        # a live data stream makes the browser recreate transient tool parts
        # after the run has already ended.  Historical stages/results are
        # exposed separately through execution_trace and event_cursor.
        "active": is_generating,
        "is_generating": is_generating,
        "status": (run.status if run else (durable_status or reconciled_trace_status)),
        # The durable event log is the authoritative presentation state.
        # Replaying from zero reconstructs tool cards even if the browser died
        # before its onFinish snapshot.
        "after_chunk_index": (run.broadcaster.history_length if is_generating and run and not durable_run else 0),
        "event_cursor": (run.broadcaster.history_length if is_generating and run else durable_event_cursor),
        "assistant_text": assistant_text,
        "has_tool_events": (
            run.broadcaster.has_tool_events
            if run
            else durable_has_tool_events
        ),
        "latest_stage": persisted_stage,
        "pending_interrupt": pending_interrupt,
    }
    conversation["pending_interrupt"] = pending_interrupt
    return _conversation_presentation(conversation, evidence=trace_evidence)


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
    return _conversation_presentation(conversation)


@router.delete("/agent/conversations/{conversation_id}")
async def delete_agent_conversation(
    conversation_id: str,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = _session_service(request, db_manager)
    async with conversation_transition(db_manager, conversation_id):
        # Resolve ownership under the same lease as cancellation and deletion.
        if not service.get_conversation(conversation_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        await _cancel_conversation_run_before_history_change(conversation_id, db_manager)
        await agent_graph_runtime.delete_thread(conversation_id)
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
async def sync_agent_conversation_snapshot(
    conversation_id: str,
    request: Request,
    payload: Dict[str, Any] = Body(...),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = _session_service(request, db_manager)
    try:
        messages = validate_conversation_snapshot_body(payload)
    except AgentRequestValidationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc
    # ``thread_state`` is an assistant-ui renderer export, not durable Agent
    # state. Never write it back into the current conversation contract; the
    # canonical transcript and LangGraph checkpoint remain authoritative.
    prune_checkpoint = bool(payload.get("prune_agent_context_to_messages"))
    async with conversation_transition(db_manager, conversation_id):
        if not service.get_conversation(conversation_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        if messages is not None:
            # A transcript replacement is a history mutation. Neither a
            # stopped writer nor a new admission may race the checkpoint or
            # transcript update.
            await _cancel_conversation_run_before_history_change(conversation_id, db_manager)
            if agent_graph_runtime.initialized:
                await agent_graph_runtime.replace_checkpoint_messages(conversation_id, messages)
        conversation = await asyncio.to_thread(
            service.save_conversation_snapshot,
            conversation_id,
            messages,
            thread_state={},
            prune_agent_context_to_messages=prune_checkpoint,
        )
        if not conversation:
            raise HTTPException(status_code=404, detail="对话不存在")
    return _conversation_presentation(conversation)
