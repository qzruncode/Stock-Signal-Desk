"""Checkpoint-safe state for the message-and-tool Agent runtime.

The graph itself is intentionally supplied by :func:`langchain.agents.create_agent`.
There are no task-specific planning, verification, or workflow contracts in this
module: the durable state is only the conversation, the observed tool work, and
the server-owned operational controls around it.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langchain.agents.middleware.types import AgentState as LangChainAgentState


def merge_records(
    current: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Append tool observations while making durable replay idempotent."""
    merged = [dict(item) for item in current or []]
    positions = {
        str(item.get("id") or item.get("action_id") or item.get("tool_call_id") or ""): index
        for index, item in enumerate(merged)
        if str(item.get("id") or item.get("action_id") or item.get("tool_call_id") or "")
    }
    for raw in incoming or []:
        item = dict(raw)
        key = str(item.get("id") or item.get("action_id") or item.get("tool_call_id") or "")
        if key and key in positions:
            merged[positions[key]] = item
        else:
            if key:
                positions[key] = len(merged)
            merged.append(item)
    return merged


def merge_strings(
    current: list[str] | None,
    incoming: list[str] | None,
) -> list[str]:
    """Keep a compact, stable set of completed or approved call identifiers."""
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*(current or []), *(incoming or [])]:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)
    return merged


class AgentState(LangChainAgentState, total=False):
    """Serializable state owned by the shared LangGraph checkpointer.

    ``messages`` is inherited from LangChain's state and uses its standard
    message reducer.  Runtime clients, registry objects, locks, database
    handles, and cancellation controls are deliberately held in
    :class:`GraphContext`, not here.
    """

    engine: str
    run_id: str
    conversation_id: str
    user_text: str
    system_prompt: str
    reference_time: str
    # Small, durable reference context resolved from a successful read tool.
    # Large member collections remain in the tool observation and are fetched
    # page-by-page when the model needs them.
    conversation_context: dict[str, Any] | None

    # One record per actual model tool call, not a precompiled action plan.
    tool_results: Annotated[list[dict[str, Any]], merge_records]
    evidence: Annotated[list[dict[str, Any]], merge_records]
    # Replaced for each candidate/final answer.  This is a durable, generic
    # fact-to-evidence audit ledger, not a task plan or business workflow.
    claim_evidence: list[dict[str, Any]]
    completed_tool_call_ids: Annotated[list[str], merge_strings]
    approved_tool_call_ids: Annotated[list[str], merge_strings]
    rejected_tool_call_ids: Annotated[list[str], merge_strings]

    # Server-owned budgets.  These bound work, never model reasoning time.
    tool_call_count: Annotated[int, operator.add]
    model_turn_count: Annotated[int, operator.add]
    evidence_repair_count: Annotated[int, operator.add]
    tool_call_limit: int
    evidence_repair_limit: int
    work_budget_exhausted: bool
    work_budget_detail: str

    # Feedback injected into the next model turn when deterministic evidence
    # checks find a repairable issue.
    evidence_feedback: str
    pending_interrupt: dict[str, Any] | None
    answer_draft: str
    answer_final: str
    status: str
    error_code: str | None


class AgentGraphInput(TypedDict, total=False):
    """Fresh-turn fields that overwrite transient run state before invocation."""

    messages: list[Any]
    run_id: str
    conversation_id: str
    user_text: str
    system_prompt: str
    reference_time: str
    conversation_context: dict[str, Any] | None
    engine: str
    tool_results: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    claim_evidence: list[dict[str, Any]]
    completed_tool_call_ids: list[str]
    approved_tool_call_ids: list[str]
    rejected_tool_call_ids: list[str]
    tool_call_count: int
    model_turn_count: int
    evidence_repair_count: int
    tool_call_limit: int
    evidence_repair_limit: int
    work_budget_exhausted: bool
    work_budget_detail: str
    evidence_feedback: str
    pending_interrupt: dict[str, Any] | None
    answer_draft: str
    answer_final: str
    status: str
    error_code: str | None


@dataclass(frozen=True)
class GraphContext:
    """Run-scoped dependencies excluded from every checkpoint."""

    model: Any
    catalog: Any
    registry: Any
    executor: Any
    events: Any
    database: Any | None
    run_id: str
    conversation_id: str
    run_attempt: int
    tenant_id: str
    owner_id: str
    side_effect_lock: Any | None = None


__all__ = [
    "AgentGraphInput",
    "AgentState",
    "GraphContext",
    "merge_records",
    "merge_strings",
]
