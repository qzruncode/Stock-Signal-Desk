# -*- coding: utf-8 -*-
"""Atomic terminal projection for a durable Agent run."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, MutableMapping, Sequence

from src.agent.orchestrator_v2.contracts import AgentStageEventV2
from src.agent.run_registry import ActiveRun, RunBroadcaster
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager


@dataclass
class AgentTerminalPublisher:
    """Commit transcript, context, artifacts, trace and run status together."""

    controller: RunBroadcaster
    run: ActiveRun
    messages: Sequence[Mapping[str, Any]]
    request_body: Mapping[str, Any]
    initial_agent_context: Mapping[str, Any] | None
    conversation_id: str
    database: DatabaseManager
    session_service: ChatSessionService
    state: MutableMapping[str, Any]
    worker_id: str

    async def commit(
        self,
        *,
        status: str,
        final_text: str,
        error_code: str | None = None,
        error_detail: str | None = None,
        latest_stage: AgentStageEventV2 | None = None,
    ) -> None:
        # A durable subscriber treats the terminal cursor as a hard boundary.
        # Flush stream events first so that boundary can never hide final data.
        await self.controller.drain()
        terminal_messages = [dict(message) for message in self.messages]
        if final_text.strip():
            terminal_messages.append(
                {
                    "id": str(self.request_body.get("unstable_assistantMessageId") or f"assistant-{uuid.uuid4().hex}"),
                    "role": "assistant",
                    "content": final_text,
                    "created_at": datetime.now().isoformat(),
                }
            )
        normalized_messages = self.session_service.normalize_messages(terminal_messages)
        next_context = (
            self.state.get("agent_context")
            if isinstance(self.state.get("agent_context"), dict)
            else self.initial_agent_context
        )
        first_user_text = next(
            (str(message.get("content") or "") for message in normalized_messages if message.get("role") == "user"),
            "",
        )
        trace_payload = dict(
            self.state.get("_terminal_trace") if isinstance(self.state.get("_terminal_trace"), Mapping) else {}
        )
        trace_payload["status"] = status
        if error_code:
            trace_payload["error_code"] = error_code
        if latest_stage is not None:
            trace_payload["latest_stage"] = latest_stage.model_dump(mode="json")

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                committed = await asyncio.shield(
                    asyncio.to_thread(
                        self.database.commit_agent_run_terminal,
                        run_id=self.run.run_id,
                        conversation_id=self.conversation_id,
                        status=status,
                        messages=normalized_messages,
                        final_text=final_text,
                        agent_context=(next_context if isinstance(next_context, Mapping) else None),
                        artifacts=tuple(self.state.get("_terminal_artifacts") or ()),
                        trace=trace_payload,
                        generated_title=(
                            self.session_service.generate_title(first_user_text) if first_user_text else None
                        ),
                        error_code=error_code,
                        error_detail=error_detail,
                        worker_id=self.worker_id,
                        attempt=self.run.attempt,
                    )
                )
                if not committed:
                    raise RuntimeError("atomic Agent terminal commit did not find its run")
                return
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.1 * (2**attempt))
        assert last_error is not None
        raise last_error


__all__ = ["AgentTerminalPublisher"]
