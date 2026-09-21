"""Checkpoint-safe state for the native Agent loop and Planning coordinator.

The graph is still supplied by :func:`langchain.agents.create_agent`. Planning
adds only checkpoint-safe coordination fields; tool execution, evidence, and
terminal publication remain owned by the existing runtime contracts.
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


def merge_team_records(
    current: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge parallel worker results by task identity.

    ``Send`` may replay a completed branch after a checkpoint resume.  Replacing
    the same task record instead of appending it keeps the team trace stable
    and prevents duplicate findings from reaching synthesis.
    """
    merged = [dict(item) for item in current or []]
    positions = {
        str(item.get("task_id") or item.get("agent_id") or item.get("id") or ""): index
        for index, item in enumerate(merged)
        if str(item.get("task_id") or item.get("agent_id") or item.get("id") or "")
    }
    for raw in incoming or []:
        item = dict(raw)
        key = str(item.get("task_id") or item.get("agent_id") or item.get("id") or "")
        if key and key in positions:
            merged[positions[key]] = item
        else:
            if key:
                positions[key] = len(merged)
            merged.append(item)
    return merged


def merge_team_attempts(
    current: dict[str, int] | None,
    incoming: dict[str, int] | None,
) -> dict[str, int]:
    """Merge parallel task attempt counters by taking the greatest attempt.

    Independent ``Send`` branches update different task ids in the same
    super-step.  A plain dictionary channel would make those updates collide;
    this reducer keeps the checkpoint deterministic and remains safe when a
    completed branch is replayed after recovery.
    """
    merged = {str(key): max(0, int(value or 0)) for key, value in (current or {}).items()}
    for key, value in (incoming or {}).items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        merged[normalized_key] = max(merged.get(normalized_key, 0), max(0, int(value or 0)))
    return merged


