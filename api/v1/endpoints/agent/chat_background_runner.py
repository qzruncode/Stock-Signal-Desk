"""Durable background lifecycle for the LangGraph control plane."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

from src.agent.langgraph_runtime import agent_graph_runtime
from src.agent.langgraph_runtime.answer_contract import finalize_terminal_answer
from src.agent.runtime_errors import build_runtime_error_receipt
from src.agent.message_normalization import latest_user_text
from src.agent.run_registry import ActiveRun, RunBroadcaster, active_run_registry
from src.agent.runtime_metadata import (
    build_run_runtime_metadata,
    merge_runtime_metadata,
)
from src.agent.terminal_publisher import AgentTerminalPublisher
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager


logger = logging.getLogger(__name__)


def _graph_history_mode(body: Mapping[str, Any]) -> str:
    """Choose continuation or branch semantics for the native checkpoint."""
    run_config = body.get("runConfig") or body.get("run_config")
    custom = run_config.get("custom") if isinstance(run_config, Mapping) else None
    edit_message_id = None
    if isinstance(custom, Mapping):
        edit_message_id = custom.get("editMessageId") or custom.get("edit_message_id")
    requested_mode = str(body.get("history_mode") or "").strip().lower()
    if str(edit_message_id or "").strip() or requested_mode in {"replace", "branch", "reset"}:
        return "replace"
    if requested_mode == "server":
        return "continue"
    return "auto"


def _normalize_agent_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"auto", "direct", "plan", "team", "goal"}:
        raise ValueError(f"unknown agent mode: {normalized}")
    return normalized


def _agent_mode(body: Mapping[str, Any]) -> str:
    """Resolve the five-mode product contract; Auto is the default."""
    return _normalize_agent_mode(body.get("agent_mode") or "auto")


def _checkpoint_thread_id(state: Mapping[str, Any], conversation_id: str) -> str:
    """Persist the thread namespace owned by the selected product graph."""
    if str(state.get("resolved_agent_mode") or state.get("agent_mode") or "").strip().lower() == "goal":
        return agent_graph_runtime.goal_thread_id(conversation_id)
    return agent_graph_runtime.thread_id(conversation_id)


def _terminal_runtime_metadata(
    base: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
    status: str,
    attempt: int,
) -> dict[str, Any]:
    """Add only server-observed terminal facts to the run envelope."""
    return merge_runtime_metadata(
        base,
        {
            "actual_agent_mode": state.get("resolved_agent_mode") or None,
            "attempt": max(1, int(attempt or 1)),
            "execution": {
                "status": status,
                "orchestrator_mode": state.get("orchestrator_mode"),
                "resolved_agent_mode": state.get("resolved_agent_mode"),
                "model_turn_count": int(state.get("model_turn_count") or 0),
                "tool_call_count": int(state.get("tool_call_count") or 0),
                "runtime_error_count": len(state.get("runtime_errors") or []),
            },
        },
    )


async def _checkpoint_state(conversation_id: str) -> dict[str, Any]:
    try:
        return await agent_graph_runtime.get_state(conversation_id)
    except Exception:
        logger.warning("[Agent] unable to read LangGraph state", exc_info=True)
        return {}


async def _release_cancelled_run_resources(
    db_manager: DatabaseManager,
    run_id: str,
) -> None:
    """Free fan-out provider slots after a user-visible cancellation.

    This is also needed when a different worker wrote the durable cancellation
    request: its local registry cannot clean up the slots owned by this worker.
    It does not interrupt or deadline model reasoning; it runs only after the
    run has already been cancelled.
    """
    try:
        await asyncio.shield(
            asyncio.to_thread(db_manager.release_agent_resources_for_run, run_id)
        )
    except Exception:
        logger.warning(
            "[Agent] failed to release resources for cancelled run_id=%s",
            run_id,
            exc_info=True,
        )


async def _execute_background_agent_run(
    *,
    controller: RunBroadcaster,
    run: ActiveRun,
    messages: list[dict[str, Any]],
    body: Mapping[str, Any],
    llm_cfg: Mapping[str, Any],
    conversation_id: str,
    db_manager: DatabaseManager,
    session_service: ChatSessionService,
    tenant_id: str,
    owner_id: str,
    resume_decision: Mapping[str, Any] | None = None,
    recovery: bool = False,
) -> None:
    """Run, resume, or recover one checkpointed message/tool loop."""
    from src.services.agent_prompt_service import AgentPromptService

    prompt_service = AgentPromptService(db_manager)
    system_prompt, is_fallback = prompt_service.get_active_system_prompt()
    prompt_identity = prompt_service.get_active_system_prompt_metadata()
    durable_run = await asyncio.to_thread(db_manager.get_agent_run, run_id=run.run_id)
    persisted_metadata = (
        durable_run.get("runtime_metadata")
        if isinstance(durable_run, Mapping)
        and isinstance(durable_run.get("runtime_metadata"), Mapping)
        else {}
    )
    runtime_metadata = build_run_runtime_metadata(
        messages=messages,
        request_body=body,
        llm_config=llm_cfg,
        tool_catalog_version=getattr(agent_graph_runtime.catalog, "version", None),
    )
    runtime_metadata = merge_runtime_metadata(runtime_metadata, persisted_metadata)
    runtime_metadata = merge_runtime_metadata(
        runtime_metadata,
        {
            "prompt": prompt_identity,
            "tool_catalog_version": getattr(agent_graph_runtime.catalog, "version", "unknown"),
            "attempt": max(1, int(run.attempt or 1)),
            "execution": {
                "process_recovery": bool(recovery),
                "approval_resume": resume_decision is not None,
            },
        },
    )
    updated = await asyncio.to_thread(
        db_manager.update_agent_run_runtime_metadata,
        run.run_id,
        {
            "prompt": prompt_identity,
            "tool_catalog_version": runtime_metadata.get("tool_catalog_version"),
            "attempt": runtime_metadata.get("attempt"),
        },
        worker_id=active_run_registry.worker_id,
        attempt=run.attempt,
    )
    if not updated:
        logger.warning("[Agent] unable to persist runtime metadata run_id=%s", run.run_id)
    logger.info(
        "[Agent] system prompt %s",
        "fallback to source default" if is_fallback else "from template",
    )
    terminal_publisher = AgentTerminalPublisher(
        controller=controller,
        run=run,
        messages=messages,
        request_body=body,
        conversation_id=conversation_id,
        database=db_manager,
        session_service=session_service,
        worker_id=active_run_registry.worker_id,
        runtime_metadata=runtime_metadata,
    )

    try:
        common = {
            "llm_config": dict(llm_cfg),
            "database": db_manager,
            "controller": controller,
            "run_id": run.run_id,
            "conversation_id": conversation_id,
            "run_attempt": run.attempt,
            "tenant_id": tenant_id,
            "owner_id": owner_id,
        }
        if resume_decision is not None:
            graph_result = await agent_graph_runtime.resume(
                interrupt_id=str(resume_decision.get("interrupt_id") or ""),
                decision={
                    "decision": str(resume_decision.get("decision") or ""),
                    "fingerprint": str(resume_decision.get("fingerprint") or ""),
                    "message": str(resume_decision.get("message") or ""),
                },
                **common,
            )
        elif recovery:
            graph_result = await agent_graph_runtime.recover(**common)
        else:
            graph_result = await agent_graph_runtime.run_new(
                messages=messages,
                user_text=latest_user_text(messages),
                system_prompt=system_prompt,
                history_mode=_graph_history_mode(body),
                planning_mode=str(body.get("planning_mode") or "auto"),
                # The visible selector is the only product-mode contract.
                # Missing values resolve to the explicit default: auto.
                agent_mode=_agent_mode(body),
                **common,
            )

        if graph_result.interrupted:
            pending = dict(graph_result.pending_interrupt or {})
            await controller.drain()
            await asyncio.to_thread(
                db_manager.update_agent_run_runtime_metadata,
                run.run_id,
                _terminal_runtime_metadata(
                    runtime_metadata,
                    state=graph_result.state,
                    status="interrupted",
                    attempt=run.attempt,
                ),
                worker_id=active_run_registry.worker_id,
                attempt=run.attempt,
            )
            parked = await asyncio.to_thread(
                db_manager.interrupt_agent_run,
                run.run_id,
                worker_id=active_run_registry.worker_id,
                attempt=run.attempt,
                checkpoint={
                    "engine": "langgraph_agent_loop",
                    "thread_id": _checkpoint_thread_id(graph_result.state, conversation_id),
                    "pending_interrupt": pending,
                },
            )
            if not parked:
                raise RuntimeError("failed to persist LangGraph approval interrupt")
            await active_run_registry.mark_interrupted(conversation_id)
            return

        final_text = graph_result.final_text
        controller.assistant_text_snapshot = final_text
        terminal_status = graph_result.status
        if terminal_status not in {"completed", "partial", "failed", "cancelled", "blocked"}:
            terminal_status = "failed"
        await terminal_publisher.commit(
            status=terminal_status,
            final_text=final_text,
            graph_state=graph_result.state,
            error_code=graph_result.error_code,
            error_detail=(
                str(graph_result.state.get("terminal_detail") or "").strip()
                or graph_result.error_code
            ),
            stage_history=graph_result.stage_history,
            runtime_metadata=_terminal_runtime_metadata(
                runtime_metadata,
                state=graph_result.state,
                status=terminal_status,
                attempt=run.attempt,
            ),
        )
        await active_run_registry.mark_done(
            conversation_id,
            terminal_status,
            final_text=final_text,
            error=graph_result.error_code,
            persist=False,
        )
    except asyncio.CancelledError:
        if active_run_registry.shutting_down or run.cancel_reason in {"restart", "lease_lost"}:
            raise
        try:
            # User cancellation is a terminal product action.  Close the
            # native checkpoint before publishing ``agent_runs=cancelled``;
            # otherwise the Team graph can be recovered later with workers or
            # review stages still marked as running.
            state = await agent_graph_runtime.finalize_checkpoint(
                conversation_id,
                status="cancelled",
                error_code="cancelled",
                terminal_detail="用户已停止本轮任务",
            )
            if not state:
                state = await _checkpoint_state(conversation_id)
            partial = str(
                state.get("answer_final")
                or state.get("answer_draft")
                or controller.assistant_text_snapshot
                or ""
            ).strip()
            partial = partial + "\n\n[已停止]" if partial else "[已停止]"
            latest_stage = {
                "event": "agent_stage",
                "engine": "langgraph_agent_loop",
                "run_id": run.run_id,
                "stage": "publish",
                "status": "cancelled",
                "summary": "用户已停止本轮任务",
            }
            controller.add_data(latest_stage)
            await terminal_publisher.commit(
                status="cancelled",
                final_text=partial,
                graph_state=state,
                error_code="cancelled",
                error_detail="cancelled by user",
                latest_stage=latest_stage,
                runtime_metadata=_terminal_runtime_metadata(
                    runtime_metadata,
                    state=state,
                    status="cancelled",
                    attempt=run.attempt,
                ),
            )
            await active_run_registry.mark_done(
                conversation_id,
                "cancelled",
                final_text=partial,
                error="cancelled",
                persist=False,
            )
        finally:
            await _release_cancelled_run_resources(db_manager, run.run_id)
        raise
    except Exception as exc:
        logger.exception("[Agent] LangGraph background run failed")
        runtime_error = build_runtime_error_receipt(
            exc,
            run_id=run.run_id,
            conversation_id=conversation_id,
            scope="coordinator",
            node="background_runner",
            phase="run",
            attempt=max(1, int(run.attempt or 1)),
            failure_kind="graph",
            error_code="agent_runtime_failed",
            terminal_impact="terminal",
            details={
                "run_attempt": run.attempt,
                "recovery": recovery,
            },
        )
        # Keep provider/implementation details in server logs only.  First
        # close the native checkpoint, then publish the same safe terminal
        # status through ``agent_runs``; otherwise the browser can see a failed
        # conversation while the checkpoint still advertises an executing task.
        state = await agent_graph_runtime.finalize_checkpoint(
            conversation_id,
            status="failed",
            error_code="agent_runtime_failed",
            terminal_detail="本轮任务发生异常，已安全结束并保留执行详情。",
        )
        if not state:
            state = await _checkpoint_state(conversation_id)
        partial = str(
            state.get("answer_final")
            or state.get("answer_draft")
            or controller.assistant_text_snapshot
            or ""
        ).strip()
        if not partial:
            partial = "本轮任务未能完成，已保留执行过程，请稍后重试。"
        partial = finalize_terminal_answer(
            partial,
            status="failed",
            error_code="agent_runtime_failed",
            detail="本轮任务发生异常，已安全结束并保留执行详情。",
        )
        latest_stage = {
            "event": "agent_stage",
            "engine": "langgraph_agent_loop",
            "run_id": run.run_id,
            "stage": "publish",
            "status": "failed",
            "error_code": "agent_runtime_failed",
            "summary": "本轮任务发生未处理异常",
            "details": {
                "kind": "runtime_error",
                "runtime_error": runtime_error,
            },
        }
        controller.add_data(latest_stage)
        if partial not in controller.assistant_text_snapshot:
            if controller.assistant_text_snapshot and not controller.assistant_text_snapshot.endswith(("\n", "\n\n")):
                controller.append_text("\n\n")
            controller.append_text(partial)
        try:
            await terminal_publisher.commit(
                status="failed",
                final_text=partial,
                graph_state=state,
                error_code="agent_runtime_failed",
                error_detail="本轮任务发生异常，已安全结束并保留执行详情。",
                latest_stage=latest_stage,
                runtime_metadata=_terminal_runtime_metadata(
                    runtime_metadata,
                    state=state,
                    status="failed",
                    attempt=run.attempt,
                ),
            )
        except Exception:
            logger.exception("[Agent] atomic failed terminal commit failed run_id=%s", run.run_id)
        await active_run_registry.mark_done(
            conversation_id,
            "failed",
            final_text=partial,
            error="agent_runtime_failed",
            persist=False,
        )


__all__ = ["_execute_background_agent_run"]
