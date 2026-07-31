# -*- coding: utf-8 -*-
"""Durable background run lifecycle."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Mapping

from src.agent.run_registry import ActiveRun, RunBroadcaster, active_run_registry
from src.agent.terminal_publisher import AgentTerminalPublisher
from src.agent.orchestrator_v2.contracts import AgentErrorCode, AgentStage, AgentStageEventV2, StageStatus
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager
from src.agent.user_memory import build_explicit_memory_message

logger = logging.getLogger(__name__)

async def _execute_background_agent_run(
    *,
    controller: RunBroadcaster,
    run: ActiveRun,
    messages: List[Dict[str, Any]],
    body: Mapping[str, Any],
    llm_cfg: Mapping[str, Any],
    agent_context: Mapping[str, Any] | None,
    conversation_id: str,
    db_manager: DatabaseManager,
    session_service: ChatSessionService,
    recovery_checkpoint: Mapping[str, Any] | None = None,
    pipeline_runner: Any,
    terminal_status_mapper: Any,
    explicit_memories: List[Mapping[str, Any]] | None = None,
) -> None:
    """Execute one durable run; safe to call for both first-run and recovery."""
    from src.services.agent_prompt_service import AgentPromptService

    system_prompt, is_fallback = AgentPromptService(db_manager).get_active_system_prompt()
    logger.info(
        "[Agent] system prompt %s",
        "fallback to source default" if is_fallback else "from template",
    )

    last_save_ts = 0.0
    state: Dict[str, Any] = {"assistant_text": ""}
    terminal_publisher = AgentTerminalPublisher(
        controller=controller,
        run=run,
        messages=messages,
        request_body=body,
        initial_agent_context=agent_context,
        conversation_id=conversation_id,
        database=db_manager,
        session_service=session_service,
        state=state,
        worker_id=active_run_registry.worker_id,
    )

    async def on_progress(assistant_text_so_far: str) -> None:
        nonlocal last_save_ts
        controller.assistant_text_snapshot = assistant_text_so_far
        now = asyncio.get_running_loop().time()
        if now - last_save_ts < 3.0:
            return
        last_save_ts = now
        await asyncio.to_thread(
            session_service.save_partial_assistant_text,
            conversation_id,
            assistant_text_so_far,
        )

    final_response_text = ""
    try:
        memory_message = build_explicit_memory_message(
            explicit_memories or []
        )
        execution_messages = (
            [memory_message, *messages]
            if memory_message is not None
            else messages
        )
        final_response_text = await pipeline_runner(
            controller,
            execution_messages,
            dict(llm_cfg),
            system_prompt,
            on_progress=on_progress,
            state=state,
            conversation_context=agent_context,
            conversation_id=conversation_id,
            run_id=run.run_id,
            run_attempt=run.attempt,
            recovery_checkpoint=recovery_checkpoint,
            db_manager=db_manager,
        )

        terminal_status = terminal_status_mapper(state)
        terminal_error = (
            str(state.get("_run_error_code") or "")
            if state.get("_run_status")
            in {
                "failed",
                "blocked",
                "partial",
            }
            else None
        )
        await terminal_publisher.commit(
            status=terminal_status,
            final_text=final_response_text,
            error_code=terminal_error,
            error_detail=terminal_error,
        )
        await active_run_registry.mark_done(
            conversation_id,
            terminal_status,
            final_text=final_response_text,
            error=terminal_error,
            persist=False,
        )
    except asyncio.CancelledError:
        partial = str(state.get("assistant_text") or "")
        if active_run_registry.shutting_down or run.cancel_reason in {"restart", "lease_lost"}:
            # A planned process restart is not a user cancellation.  Preserve
            # the partial snapshot and leave the durable run active with a
            # released lease; the next worker will reclaim the same run_id.
            if partial.strip():
                try:
                    await asyncio.shield(
                        asyncio.to_thread(
                            session_service.save_partial_assistant_text,
                            conversation_id,
                            partial,
                        )
                    )
                except (asyncio.CancelledError, Exception):
                    logger.warning(
                        "[Agent] restart-time partial save failed",
                        exc_info=True,
                    )
            raise

        cancelled_stage = AgentStageEventV2(
            run_id=run.run_id,
            stage=AgentStage.COMPLETED,
            status=StageStatus.CANCELLED,
            summary="用户已停止本轮分析",
        )
        controller.add_data(cancelled_stage.model_dump(mode="json"))
        partial = partial.rstrip() + "\n\n[已停止]" if partial.strip() else "[已停止]"
        try:
            await terminal_publisher.commit(
                status="cancelled",
                final_text=partial,
                error_code="cancelled",
                error_detail="cancelled by user",
                latest_stage=cancelled_stage,
            )
        except Exception:
            logger.exception(
                "[Agent] atomic cancel terminal commit failed run_id=%s",
                run.run_id,
            )
            # Leave the durable active slot intact. The expired lease exposes
            # the failed terminal commit to recovery/operations instead of
            # publishing a transcript/run mismatch.
            raise
        await active_run_registry.mark_done(
            conversation_id,
            "cancelled",
            final_text=partial,
            error="cancelled",
            persist=False,
        )
        raise
    except Exception as exc:
        logger.exception("[Agent] background run failed")
        controller.add_error(str(exc))
        failed_stage = AgentStageEventV2(
            run_id=run.run_id,
            stage=AgentStage.COMPLETED,
            status=StageStatus.FAILED,
            error_code=AgentErrorCode.TOOL_FAILED,
            summary="本轮分析发生未处理的内部异常",
        )
        controller.add_data(failed_stage.model_dump(mode="json"))
        partial = str(state.get("assistant_text") or "")
        try:
            await terminal_publisher.commit(
                status="failed",
                error_code=AgentErrorCode.TOOL_FAILED.value,
                error_detail=str(exc),
                final_text=partial,
                latest_stage=failed_stage,
            )
        except Exception:
            logger.exception(
                "[Agent] atomic failed terminal commit failed run_id=%s",
                run.run_id,
            )
        await active_run_registry.mark_done(
            conversation_id,
            "failed",
            final_text=partial,
            error=str(exc),
            persist=False,
        )


__all__ = ["_execute_background_agent_run"]