def merge_collaboration(
    current: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge the namespaced Team projection without losing parallel reports.

    LangGraph ``Send`` branches update the same parent state in one
    super-step.  A plain mapping channel would let the last branch overwrite
    the others, which is exactly how a parallel Team becomes one misleading
    timeline.  Keep scalar phase fields last-write-wins, but merge task/report
    collections by their stable ids.
    """
    merged = dict(current or {})
    for key, value in (incoming or {}).items():
        if key in {"tasks", "reports", "messages", "events"}:
            if key == "reports":
                existing = dict(merged.get(key) or {})
                if isinstance(value, dict):
                    existing.update({str(item): record for item, record in value.items()})
                merged[key] = existing
                continue
            existing_items = list(merged.get(key) or [])
            if isinstance(value, (list, tuple)):
                by_id = {
                    str(item.get("task_id") or item.get("event_id") or item.get("message_id") or item.get("id") or index): index
                    for index, item in enumerate(existing_items)
                    if isinstance(item, dict)
                }
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("task_id") or item.get("event_id") or item.get("message_id") or item.get("id") or "")
                    if item_id and item_id in by_id:
                        existing_items[by_id[item_id]] = dict(item)
                    else:
                        if item_id:
                            by_id[item_id] = len(existing_items)
                        existing_items.append(dict(item))
                merged[key] = existing_items
            continue
        merged[key] = value
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

    # Planning is an optional coordinator around the generic Agent loop. These
    # fields are server-owned projections of the plan and its observations; they
    # never contain hidden chain-of-thought.
    planning_enabled: bool
    planning_mode: str
    planning_status: str
    planning_plan: dict[str, Any] | None
    planning_revision: int
    planning_current_step_id: str
    planning_active_tool_call_ids: list[str]
    planning_step_reports: list[dict[str, Any]]
    planning_updates: list[dict[str, Any]]
    planning_replan_count: int
    planning_replan_limit: int
    planning_model_call_count: int
    planning_original_structured_output_required: bool
    planning_error: str
    planning_decision: dict[str, Any] | None
    planning_step_attempts: int
    planning_step_tool_call_ids: list[str]
    planning_feedback: str

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
    content_access_repair_count: Annotated[int, operator.add]
    response_repair_count: Annotated[int, operator.add]
    fallback_repair_count: Annotated[int, operator.add]
    tool_call_limit: int
    evidence_repair_limit: int
    content_access_repair_limit: int
    response_repair_limit: int
    fallback_repair_limit: int
    work_budget_exhausted: bool
    work_budget_detail: str

    # Feedback injected into the next model turn when deterministic evidence
    # checks find a repairable issue.
    evidence_feedback: str
    # A bounded recovery for one failed source, started at the native tools
    # join. While set, ModelRequest requires the owned web-search/read turn.
    fallback_feedback: str
    # One bounded recovery record per failed external read operation.  This is
    # reducer-backed so parallel Team workers cannot overwrite one another.
    source_fallback_attempts: Annotated[list[dict[str, Any]], merge_records]
    # Runtime failures are durable control-plane facts, never model prose.
    runtime_errors: Annotated[list[dict[str, Any]], merge_records]
    # Complete reference candidates plus the subset of model-selected URLs
    # whose source body still needs a successful read.
    content_access_targets: list[dict[str, Any]]
    # Links belonging to reference-only tool calls cited by the candidate
    # answer; this is scoped per cited tool action, not to the whole run.
    required_content_reads: list[dict[str, Any]]
    pending_content_reads: list[dict[str, Any]]
    # A proactive reference-selection turn is separate from the terminal
    # content-access recovery feedback.  The former lets the runtime ask the
    # model which candidate URLs matter before it submits an answer.
    content_selection_feedback: str
    content_access_feedback: str
    # Feedback used when the provider returns a plain/invalid answer after the
    # application has required the native structured response contract.
    response_format_feedback: str
    pending_interrupt: dict[str, Any] | None
    # Plain checkpoint-safe copy of LangChain's structured_response.  This
    # typed mapping is the application publication contract and is what
    # terminal code consumes.
    structured_answer: dict[str, Any] | None
    # The output-tool call that produced ``structured_answer``.  LangChain's
    # built-in ``structured_response`` channel is intentionally retained by
    # the graph across retries; this run-local identity prevents an older
    # structured response from being mistaken for the current model turn.
    structured_answer_call_id: str
    # Whether this graph instance must finish through the configured
    # LangChain response format.  The built-in structured_response channel is
    # retained by the native graph for routing, so this application flag also
    # prevents a stale response from allowing a plain-text terminal path.
    structured_output_required: bool
    answer_draft: str
    answer_final: str
    # Semantic Reflection is a bounded publish-time review for research
    # answers with judgment-bearing blocks.  The review itself is checkpoint
    # safe and contains only a redacted projection.
    reflection_status: str
    reflection_feedback: str
    reflection_review: dict[str, Any] | None
    reflection_round: int
    reflection_call_count: int
    reflection_revision_count: int
    # Human-readable reason shared by graph guards and the terminal publisher.
    terminal_detail: str
    status: str
    error_code: str | None

    # Multi-agent coordination is an optional parent graph around the native
    # Agent loop. These fields are compact projections; worker transcripts are
    # kept in per-invocation subgraphs that inherit the parent checkpointer
    # under LangGraph's task namespace rather than copied into parent state.
    # User-selected product mode: auto, direct, plan, or team.
    agent_mode: str
    # Effective route after Auto is resolved; empty while Auto is still routing.
    resolved_agent_mode: str
    orchestrator_mode: str
    team_id: str
    orchestrator_route: str
    orchestrator_route_reason: str
    orchestrator_execution_strategy: str
    team_status: str
    team_plan: dict[str, Any] | None
    team_plan_source: str
    team_plan_error: str
    team_tasks: list[dict[str, Any]]
    team_current_task: dict[str, Any] | None
    team_current_task_attempt: int
    team_previous_result: dict[str, Any] | None
    team_repair_instructions: list[str]
    team_dispatched_task_ids: list[str]
    team_ready_task_ids: list[str]
    team_dispatch_round: int
    team_task_attempts: Annotated[dict[str, int], merge_team_attempts]
    team_worker_handoff_status: str
    team_worker_handoff_task_ids: list[str]
    team_worker_handoff_incomplete_task_ids: list[str]
    team_worker_handoff_error: str
    team_worker_handoff_narration_status: str
    team_worker_handoff_narration_error: str
    team_failure_policy_action: str
    team_failure_policy_task_ids: list[str]
    team_failure_policy_status: str
    team_failure_policy_error: str
    team_results: Annotated[list[dict[str, Any]], merge_team_records]
    team_evidence_catalog: list[dict[str, Any]]
    team_evidence_merge: dict[str, Any] | None
    team_evidence_merge_status: str
    team_draft: dict[str, Any] | None
    team_draft_status: str
    team_draft_error: str
    team_review_dispatch_status: str
    team_review_dispatch_error: str
    team_review_gate_status: str
    team_review_gate_error: str
    team_conflict_assessment: dict[str, Any] | None
    team_conflict_status: str
    team_conflict_error: str
    team_critic_review: dict[str, Any] | None
    team_critic_status: str
    team_critic_error: str
    team_bull_case_review: dict[str, Any] | None
    team_bull_case_status: str
    team_bull_case_error: str
    team_bear_case_review: dict[str, Any] | None
    team_bear_case_status: str
    team_bear_case_error: str
    team_consensus: dict[str, Any] | None
    team_consensus_status: str
    team_consensus_error: str
    # Independent server-owned gates for worker success criteria and the Team
    # plan's completion criteria.  The model proposal is stored only after
    # canonicalization against the current plan and evidence catalog.
    team_criteria_assessment: dict[str, Any] | None
    team_criteria_status: str
    team_criteria_error: str
    team_reexecution: dict[str, Any] | None
    team_reexecution_status: str
    team_reexecution_round: int
    team_reexecution_task_ids: list[str]
    team_reexecution_error: str
    team_contract_call_count: Annotated[int, operator.add]
    team_worker_count: int
    team_completed_worker_count: int
    collaboration: Annotated[dict[str, Any], merge_collaboration]


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
    planning_enabled: bool
    planning_mode: str
    planning_status: str
    planning_plan: dict[str, Any] | None
    planning_revision: int
    planning_current_step_id: str
    planning_active_tool_call_ids: list[str]
    planning_step_reports: list[dict[str, Any]]
    planning_updates: list[dict[str, Any]]
    planning_replan_count: int
    planning_replan_limit: int
    planning_model_call_count: int
    planning_original_structured_output_required: bool
    planning_error: str
    planning_decision: dict[str, Any] | None
    planning_step_attempts: int
    planning_step_tool_call_ids: list[str]
    planning_feedback: str
    tool_results: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    claim_evidence: list[dict[str, Any]]
    completed_tool_call_ids: list[str]
    approved_tool_call_ids: list[str]
    rejected_tool_call_ids: list[str]
    tool_call_count: int
    model_turn_count: int
    evidence_repair_count: int
    content_access_repair_count: int
    response_repair_count: int
    fallback_repair_count: int
    tool_call_limit: int
    evidence_repair_limit: int
    content_access_repair_limit: int
    response_repair_limit: int
    fallback_repair_limit: int
    work_budget_exhausted: bool
    work_budget_detail: str
    evidence_feedback: str
    fallback_feedback: str
    source_fallback_attempts: list[dict[str, Any]]
    runtime_errors: list[dict[str, Any]]
    content_access_targets: list[dict[str, Any]]
    required_content_reads: list[dict[str, Any]]
    pending_content_reads: list[dict[str, Any]]
    content_selection_feedback: str
    content_access_feedback: str
    response_format_feedback: str
    pending_interrupt: dict[str, Any] | None
    structured_answer: dict[str, Any] | None
    structured_answer_call_id: str
    structured_output_required: bool
    answer_draft: str
    answer_final: str
    reflection_status: str
    reflection_feedback: str
    reflection_review: dict[str, Any] | None
    reflection_round: int
    reflection_call_count: int
    reflection_revision_count: int
    terminal_detail: str
    status: str
    error_code: str | None

    agent_mode: str
    resolved_agent_mode: str
    orchestrator_mode: str
    team_id: str
    orchestrator_route: str
    orchestrator_route_reason: str
    orchestrator_execution_strategy: str
    team_status: str
    team_plan: dict[str, Any] | None
    team_plan_source: str
    team_plan_error: str
    team_tasks: list[dict[str, Any]]
    team_current_task: dict[str, Any] | None
    team_current_task_attempt: int
    team_previous_result: dict[str, Any] | None
    team_repair_instructions: list[str]
    team_dispatched_task_ids: list[str]
    team_ready_task_ids: list[str]
    team_dispatch_round: int
    team_task_attempts: dict[str, int]
    team_worker_handoff_status: str
    team_worker_handoff_task_ids: list[str]
    team_worker_handoff_incomplete_task_ids: list[str]
    team_worker_handoff_error: str
    team_worker_handoff_narration_status: str
    team_worker_handoff_narration_error: str
    team_failure_policy_action: str
    team_failure_policy_task_ids: list[str]
    team_failure_policy_status: str
    team_failure_policy_error: str
    team_results: list[dict[str, Any]]
    team_evidence_catalog: list[dict[str, Any]]
    team_evidence_merge: dict[str, Any] | None
    team_evidence_merge_status: str
    team_draft: dict[str, Any] | None
    team_draft_status: str
    team_draft_error: str
    team_review_dispatch_status: str
    team_review_dispatch_error: str
    team_review_gate_status: str
    team_review_gate_error: str
    team_conflict_assessment: dict[str, Any] | None
    team_conflict_status: str
    team_conflict_error: str
    team_critic_review: dict[str, Any] | None
    team_critic_status: str
    team_critic_error: str
    team_bull_case_review: dict[str, Any] | None
    team_bull_case_status: str
    team_bull_case_error: str
    team_bear_case_review: dict[str, Any] | None
    team_bear_case_status: str
    team_bear_case_error: str
    team_consensus: dict[str, Any] | None
    team_consensus_status: str
    team_consensus_error: str
    team_criteria_assessment: dict[str, Any] | None
    team_criteria_status: str
    team_criteria_error: str
    team_reexecution: dict[str, Any] | None
    team_reexecution_status: str
    team_reexecution_round: int
    team_reexecution_task_ids: list[str]
    team_reexecution_error: str
    team_contract_call_count: int
    team_worker_count: int
    team_completed_worker_count: int
    collaboration: dict[str, Any]


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
    # Team workers may compile the shared web tools into their child graph so
    # a recovery turn can use the native LangGraph tool path.  Middleware hides
    # these names during normal turns and allows them only when fallback state
    # is active.
    recovery_only_tools: frozenset[str] = frozenset()
    # Reserved for a future independently hosted reviewer.  The first phase
    # uses the run model through a separate, tool-free structured call.
    reflection_model: Any | None = None
    side_effect_lock: Any | None = None


__all__ = [
    "AgentGraphInput",
    "AgentState",
    "GraphContext",
    "merge_records",
    "merge_strings",
    "merge_collaboration",
    "merge_team_attempts",
    "merge_team_records",
]
