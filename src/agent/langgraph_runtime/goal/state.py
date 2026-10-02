"""Checkpoint-safe state and runtime context for the independent Goal graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langchain.agents.middleware.types import AgentState as LangChainAgentState
from langgraph.graph.message import add_messages

from ..state import merge_records, merge_strings


class GoalState(LangChainAgentState, total=False):
    """Only Goal-owned state channels are persisted in the Goal checkpoint."""

    messages: Annotated[list[Any], add_messages]
    engine: str
    run_id: str
    conversation_id: str
    user_text: str
    system_prompt: str
    knowledge_base_ids: list[str]
    reference_time: str
    status: str
    error_code: str | None
    terminal_detail: str
    answer_draft: str
    answer_final: str

    # Product mode identity.  These are intentionally not the Planning or
    # Team state contracts; they let the shared lifecycle identify ownership.
    agent_mode: str
    resolved_agent_mode: str
    orchestrator_mode: str

    goal_contract: dict[str, Any] | None
    goal_contract_confirmed: bool
    goal_confirmation_status: str
    goal_status: str
    goal_action: dict[str, Any] | None
    goal_action_status: str
    goal_last_observation: dict[str, Any] | None
    goal_assessment: dict[str, Any] | None
    goal_progress: str
    goal_criteria: list[dict[str, Any]]
    goal_blocker: str
    goal_terminal_reason: str
    goal_current_action_id: str
    goal_pending_confirmation_criteria: list[str]
    goal_started_at: str
    goal_iterations: int
    goal_replan_count: int
    goal_iteration_limit: int
    goal_replan_limit: int
    goal_tool_call_limit: int
    goal_model_call_limit: int
    goal_action_validation_repair_limit: int
    goal_action_validation_repairs: int
    goal_evidence_ids: Annotated[list[str], merge_strings]
    goal_history: Annotated[list[dict[str, Any]], merge_records]

    # Generic platform evidence facts consumed by the existing terminal
    # publisher and run explorer.  Goal owns how they are produced.
    tool_results: Annotated[list[dict[str, Any]], merge_records]
    evidence: Annotated[list[dict[str, Any]], merge_records]
    claim_evidence: list[dict[str, Any]]
    completed_tool_call_ids: Annotated[list[str], merge_strings]
    approved_tool_call_ids: Annotated[list[str], merge_strings]
    rejected_tool_call_ids: Annotated[list[str], merge_strings]
    runtime_errors: Annotated[list[dict[str, Any]], merge_records]
    tool_call_count: Annotated[int, operator.add]
    model_turn_count: Annotated[int, operator.add]
    pending_interrupt: dict[str, Any] | None


class GoalGraphInput(TypedDict, total=False):
    """Fresh-turn fields accepted by GoalGraph."""

    messages: list[Any]
    engine: str
    run_id: str
    conversation_id: str
    user_text: str
    system_prompt: str
    knowledge_base_ids: list[str]
    reference_time: str
    status: str
    error_code: str | None
    terminal_detail: str
    answer_draft: str
    answer_final: str
    agent_mode: str
    resolved_agent_mode: str
    orchestrator_mode: str
    goal_contract: dict[str, Any] | None
    goal_contract_confirmed: bool
    goal_confirmation_status: str
    goal_status: str
    goal_action: dict[str, Any] | None
    goal_action_status: str
    goal_last_observation: dict[str, Any] | None
    goal_assessment: dict[str, Any] | None
    goal_progress: str
    goal_criteria: list[dict[str, Any]]
    goal_blocker: str
    goal_terminal_reason: str
    goal_current_action_id: str
    goal_pending_confirmation_criteria: list[str]
    goal_started_at: str
    goal_iterations: int
    goal_replan_count: int
    goal_iteration_limit: int
    goal_replan_limit: int
    goal_tool_call_limit: int
    goal_model_call_limit: int
    goal_action_validation_repair_limit: int
    goal_action_validation_repairs: int
    goal_evidence_ids: list[str]
    goal_history: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    claim_evidence: list[dict[str, Any]]
    completed_tool_call_ids: list[str]
    approved_tool_call_ids: list[str]
    rejected_tool_call_ids: list[str]
    runtime_errors: list[dict[str, Any]]
    tool_call_count: int
    model_turn_count: int
    pending_interrupt: dict[str, Any] | None


@dataclass(frozen=True)
class GoalContext:
    """Run-scoped dependencies excluded from every Goal checkpoint."""

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
    knowledge_base_ids: tuple[str, ...] = ()
    knowledge_base_catalog: dict[str, Any] | None = None
    side_effect_lock: Any | None = None


__all__ = ["GoalContext", "GoalGraphInput", "GoalState"]
