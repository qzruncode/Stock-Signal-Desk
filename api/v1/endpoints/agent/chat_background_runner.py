"""Durable background lifecycle for the LangGraph control plane."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

from src.agent.langgraph_runtime import agent_graph_runtime
from src.agent.message_normalization import latest_user_text
from src.agent.run_registry import ActiveRun, RunBroadcaster, active_run_registry
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

    system_prompt, is_fallback = AgentPromptService(db_manager).get_active_system_prompt()
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
                **common,
            )

        if graph_result.interrupted:
            pending = dict(graph_result.pending_interrupt or {})
            await controller.drain()
            parked = await asyncio.to_thread(
                db_manager.interrupt_agent_run,
                run.run_id,
                worker_id=active_run_registry.worker_id,
                attempt=run.attempt,
                checkpoint={
                    "engine": "langgraph_agent_loop",
                    "thread_id": agent_graph_runtime.thread_id(conversation_id),
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
        controller.add_error(str(exc))
        state = await _checkpoint_state(conversation_id)
        partial = str(
            state.get("answer_final")
            or state.get("answer_draft")
            or controller.assistant_text_snapshot
            or ""
        ).strip()
        latest_stage = {
            "event": "agent_stage",
            "engine": "langgraph_agent_loop",
            "run_id": run.run_id,
            "stage": "publish",
            "status": "failed",
            "error_code": "agent_runtime_failed",
            "summary": "本轮任务发生未处理异常",
        }
        controller.add_data(latest_stage)
        try:
            await terminal_publisher.commit(
                status="failed",
                final_text=partial,
                graph_state=state,
                error_code="agent_runtime_failed",
                error_detail=f"{type(exc).__name__}: {exc}",
                latest_stage=latest_stage,
            )
        except Exception:
            logger.exception("[Agent] atomic failed terminal commit failed run_id=%s", run.run_id)
        await active_run_registry.mark_done(
            conversation_id,
            "failed",
            final_text=partial,
            error=str(exc),
            persist=False,
        )


__all__ = ["_execute_background_agent_run"]
