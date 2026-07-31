# -*- coding: utf-8 -*-
"""Recovery of durable runs whose worker lease expired."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Mapping

from src.agent.run_registry import ActiveRun, RunBroadcaster, active_run_registry
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

async def recover_interrupted_agent_runs(
    db_manager: DatabaseManager,
    *,
    limit: int = 20,
    config_loader: Any,
    background_runner: Any,
) -> int:
    """Reclaim expired durable leases and restart them with the same run_id."""
    active_run_registry.configure(db_manager)
    candidates = await asyncio.to_thread(
        db_manager.list_recoverable_agent_runs,
        limit=limit,
    )
    recovered = 0
    for candidate in candidates:
        run_id = str(candidate.get("run_id") or "")
        conversation_id = str(candidate.get("conversation_id") or "")
        if not run_id or not conversation_id:
            continue
        reclaimed = await asyncio.to_thread(
            db_manager.reclaim_agent_run,
            run_id,
            worker_id=active_run_registry.worker_id,
        )
        if reclaimed is None:
            continue
        request_payload = reclaimed.get("request") or {}
        messages = request_payload.get("messages")
        body = request_payload.get("body")
        if not isinstance(messages, list) or not isinstance(body, Mapping):
            await asyncio.to_thread(
                db_manager.finish_agent_run,
                run_id,
                status="failed",
                error_code="recovery_payload_invalid",
                error_detail="durable request payload is incomplete",
            )
            continue
        try:
            llm_cfg = config_loader()
            run = await active_run_registry.adopt_recovered(
                conversation_id=conversation_id,
                run_id=run_id,
                event_cursor=int(reclaimed.get("event_cursor") or 0),
                attempt=int(reclaimed.get("attempt") or 1),
            )
            session_service = ChatSessionService(
                db_manager,
                tenant_id=str(reclaimed.get("tenant_id") or "local"),
                owner_id=str(reclaimed.get("owner_id") or "admin"),
            )
            agent_context = request_payload.get("agent_context")

            async def factory(
                broadcaster: RunBroadcaster,
                *,
                _run: ActiveRun = run,
                _messages: List[Dict[str, Any]] = list(messages),
                _body: Mapping[str, Any] = dict(body),
                _context: Mapping[str, Any] | None = (agent_context if isinstance(agent_context, Mapping) else None),
                _checkpoint: Mapping[str, Any] | None = (
                    reclaimed.get("context_snapshot")
                    if isinstance(
                        reclaimed.get("context_snapshot"),
                        Mapping,
                    )
                    else None
                ),
            ) -> "asyncio.Task":
                return asyncio.create_task(
                    background_runner(
                        controller=broadcaster,
                        run=_run,
                        messages=_messages,
                        body=_body,
                        llm_cfg=llm_cfg,
                        agent_context=_context,
                        conversation_id=conversation_id,
                        db_manager=db_manager,
                        session_service=session_service,
                        recovery_checkpoint=_checkpoint,
                    )
                )

            await run.start(factory)
            recovered += 1
            logger.info(
                "[Agent] recovered durable run_id=%s conversation_id=%s attempt=%s",
                run_id,
                conversation_id,
                reclaimed.get("attempt"),
            )
        except Exception as exc:
            logger.exception("[Agent] failed to recover run_id=%s", run_id)
            await asyncio.to_thread(
                db_manager.finish_agent_run,
                run_id,
                status="failed",
                error_code="recovery_start_failed",
                error_detail=str(exc),
            )
    return recovered


__all__ = ["recover_interrupted_agent_runs"]
