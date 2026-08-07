"""Atomic terminal projection for the LangGraph Agent runtime."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from src.agent.run_registry import ActiveRun, RunBroadcaster
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager


@dataclass
class AgentTerminalPublisher:
    controller: RunBroadcaster
    run: ActiveRun
    messages: Sequence[Mapping[str, Any]]
    request_body: Mapping[str, Any]
    conversation_id: str
    database: DatabaseManager
    session_service: ChatSessionService
    worker_id: str

    async def commit(
        self,
        *,
        status: str,
        final_text: str,
        graph_state: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        latest_stage: Mapping[str, Any] | None = None,
    ) -> None:
        """Commit transcript, generic trace, and run status in one DB transaction."""
        await self.controller.drain()
        terminal_messages = [dict(message) for message in self.messages]
        if final_text.strip():
            terminal_messages.append(
                {
                    "id": str(
                        self.request_body.get("unstable_assistantMessageId")
                        or f"assistant-{uuid.uuid4().hex}"
                    ),
                    "role": "assistant",
                    "content": final_text,
                    "created_at": datetime.now().isoformat(),
                }
            )
        normalized_messages = self.session_service.normalize_messages(terminal_messages)
        first_user_text = next(
            (
                str(message.get("content") or "")
                for message in normalized_messages
                if message.get("role") == "user"
            ),
            "",
        )
        state = dict(graph_state or {})
        plan = state.get("plan") if isinstance(state.get("plan"), Mapping) else {}
        actions = [
            dict(item)
            for item in (plan.get("actions") or [])
            if isinstance(item, Mapping)
        ]
        tool_results = [
            dict(item)
            for item in (state.get("tool_results") or [])
            if isinstance(item, Mapping)
        ]
        evidence = [
            dict(item)
            for item in (state.get("evidence") or [])
            if isinstance(item, Mapping)
        ]
        verification = (
            dict(state.get("verification") or {})
            if isinstance(state.get("verification"), Mapping)
            else {}
        )
        quality_projection = {
            "engine": "langgraph",
            "intent": state.get("intent") if isinstance(state.get("intent"), Mapping) else {},
            "actions": actions,
            "tool_results": tool_results,
            "evidence": evidence,
            "verification": verification,
            "completed_action_ids": list(state.get("completed_action_ids") or []),
            "budgets": {
                "plan_round": int(state.get("plan_round") or 0),
                "max_plan_rounds": int(state.get("max_plan_rounds") or 0),
                "search_expansions": int(state.get("search_expansions") or 0),
                "max_search_expansions": int(state.get("max_search_expansions") or 0),
                "verification_round": int(state.get("verification_round") or 0),
                "max_verification_rounds": int(state.get("max_verification_rounds") or 0),
                "max_elapsed_seconds": int(state.get("max_elapsed_seconds") or 0),
            },
        }
        trace_payload = {
            "engine": "langgraph",
            "run_id": self.run.run_id,
            "status": status,
            "intent": state.get("intent"),
            "plan_round": state.get("plan_round"),
            "search_expansions": state.get("search_expansions"),
            "verification_round": state.get("verification_round"),
            "verification": verification,
            "quality_projection": quality_projection,
        }
        if error_code:
            trace_payload["error_code"] = error_code
        if latest_stage is not None:
            trace_payload["latest_stage"] = dict(latest_stage)

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
                        # Old orchestration JSON is never fed into the new
                        # graph. Clear it as the new turn becomes canonical.
                        agent_context={},
                        artifacts=(),
                        conclusions=(),
                        trace=trace_payload,
                        generated_title=(
                            self.session_service.generate_title(first_user_text)
                            if first_user_text
                            else None
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
