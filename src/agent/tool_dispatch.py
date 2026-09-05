# -*- coding: utf-8 -*-
"""Typed boundary between LangGraph policy and atomic tool execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping
import threading

from src.tools.base import (
    ToolProgressUpdate,
    enforce_result_contract,
    tool_execution_context,
    tool_effect_approval,
    tool_idempotency_context,
    tool_progress_observer,
)
from src.tools.process_runner import (
    ISOLATED_TOOL_NAMES,
    STATEFUL_TOOL_NAMES,
)
from src.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolDispatchRequest:
    tool_name: str
    arguments: Mapping[str, Any]
    idempotency_key: str
    force_isolation: bool = False
    conversation_id: str | None = None
    run_id: str | None = None
    timeout_seconds: float | None = None
    approved: bool = False


@dataclass(frozen=True)
class ToolDispatchOutcome:
    """Full execution data plus the bounded card projection."""

    canonical_result: dict[str, Any]
    presentation_result: dict[str, Any]


class ToolDispatcher:
    """Execute one already policy-approved call and normalize its result."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        isolated_executor: Callable[..., Any],
        compact_result: Callable[[str, Any], Any],
        attach_fallback: Callable[[str, dict[str, Any], Any], Any],
    ) -> None:
        self._registry = registry
        self._isolated_executor = isolated_executor
        self._compact_result = compact_result
        self._attach_fallback = attach_fallback

    def execute(
        self,
        request: ToolDispatchRequest,
        *,
        cancel_event: threading.Event,
        progress_observer: Callable[[ToolProgressUpdate], None],
    ) -> ToolDispatchOutcome:
        arguments = dict(request.arguments)
        with (
            tool_progress_observer(progress_observer),
            tool_idempotency_context(request.idempotency_key),
            tool_execution_context(
                conversation_id=request.conversation_id,
                run_id=request.run_id,
            ),
            tool_effect_approval(request.approved),
        ):
            if request.tool_name in STATEFUL_TOOL_NAMES:
                raw_result = self._registry.execute(
                    request.tool_name,
                    arguments,
                )
            elif (
                request.tool_name in ISOLATED_TOOL_NAMES or request.force_isolation
            ) and self._registry.supports_isolated_execution(request.tool_name):
                raw_result = self._isolated_executor(
                    request.tool_name,
                    arguments,
                    cancel_event=cancel_event,
                    deadline_seconds=request.timeout_seconds,
                    idempotency_key=request.idempotency_key,
                    execution_context={
                        "conversation_id": request.conversation_id,
                        "run_id": request.run_id,
                    },
                    effect_approved=request.approved,
                )
            else:
                raw_result = self._registry.execute(
                    request.tool_name,
                    arguments,
                )
        canonical = self._attach_fallback(
            request.tool_name,
            arguments,
            raw_result,
        )
        # Registry execution and isolated workers both promise the same
        # object-shaped result envelope.  Do not turn a malformed scalar or a
        # compatibility-hook return value into a synthetic success: the
        # executor must record it as a failed tool observation and let the
        # source-fallback policy decide the next step.
        normalized = enforce_result_contract(request.tool_name, canonical)
        presentation = self._compact_result(
            request.tool_name,
            normalized,
        )
        if not isinstance(presentation, dict):
            presentation = {
                "success": normalized["success"],
                "result": presentation,
                "errors": normalized.get("errors", []),
                "partial": normalized.get("partial", False),
            }
        return ToolDispatchOutcome(
            canonical_result=normalized,
            presentation_result=presentation,
        )


__all__ = [
    "ToolDispatcher",
    "ToolDispatchOutcome",
    "ToolDispatchRequest",
]
