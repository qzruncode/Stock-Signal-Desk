"""Worker event projection without leaking private child streams."""

from __future__ import annotations

from typing import Any, Mapping

from ..events import GraphEventBridge


class _ForwardedWorkerToolCall:
    """Forward a worker tool handle to the root assistant stream.

    Worker model text stays private, but a tool call is part of the product's
    chronological execution story.  The root bridge owns the actual stream
    handle so tool begin, arguments, and result remain one ordered part while
    the worker still uses its scoped executor.
    """

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def append_args_text(self, value: str) -> None:
        append = getattr(self._handle, "append_args_text", None)
        if callable(append):
            append(value)

    def set_response(self, result: Any, is_error: bool = False) -> None:
        set_response = getattr(self._handle, "set_response", None)
        if callable(set_response):
            set_response(result, is_error=is_error)

    def close(self) -> None:
        close = getattr(self._handle, "close", None)
        if callable(close):
            close()


class TeamWorkerEventBridge(GraphEventBridge):
    """Capture a child graph locally and project only safe stages to the root."""

    def __init__(
        self,
        parent: GraphEventBridge,
        *,
        team_id: str,
        task_id: str,
        agent_id: str,
        expert_id: str,
        display_name: str | None = None,
        scope: str = "expert",
        attempt: int = 1,
    ) -> None:
        super().__init__(None, run_id=parent.run_id)
        self.parent = parent
        self.team_id = team_id
        self.task_id = task_id
        self.agent_id = agent_id
        self.expert_id = str(expert_id).strip()
        self.display_name = str(display_name or "").strip()
        self.scope = str(scope or "expert").strip().lower()
        self.attempt = max(1, int(attempt or 1))

    def commit_model_progress(self) -> None:
        """Forward one completed worker model turn to the parent lane.

        Child model chunks are intentionally buffered until the existing
        runtime accepts the complete tool-planning turn.  Forwarding the
        aggregate as one typed part preserves the worker's own chronology
        without exposing a partial sentence or creating a second assistant
        stream.
        """
        if self._model_progress_committed:
            return
        text = "".join(self._model_text_buffer).strip()
        # The reviewer/synthesizer has a different publication contract from
        # a research worker: its model turn is the candidate final answer.
        # Forwarding that candidate as an expert projection makes the user
        # see a second complete answer before the parent publishes the
        # server-accepted answer. Keep the child text local for validation.
        if text and self.scope != "review":
            self.parent.publish_model_projection(
                text,
                scope="expert",
                collaboration_id=self.team_id,
                agent_id=self.agent_id,
                task_id=self.task_id,
                phase="worker",
                kind="worker-progress",
                attempt=self.attempt,
                parent_event_id=f"{self.agent_id}:worker",
                projection_id=f"{self.agent_id}:worker:{self._round_id or 'turn'}:projection"[:192],
            )
            self._model_text_published = text
        self._model_text_buffer = []
        self._model_progress_committed = True

    def model_message(self, message: Any) -> None:
        """Buffer worker narration until the model turn has actually ended.

        A provider commonly starts a tool-call stream before it has finished
        the natural-language prefix.  Treating the first ``tool_call_chunks``
        item as a boundary truncates the exact text the user should see.  A
        worker projection is therefore committed only from the middleware's
        completed-turn hook, or from LangChain's explicit final chunk when the
        provider marks one.  The latter is important for stream consumers
        whose outer graph loop observes the chunks after the middleware hook.
        """
        super().model_message(message)
        tool_call_chunks = getattr(message, "tool_call_chunks", None) or []
        tool_calls = getattr(message, "tool_calls", None) or []
        is_last_chunk = str(getattr(message, "chunk_position", "") or "").strip().lower() == "last"
        if is_last_chunk and (tool_call_chunks or tool_calls):
            self.commit_model_progress()

    def publish_model_projection(self, text: str, **kwargs: Any) -> None:
        """Forward structured worker-contract projections to the parent lane."""
        if self.scope == "review":
            return
        payload = dict(kwargs)
        payload.update(
            {
                "scope": "expert",
                "collaboration_id": self.team_id,
                "agent_id": self.agent_id,
                "task_id": self.task_id,
                "attempt": self.attempt,
            }
        )
        self.parent.publish_model_projection(text, **payload)

    def commit_model_answer(self, answer: str, **_kwargs: Any) -> None:
        """Keep a worker's terminal answer internal; parent publishes its report.

        A child graph's plain final answer is not a collaboration handoff and
        would duplicate the validated ``WorkerAssessment``.  The worker state
        still retains that answer for assessment, while the parent stream only
        receives the accepted structured report below.
        """
        normalized = str(answer or "").strip()
        self._model_text_buffer = []
        self._model_text_published = normalized
        self._last_committed_answer = normalized or None
        self._model_progress_committed = True

    def _qualified_tool_call_id(self, value: str | None) -> str | None:
        normalized = str(value or '').strip()
        if not normalized:
            return None
        prefix = f'{self.agent_id}:'
        if normalized.startswith(prefix):
            return normalized[:192]
        return f'{prefix}{normalized}'[:192]

    async def add_tool_call(
        self,
        tool_name: str,
        tool_call_id: str | None = None,
        parent_id: str | None = None,
    ) -> _ForwardedWorkerToolCall:
        """Project scoped worker tools onto the root ordered stream.

        The worker bridge intentionally has no direct controller of its own;
        otherwise a child graph could leak private model output or create a
        second stream.  Tool calls are the explicit exception: they are safe,
        user-facing execution facts, and the parent bridge already enforces
        the assistant-stream boundary and payload limits.
        """
        # Route through the parent bridge, not around it.  The bridge owns the
        # compatibility boundary for assistant-stream versions that do not
        # support ``parent_id``; calling the raw controller here would bring
        # that exact runtime TypeError back for Team workers.
        qualified_tool_call_id = self._qualified_tool_call_id(tool_call_id)
        if qualified_tool_call_id is None:
            qualified_tool_call_id = f'{self.agent_id}:call_{id(self)}'[:192]
        handle = await self.parent.add_tool_call(
            tool_name,
            tool_call_id=qualified_tool_call_id,
            parent_id=parent_id,
        )
        return _ForwardedWorkerToolCall(handle)

    def stage(
        self,
        stage: str,
        status: str,
        summary: str,
        *,
        action_id: str | None = None,
        tool_call_id: str | None = None,
        round_id: str | None = None,
        error_code: str | None = None,
        details: Mapping[str, Any] | None = None,
        user_message: str | None = None,
    ) -> dict[str, Any]:
        local = super().stage(
            stage,
            status,
            summary,
            action_id=action_id,
            tool_call_id=tool_call_id,
            round_id=round_id,
            error_code=error_code,
            details=details,
            user_message=user_message,
        )
        # Child publication and worker-local evidence checks are not the root
        # product boundary.  The parent owns final publication and the final
        # reviewer owns the authoritative evidence/reflection stages.  Keep
        # these events in the child-local history for diagnostics, but avoid
        # presenting an intermediate worker warning as the run's verdict.
        if stage == "publish" or (self.scope != "review" and stage in {"evidence", "reflection"}):
            return local
        projected_details = {
            **dict(details or {}),
            "team_id": self.team_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "expert_id": self.expert_id,
            "agent_display_name": self.display_name,
            "scope": self.scope,
        }
        action_prefix = f"{self.agent_id}:"
        normalized_action_id = str(action_id or "")
        projected_action_id = (
            (
                normalized_action_id
                if normalized_action_id.startswith(action_prefix)
                else f"{action_prefix}{normalized_action_id}"
            )
            if normalized_action_id
            else None
        )
        normalized_tool_call_id = str(tool_call_id or "")
        projected_tool_call_id = (
            (
                normalized_tool_call_id
                if normalized_tool_call_id.startswith(action_prefix)
                else f"{action_prefix}{normalized_tool_call_id}"
            )
            if normalized_tool_call_id
            else None
        )
        self.parent.stage(
            stage,
            status,
            summary,
            action_id=projected_action_id[:192] if projected_action_id else None,
            tool_call_id=projected_tool_call_id[:192] if projected_tool_call_id else None,
            round_id=(f"{self.agent_id}:m{round_id}" if round_id not in (None, "") else None),
            error_code=error_code,
            details=projected_details,
            user_message=user_message,
        )
        return local


__all__ = ["TeamWorkerEventBridge"]
