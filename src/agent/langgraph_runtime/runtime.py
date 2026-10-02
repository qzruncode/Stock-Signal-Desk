"""Lifecycle and invocation facade for the application-hosted Agent graph."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
    convert_to_messages,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command, Overwrite

from src.agent.model_runtime import (
    ModelContextWindowExceededError,
    ModelProviderReportedTimeoutError,
    ModelProviderUnavailableError,
)
from src.agent.progress import is_non_answer_agent_message
from src.agent.run_registry import active_run_registry
from src.agent.runtime_safety import get_agent_runtime_limits
from src.tools.registry import ToolRegistry

from .catalog import ToolCatalog
from .claim_evidence import (
    build_claim_evidence_ledger,
    build_structured_claim_evidence_ledger,
)
from .content_access import (
    DEFAULT_CONTENT_ACCESS_REPAIR_LIMIT,
    build_content_access_targets,
    canonical_url,
    cited_reference_access_status,
    required_content_access_targets,
    successful_content_read_urls,
)
from .events import GraphEventBridge
from .executor import AtomicToolExecutor
from .evidence_identity import prepare_answer_for_client
from .answer_contract import (
    finalize_terminal_answer,
    render_structured_answer,
    structured_answer_contract_issues,
    structured_answer_mapping,
    structured_answer_profile,
)
from .graph import DEFAULT_RESPONSE_FORMAT, build_agent_graph
from .model import GuardedAnthropicChatModel, GuardedModelGateway
from .mode_dispatch import normalize_product_mode, resolve_product_mode
from .knowledge_research import selected_document_catalog
from .planning import PLANNING_DEFAULT_REPLAN_LIMIT, resolve_planning_mode
from .state import AgentGraphInput, GraphContext
from .goal import GoalContext, build_goal_graph, goal_turn_defaults
from .team.graph import (
    build_team_graph,
)
from .team.registry import ExpertRegistry
from src.tools.base import evidence_record_is_eligible


# ``checkpoint_ns`` is reserved by LangGraph for subgraph routing and the root
# graph must use its empty namespace.  The versioned thread prefix is the
# durable engine namespace here; it prevents a checkpoint created by the
# removed semantic DAG from being resumed by this loop.
CHECKPOINT_THREAD_PREFIX = "agent-v2"
CHECKPOINT_NAMESPACE = ""
GOAL_CHECKPOINT_THREAD_PREFIX = "goal-v1"
GOAL_CHECKPOINT_NAMESPACE = ""

logger = logging.getLogger(__name__)


def _identity_compact(_tool_name: str, result: Any) -> Any:
    return result


def _identity_fallback(_tool_name: str, _arguments: dict[str, Any], result: Any) -> Any:
    return result


def _latest_turn_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the new client turn for an existing checkpoint thread.

    The HTTP layer still receives and persists the complete visible transcript.
    A native LangGraph continuation only needs the newest user turn; the
    checkpoint already owns the summarized working context and tool messages.
    """
    for index in range(len(messages) - 1, -1, -1):
        if str(messages[index].get("role") or "").strip().lower() == "user":
            return [dict(message) for message in messages[index:]]
    return [dict(message) for message in messages[-1:]]


def _checkpoint_has_messages(snapshot: Any) -> bool:
    values = getattr(snapshot, "values", None)
    if not isinstance(values, Mapping):
        return False
    messages = values.get("messages")
    return isinstance(messages, (list, tuple)) and bool(messages)


_TEAM_CHECKPOINT_STAGE_FIELDS = (
    "team_worker_handoff_status",
    "team_worker_handoff_narration_status",
    "team_failure_policy_status",
    "team_evidence_merge_status",
    "team_draft_status",
    "team_review_dispatch_status",
    "team_review_gate_status",
    "team_conflict_status",
    "team_critic_status",
    "team_bull_case_status",
    "team_bear_case_status",
    "team_consensus_status",
    "team_criteria_status",
    "team_reexecution_status",
)
_TEAM_TERMINAL_STAGE_VALUES = frozenset(
    {"completed", "failed", "cancelled", "blocked", "partial", "passed", "not_needed"}
)


def _checkpoint_is_team_state(values: Mapping[str, Any] | None) -> bool:
    """Recognize a current Team checkpoint from its canonical route fields."""
    if not isinstance(values, Mapping):
        return False
    mode = str(values.get("orchestrator_mode") or "").strip().lower()
    if mode.startswith("multi_agent"):
        return True
    route = str(values.get("orchestrator_route") or "").strip().lower()
    team_id = str(values.get("team_id") or "").strip()
    resolved_mode = str(values.get("resolved_agent_mode") or "").strip().lower()
    has_team_markers = bool(team_id or route == "team" or resolved_mode == "team")
    collaboration = values.get("collaboration")
    if (
        has_team_markers
        and isinstance(collaboration, Mapping)
        and str(collaboration.get("schema_version") or "").startswith("team.")
    ):
        return True
    return has_team_markers


def _checkpoint_is_goal_state(values: Mapping[str, Any] | None) -> bool:
    """Recognize a Goal checkpoint before materializing any graph snapshot."""
    if not isinstance(values, Mapping):
        return False
    return (
        str(values.get("agent_mode") or "").strip().lower() == "goal"
        or str(values.get("resolved_agent_mode") or "").strip().lower() == "goal"
        or str(values.get("orchestrator_mode") or "").strip().lower() == "goal_v1"
        or isinstance(values.get("goal_contract"), Mapping)
    )


def _terminal_status(current: Any, requested: str) -> str:
    """Keep a durable terminal state monotonic when cleanup races the graph."""
    normalized_requested = str(requested or "failed").strip().lower()
    if normalized_requested not in {"completed", "partial", "failed", "blocked", "cancelled"}:
        normalized_requested = "failed"
    current_status = str(current or "").strip().lower()
    if current_status in {"completed", "failed", "blocked", "cancelled"}:
        return current_status
    return normalized_requested


def _terminal_checkpoint_update(
    state: Mapping[str, Any],
    *,
    status: str,
    error_code: str,
    terminal_detail: str,
) -> dict[str, Any]:
    """Build one durable terminal update for generic, Goal, and Team state.

    ``agent_runs`` and the LangGraph checkpoint have different owners, so a
    cancellation or exception can otherwise leave the browser with a terminal
    run while the checkpoint still advertises a live Team stage. This helper
    closes the generic status, the Team stage projection, and the namespaced
    collaboration phase in the same ``aupdate_state`` call.
    """
    current_status = str(state.get("status") or "").strip().lower()
    effective_status = _terminal_status(current_status, status)
    if current_status in {"completed", "failed", "blocked", "cancelled"}:
        safe_error_code = state.get("error_code")
        safe_detail = str(state.get("terminal_detail") or "本轮任务已结束。")[:1_000]
    else:
        safe_error_code = str(error_code or "agent_runtime_failed")[:160]
        safe_detail = str(terminal_detail or "本轮任务未能完成。")[:1_000]
    update: dict[str, Any] = {
        "status": effective_status,
        "error_code": safe_error_code,
        "terminal_detail": safe_detail,
    }
    if _checkpoint_is_goal_state(state):
        goal_status = (
            "failed"
            if effective_status == "failed"
            else "blocked"
            if effective_status in {"partial", "cancelled", "blocked"}
            else effective_status
        )
        update.update(
            {
                "goal_status": goal_status,
                "goal_terminal_reason": safe_detail,
                "goal_blocker": safe_detail if goal_status != "completed" else "",
                "goal_progress": "Goal 已终止并保留当前证据。",
                "pending_interrupt": None,
            }
        )
        return update
    if not _checkpoint_is_team_state(state) or effective_status == "completed":
        return update

    current_team_status = str(state.get("team_status") or "").strip().lower()
    if current_team_status in {"completed", "failed", "blocked", "plan_rejected", "cancelled"} or current_team_status.endswith(
        "_completed"
    ):
        team_terminal_status = current_team_status
    else:
        team_terminal_status = effective_status
    update["team_status"] = team_terminal_status

    for field in _TEAM_CHECKPOINT_STAGE_FIELDS:
        current_value = str(state.get(field) or "").strip().lower()
        if current_value in _TEAM_TERMINAL_STAGE_VALUES:
            continue
        if field == "team_criteria_status":
            update[field] = "cancelled" if effective_status == "cancelled" else "blocked"
        elif field == "team_reexecution_status":
            update[field] = "cancelled" if effective_status == "cancelled" else "blocked"
        else:
            update[field] = "cancelled" if effective_status == "cancelled" else effective_status

    current_collaboration = (
        state.get("collaboration") if isinstance(state.get("collaboration"), Mapping) else {}
    )
    phase = effective_status
    update["collaboration"] = {
        "schema_version": str(current_collaboration.get("schema_version") or "team.v1"),
        "phase": phase,
        "revision": int(current_collaboration.get("revision") or 0) + 1,
        "failure": {
            "status": team_terminal_status,
            "error_code": safe_error_code,
            "detail": safe_detail,
            "phase": phase,
        },
    }
    return update


def _reset_turn_state() -> dict[str, Any]:
    """Shared run-local defaults; use native Overwrite for reducer channels."""
    return {
        "tool_results": Overwrite([]),
        "evidence": Overwrite([]),
        "claim_evidence": [],
        "completed_tool_call_ids": Overwrite([]),
        "approved_tool_call_ids": Overwrite([]),
        "rejected_tool_call_ids": Overwrite([]),
        "tool_call_count": Overwrite(0),
        "model_turn_count": Overwrite(0),
        "evidence_repair_count": Overwrite(0),
        "content_access_repair_count": Overwrite(0),
        "response_repair_count": Overwrite(0),
        "fallback_repair_count": Overwrite(0),
        "source_fallback_attempts": Overwrite([]),
        "runtime_errors": Overwrite([]),
        "work_budget_exhausted": False,
        "work_budget_detail": "",
        "content_access_targets": [],
        "required_content_reads": [],
        "pending_content_reads": [],
        "content_selection_feedback": "",
        "content_access_feedback": "",
        "evidence_feedback": "",
        "evidence_repair_answer": None,
        "fallback_feedback": "",
        "response_format_feedback": "",
        "pending_interrupt": None,
        "structured_answer": None,
        "structured_answer_call_id": "",
        "reflection_status": "not_started",
        "reflection_feedback": "",
        "reflection_review": None,
        "reflection_fallback_answer": None,
        "reflection_round": 0,
        "reflection_call_count": 0,
        "reflection_revision_count": 0,
        "planning_enabled": False,
        "planning_mode": "direct",
        "planning_status": "not_started",
        "planning_plan": None,
        "planning_revision": 0,
        "planning_current_step_id": "",
        "planning_active_tool_call_ids": [],
        "planning_step_reports": [],
        "planning_updates": [],
        "planning_replan_count": 0,
        "planning_replan_limit": PLANNING_DEFAULT_REPLAN_LIMIT,
        "planning_model_call_count": 0,
        "planning_original_structured_output_required": False,
        "planning_error": "",
        "planning_decision": None,
        "planning_step_attempts": 0,
        "planning_no_progress_attempts": 0,
        "planning_step_tool_call_ids": [],
        "planning_feedback": "",
        "agent_mode": "auto",
        "resolved_agent_mode": "",
        "orchestrator_mode": "direct_agent_loop",
        "team_id": "",
        "orchestrator_route": "",
        "orchestrator_route_reason": "",
        "orchestrator_execution_strategy": "",
        "team_status": "not_started",
        "team_plan": None,
        "team_plan_source": "",
        "team_plan_error": "",
        "team_tasks": [],
        "team_current_task": None,
        "team_current_task_attempt": 0,
        "team_dispatched_task_ids": [],
        "team_ready_task_ids": [],
        "team_dispatch_round": 0,
        # ``team_task_attempts`` uses a reducer while a single Team run is
        # fanning out parallel workers.  A new user turn still has to start
        # with a clean retry budget; otherwise the reducer merges the previous
        # run's counters into this turn and a fresh Team can skip workers or
        # start at attempt-2.  ``Overwrite`` clears it only at the turn
        # boundary; subsequent Send branches continue to merge task-local
        # counters normally.
        "team_task_attempts": Overwrite({}),
        "team_worker_handoff_status": "not_started",
        "team_worker_handoff_task_ids": [],
        "team_worker_handoff_error": "",
        "team_worker_handoff_narration_status": "not_started",
        "team_worker_handoff_narration_error": "",
        "team_failure_policy_action": "",
        "team_failure_policy_task_ids": [],
        "team_failure_policy_status": "not_started",
        "team_failure_policy_error": "",
        "team_results": Overwrite([]),
        "team_evidence_catalog": [],
        "team_evidence_merge": None,
        "team_evidence_merge_status": "not_started",
        "team_draft": None,
        "team_draft_status": "not_started",
        "team_draft_error": "",
        "team_review_dispatch_status": "not_started",
        "team_review_dispatch_error": "",
        "team_review_gate_status": "not_started",
        "team_review_gate_error": "",
        "team_conflict_assessment": None,
        "team_conflict_status": "not_started",
        "team_conflict_error": "",
        "team_critic_review": None,
        "team_critic_status": "not_started",
        "team_critic_error": "",
        "team_bull_case_review": None,
        "team_bull_case_status": "not_started",
        "team_bull_case_error": "",
        "team_bear_case_review": None,
        "team_bear_case_status": "not_started",
        "team_bear_case_error": "",
        "team_consensus": None,
        "team_consensus_status": "not_started",
        "team_consensus_error": "",
        "team_criteria_assessment": None,
        "team_criteria_status": "not_started",
        "team_criteria_error": "",
        "team_reexecution": None,
        "team_reexecution_status": "not_started",
        "team_reexecution_round": 0,
        "team_reexecution_task_ids": [],
        "team_reexecution_error": "",
        "team_contract_call_count": Overwrite(0),
        "team_worker_count": 0,
        "team_completed_worker_count": 0,
        # The collaboration reducer intentionally merges parallel reports
        # inside one run.  It must not carry reports/tasks from the previous
        # conversation turn into a new Team plan, so clear the namespace at
        # the same boundary as the other run-local channels.
        "collaboration": Overwrite({
            "schema_version": "team.v1",
            "phase": "not_started",
            "revision": 0,
            "plan": None,
            "tasks": [],
            "reports": {},
            "review": {},
            "reexecution": {},
        }),
        "answer_draft": "",
        "answer_final": "",
        "terminal_detail": "",
        "error_code": None,
    }


def _checkpoint_cleanup_for_continuation(snapshot: Any) -> list[RemoveMessage]:
    """Remove an interrupted AI tool-call turn before appending new input.

    A cancelled tool node can leave its AIMessage checkpointed without all of
    the corresponding ToolMessage results.  LangChain's message reducer
    requires those pairs to be complete on the next model call, so use the
    native RemoveMessage operation to remove that incomplete turn atomically
    with the next user message.
    """
    values = getattr(snapshot, "values", None)
    raw_messages = values.get("messages") if isinstance(values, Mapping) else None
    if not isinstance(raw_messages, (list, tuple)):
        return []
    try:
        messages = list(convert_to_messages(raw_messages))
    except (TypeError, ValueError):
        return []

    answered_tool_call_ids = {
        str(message.tool_call_id or "").strip()
        for message in messages
        if isinstance(message, ToolMessage) and str(message.tool_call_id or "").strip()
    }
    remove_ids: list[str] = []
    for index, message in enumerate(messages):
        if not isinstance(message, AIMessage) or not message.tool_calls:
            continue
        pending = {
            str(call.get("id") or "").strip()
            for call in message.tool_calls
            if str(call.get("id") or "").strip()
        } - answered_tool_call_ids
        if not pending:
            continue
        # Remove the incomplete model turn and any partial tool-node writes
        # after it, while retaining a following user message if one exists.
        for trailing in messages[index:]:
            if isinstance(trailing, HumanMessage):
                break
            message_id = str(getattr(trailing, "id", "") or "").strip()
            if message_id and message_id not in remove_ids:
                remove_ids.append(message_id)
        break
    return [RemoveMessage(id=message_id) for message_id in remove_ids]


def _postgres_connection_string(value: str) -> str:
    normalized = value.strip()
    for driver in (
        "postgresql+psycopg://",
        "postgresql+psycopg2://",
        "postgresql+asyncpg://",
    ):
        if normalized.startswith(driver):
            return "postgresql://" + normalized[len(driver) :]
    return normalized


def _sqlite_checkpoint_path(database_url: str) -> Path:
    configured = str(os.getenv("AGENT_CHECKPOINT_SQLITE_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    value = database_url.strip()
    if value.startswith("sqlite:///"):
        raw_path = value[len("sqlite:///") :]
        if raw_path and raw_path != ":memory:":
            source = Path(raw_path).expanduser()
            if not source.is_absolute():
                source = (Path.cwd() / source).resolve()
            return source.with_name(f"{source.stem}.langgraph{source.suffix or '.sqlite3'}")
    return (Path.cwd() / ".agent-checkpoints" / "langgraph.sqlite3").resolve()


def _checkpoint_id(config: Mapping[str, Any] | None) -> str | None:
    """Return a checkpoint id without exposing the rest of a runnable config."""
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    value = str(configurable.get("checkpoint_id") or "").strip()
    return value or None


def _checkpoint_task_summary(task: Any) -> dict[str, Any]:
    """Project Pregel tasks to safe lifecycle hints for diagnostics."""
    interrupts = getattr(task, "interrupts", ()) or ()
    error = getattr(task, "error", None)
    return {
        "id": str(getattr(task, "id", "") or "") or None,
        "name": str(getattr(task, "name", "") or "") or None,
        "interrupt_count": len(interrupts),
        "has_error": error is not None,
        "error_type": type(error).__name__ if error is not None else None,
    }


def _checkpoint_summary(snapshot: Any) -> dict[str, Any]:
    """Build a bounded, read-only summary of one LangGraph checkpoint.

    The full values remain in the native saver.  The diagnostic endpoint only
    needs enough information to identify a graph revision and understand where
    it stopped; returning raw messages/tool payloads here would duplicate the
    conversation and could expose sensitive tool arguments.
    """
    values = getattr(snapshot, "values", {})
    values = dict(values) if isinstance(values, Mapping) else {}
    metadata = getattr(snapshot, "metadata", {})
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    writes = metadata.get("writes")
    write_keys = (
        sorted(str(key) for key in writes)
        if isinstance(writes, Mapping)
        else []
    )
    messages = values.get("messages")
    tool_results = values.get("tool_results")
    evidence = values.get("evidence")
    tasks = getattr(snapshot, "tasks", ()) or ()
    created_at = getattr(snapshot, "created_at", None)
    return {
        "checkpoint_id": _checkpoint_id(getattr(snapshot, "config", None)),
        "parent_checkpoint_id": _checkpoint_id(getattr(snapshot, "parent_config", None)),
        "created_at": str(created_at) if created_at is not None else None,
        "metadata": {
            "source": str(metadata.get("source") or "") or None,
            "step": metadata.get("step"),
            "write_keys": write_keys,
        },
        "next": [str(item) for item in (getattr(snapshot, "next", ()) or ())],
        "tasks": [_checkpoint_task_summary(task) for task in tasks],
        "state": {
            "run_id": str(values.get("run_id") or "") or None,
            "conversation_id": str(values.get("conversation_id") or "") or None,
            "status": str(values.get("status") or "") or None,
            "message_count": len(messages) if isinstance(messages, (list, tuple)) else 0,
            "tool_result_count": len(tool_results) if isinstance(tool_results, (list, tuple)) else 0,
            "evidence_count": len(evidence) if isinstance(evidence, (list, tuple)) else 0,
            "model_turn_count": int(values.get("model_turn_count") or 0),
            "tool_call_count": int(values.get("tool_call_count") or 0),
            "evidence_repair_count": int(values.get("evidence_repair_count") or 0),
            "content_access_repair_count": int(values.get("content_access_repair_count") or 0),
            "response_repair_count": int(values.get("response_repair_count") or 0),
            "response_repair_limit": int(values.get("response_repair_limit") or 0),
            "fallback_repair_count": int(values.get("fallback_repair_count") or 0),
            "fallback_repair_limit": int(values.get("fallback_repair_limit") or 0),
            "source_fallback_attempt_count": len(values.get("source_fallback_attempts") or []),
            "runtime_error_count": len(values.get("runtime_errors") or []),
            "reflection_status": str(values.get("reflection_status") or "not_started"),
            "reflection_round": int(values.get("reflection_round") or 0),
            "reflection_call_count": int(values.get("reflection_call_count") or 0),
            "reflection_revision_count": int(values.get("reflection_revision_count") or 0),
            "planning_enabled": bool(values.get("planning_enabled")),
            "planning_mode": str(values.get("planning_mode") or "direct"),
            "agent_mode": str(values.get("agent_mode") or "direct"),
            "resolved_agent_mode": str(values.get("resolved_agent_mode") or "") or None,
            "planning_status": str(values.get("planning_status") or "not_started"),
            "planning_revision": int(values.get("planning_revision") or 0),
            "planning_current_step_id": str(values.get("planning_current_step_id") or "") or None,
            "planning_replan_count": int(values.get("planning_replan_count") or 0),
            "planning_step_count": len(values.get("planning_plan", {}).get("steps") or [])
            if isinstance(values.get("planning_plan"), Mapping)
            else 0,
            "goal_status": str(values.get("goal_status") or "not_started"),
            "goal_revision": int(
                values.get("goal_contract", {}).get("revision") or 0
            )
            if isinstance(values.get("goal_contract"), Mapping)
            else 0,
            "goal_iteration": int(values.get("goal_iterations") or 0),
            "goal_replan_count": int(values.get("goal_replan_count") or 0),
            "goal_criterion_count": len(values.get("goal_criteria") or []),
            "goal_evidence_count": len(values.get("goal_evidence_ids") or []),
            "goal_blocker": str(values.get("goal_blocker") or "") or None,
            "required_content_read_count": len(values.get("required_content_reads") or []),
            "pending_content_read_count": len(values.get("pending_content_reads") or []),
            "has_pending_interrupt": isinstance(values.get("pending_interrupt"), Mapping),
            "has_answer": bool(str(values.get("answer_final") or values.get("answer_draft") or "").strip()),
        },
    }


def _evidence_repair_limit() -> int:
    try:
        return max(0, min(8, int(str(os.getenv("AGENT_EVIDENCE_REPAIR_LIMIT") or "2").strip())))
    except ValueError:
        return 2


def _content_access_repair_limit() -> int:
    try:
        return max(
            0,
            min(
                8,
                int(
                    str(os.getenv("AGENT_CONTENT_ACCESS_REPAIR_LIMIT") or DEFAULT_CONTENT_ACCESS_REPAIR_LIMIT)
                    .strip()
                ),
            ),
        )
    except ValueError:
        return DEFAULT_CONTENT_ACCESS_REPAIR_LIMIT


def _response_repair_limit() -> int:
    """Bound provider attempts to satisfy the application answer contract."""
    try:
        return max(
            0,
            min(8, int(str(os.getenv("AGENT_RESPONSE_REPAIR_LIMIT") or "1").strip())),
        )
    except ValueError:
        return 1


def _fallback_repair_limit() -> int:
    """Bound each source recovery to search plus, when needed, one body read."""
    try:
        return max(
            0,
            min(2, int(str(os.getenv("AGENT_SOURCE_FALLBACK_REPAIR_LIMIT") or "2").strip())),
        )
    except ValueError:
        return 2


@dataclass(frozen=True)
class GraphRunResult:
    status: str
    final_text: str
    state: dict[str, Any]
    stage_history: list[dict[str, Any]] | None = None
    interrupted: bool = False
    pending_interrupt: dict[str, Any] | None = None
    error_code: str | None = None


class LangGraphRuntimeManager:
    """Own independent product graphs and one native checkpointer."""

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        expert_registry: ExpertRegistry | None = None,
        compact_result: Callable[[str, Any], Any] = _identity_compact,
        attach_fallback: Callable[[str, dict[str, Any], Any], Any] = _identity_fallback,
        response_format: Any | None = DEFAULT_RESPONSE_FORMAT,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.expert_registry = expert_registry or ExpertRegistry.default()
        self.catalog = ToolCatalog(self.registry)
        self.compact_result = compact_result
        self.attach_fallback = attach_fallback
        self.response_format = response_format
        self.checkpointer: Any | None = None
        self.graph: Any | None = None
        self.team_graph: Any | None = None
        self.goal_graph: Any | None = None
        self._checkpointer_context: AbstractAsyncContextManager[Any] | None = None
        self._backend = "uninitialized"

    @property
    def initialized(self) -> bool:
        # Keep the existing readiness contract stable for health checks and
        # embedded tests.  Goal is required only when a Goal request selects
        # the independent graph via ``_require_goal_graph``.
        return self.graph is not None

    @property
    def backend(self) -> str:
        return self._backend

    def configure_tool_projection(
        self,
        *,
        compact_result: Callable[[str, Any], Any],
        attach_fallback: Callable[[str, dict[str, Any], Any], Any] | None = None,
    ) -> None:
        self.compact_result = compact_result
        if attach_fallback is not None:
            self.attach_fallback = attach_fallback

    async def start(
        self,
        database: Any | None = None,
        *,
        testing: bool = False,
        checkpointer: Any | None = None,
    ) -> None:
        if self.graph is not None and self.team_graph is not None and self.goal_graph is not None:
            return
        if checkpointer is not None:
            self.checkpointer = checkpointer
            self._backend = type(checkpointer).__name__
        elif testing:
            self.checkpointer = InMemorySaver()
            self._backend = "memory"
        else:
            configured_url = str(os.getenv("AGENT_CHECKPOINT_DATABASE_URL") or "").strip()
            database_url = configured_url or str(
                getattr(database, "_db_url", None) or os.getenv("DATABASE_URL") or ""
            ).strip()
            if database_url.startswith(("postgresql://", "postgres://", "postgresql+")):
                context = AsyncPostgresSaver.from_conn_string(_postgres_connection_string(database_url))
                self._checkpointer_context = context
                self.checkpointer = await context.__aenter__()
                await self.checkpointer.setup()
                self._backend = "postgres"
            else:
                if configured_url.startswith("sqlite:///"):
                    sqlite_path = Path(configured_url[len("sqlite:///") :]).expanduser().resolve()
                elif configured_url and "://" not in configured_url:
                    sqlite_path = Path(configured_url).expanduser().resolve()
                else:
                    sqlite_path = _sqlite_checkpoint_path(database_url)
                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
                context = AsyncSqliteSaver.from_conn_string(str(sqlite_path))
                self._checkpointer_context = context
                self.checkpointer = await context.__aenter__()
                await self.checkpointer.setup()
                self._backend = f"sqlite:{sqlite_path}"
        self.graph = build_agent_graph(
            checkpointer=self.checkpointer,
            registry=self.registry,
            response_format=self.response_format,
        )
        self.team_graph = build_team_graph(
            checkpointer=self.checkpointer,
            registry=self.registry,
            expert_registry=self.expert_registry,
            response_format=self.response_format,
        )
        self.goal_graph = build_goal_graph(checkpointer=self.checkpointer)

    async def close(self) -> None:
        self.graph = None
        self.team_graph = None
        self.goal_graph = None
        self.checkpointer = None
        context = self._checkpointer_context
        self._checkpointer_context = None
        if context is not None:
            await context.__aexit__(None, None, None)
        self._backend = "uninitialized"

    @staticmethod
    def thread_id(conversation_id: str) -> str:
        return f"{CHECKPOINT_THREAD_PREFIX}:{conversation_id}"

    @staticmethod
    def goal_thread_id(conversation_id: str) -> str:
        return f"{GOAL_CHECKPOINT_THREAD_PREFIX}:{conversation_id}"

    @classmethod
    def graph_config(cls, conversation_id: str, *, mode: str = "standard") -> dict[str, Any]:
        limits = get_agent_runtime_limits()
        is_goal = str(mode or "standard").strip().lower() == "goal"
        return {
            "configurable": {
                "thread_id": cls.goal_thread_id(conversation_id) if is_goal else cls.thread_id(conversation_id),
                "checkpoint_ns": GOAL_CHECKPOINT_NAMESPACE if is_goal else CHECKPOINT_NAMESPACE,
            },
            # This guards the number of graph transitions, never the duration
            # of a provider's reasoning.  Tool/provider budgets remain the
            # actual work limits.
            "recursion_limit": max(1_000, min(10_000, limits.max_tool_calls * 4 + 32)),
        }

    def _require_graph(self) -> Any:
        if self.graph is None:
            raise RuntimeError("LangGraph runtime has not been initialized")
        return self.graph

    def _require_team_graph(self) -> Any:
        if self.team_graph is None:
            raise RuntimeError("LangGraph multi-agent runtime has not been initialized")
        return self.team_graph

    def _require_goal_graph(self) -> Any:
        if self.goal_graph is None:
            raise RuntimeError("Independent Goal runtime has not been initialized")
        return self.goal_graph

    def _graph_mode(self, graph: Any) -> str:
        return "goal" if graph is self.goal_graph else "standard"

    def _graph_config(self, conversation_id: str, graph: Any) -> dict[str, Any]:
        return self.graph_config(conversation_id, mode=self._graph_mode(graph))

    async def _graph_for_checkpoint(self, conversation_id: str) -> Any:
        """Select the graph that owns the durable checkpointed run.

        Goal uses a separate thread namespace.  Inspect raw checkpoint values
        before materializing any graph so a pending Goal or Team checkpoint is
        never probed through the wrong StateGraph.
        """
        checkpointer = self.checkpointer
        if checkpointer is not None and hasattr(checkpointer, "aget_tuple"):
            try:
                goal_tuple = await checkpointer.aget_tuple(
                    self.graph_config(conversation_id, mode="goal")
                )
                standard_tuple = await checkpointer.aget_tuple(
                    self.graph_config(conversation_id, mode="standard")
                )
            except Exception as exc:
                raise RuntimeError("无法读取当前运行的 LangGraph checkpoint") from exc

            def channel_values(checkpoint_tuple: Any) -> Mapping[str, Any] | None:
                checkpoint = getattr(checkpoint_tuple, "checkpoint", None)
                values = checkpoint.get("channel_values") if isinstance(checkpoint, Mapping) else None
                return values if isinstance(values, Mapping) else None

            goal_values = channel_values(goal_tuple)
            standard_values = channel_values(standard_tuple)
            goal_active = str(goal_values.get("status") or "").lower() in {
                "running", "interrupted", "waiting_for_user", "replanning"
            } if goal_values else False
            standard_active = str(standard_values.get("status") or "").lower() in {
                "running", "interrupted", "waiting_for_user", "replanning"
            } if standard_values else False
            if (
                goal_values is not None
                and _checkpoint_is_goal_state(goal_values)
                and (goal_active or not standard_active)
            ):
                return self._require_goal_graph()
            if standard_values is not None:
                if _checkpoint_is_team_state(standard_values):
                    return self._require_team_graph()
                return self._require_graph()
            if goal_values is not None and _checkpoint_is_goal_state(goal_values):
                return self._require_goal_graph()

        # Test and embedded runtimes may expose graph state without a native
        # checkpointer. Resolve the current graph from its canonical state.
        goal_graph = self.goal_graph
        if goal_graph is not None:
            try:
                goal_snapshot = await goal_graph.aget_state(
                    self.graph_config(conversation_id, mode="goal")
                )
            except Exception:
                goal_snapshot = None
            if _checkpoint_is_goal_state(getattr(goal_snapshot, "values", None)):
                values = getattr(goal_snapshot, "values", {}) or {}
                if str(values.get("status") or "").lower() in {
                    "running", "interrupted", "waiting_for_user", "replanning"
                } or self.graph is None:
                    return goal_graph

        standard_config = self.graph_config(conversation_id, mode="standard")
        team_graph = self.team_graph
        if team_graph is not None and team_graph is not self.graph:
            try:
                team_snapshot = await team_graph.aget_state(standard_config)
            except Exception:
                team_snapshot = None
            if _checkpoint_is_team_state(getattr(team_snapshot, "values", None)):
                return team_graph

        return self._require_graph()

    async def _checkpoint_knowledge_base_ids(self, graph: Any, conversation_id: str) -> list[str]:
        """Restore the server-owned retrieval scope when an Agent run resumes."""
        try:
            snapshot = await graph.aget_state(self._graph_config(conversation_id, graph))
        except Exception:
            return []
        values = getattr(snapshot, "values", None)
        raw_ids = values.get("knowledge_base_ids") if isinstance(values, Mapping) else None
        if not isinstance(raw_ids, (list, tuple)):
            return []
        return list(dict.fromkeys(str(item).strip() for item in raw_ids if str(item).strip()))[:8]

    async def replace_checkpoint_messages(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """Synchronize a visible transcript branch into the native checkpoint.

        The UI may delete or edit a turn without immediately starting a new
        model run.  Reset the message reducer and the run-local channels
        together so the next continuation cannot resurrect the old branch.
        """
        graph = await self._graph_for_checkpoint(conversation_id)
        config = self._graph_config(conversation_id, graph)
        snapshot = await graph.aget_state(config)
        if not _checkpoint_has_messages(snapshot):
            return
        if graph is self.goal_graph:
            update = {
                **goal_turn_defaults(),
                "messages": [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    *convert_to_messages(messages),
                ],
                "status": "idle",
            }
            await graph.aupdate_state(config, update)
            return
        update: dict[str, Any] = {
            **_reset_turn_state(),
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *convert_to_messages(messages),
            ],
            "conversation_context": None,
            "status": "idle",
        }
        await graph.aupdate_state(config, update)

    def _context(
        self,
        *,
        llm_config: Mapping[str, Any],
        database: Any | None,
        controller: Any | None,
        run_id: str,
        conversation_id: str,
        run_attempt: int,
        tenant_id: str,
        owner_id: str,
        knowledge_base_ids: Sequence[str] = (),
        model: Any | None = None,
        executor: Any | None = None,
    ) -> GraphContext:
        events = GraphEventBridge(controller, run_id=run_id)
        if model is None:
            gateway = GuardedModelGateway(
                llm_config=llm_config,
                database=database,
                run_id=run_id,
                worker_id=active_run_registry.worker_id,
            )
            from src.agent.usage import PersistedUsageCallback

            model_config = dict(llm_config)
            try:
                context_window = int(model_config.get("context_window") or 0)
            except (TypeError, ValueError):
                context_window = 0
            model_client: Any = GuardedAnthropicChatModel(
                gateway=gateway,
                llm_config=model_config,
                callbacks=[PersistedUsageCallback(database, run_id)],
                profile=(
                    {"max_input_tokens": context_window}
                    if context_window > 0
                    else None
                ),
            )
        else:
            model_client = model
        tool_executor = executor or AtomicToolExecutor(
            self.registry,
            database=database,
            run_id=run_id,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            knowledge_base_ids=knowledge_base_ids,
            # The bridge owns the ordered assistant-stream projection while a
            # LangGraph run is active.  LangGraph state remains the checkpoint
            # and recovery source; it is not replayed into a second UI stream.
            controller=events,
            events=events,
            compact_result=self.compact_result,
            attach_fallback=self.attach_fallback,
        )
        return GraphContext(
            model=model_client,
            catalog=self.catalog,
            registry=self.registry,
            executor=tool_executor,
            events=events,
            database=database,
            run_id=run_id,
            conversation_id=conversation_id,
            run_attempt=max(1, int(run_attempt)),
            tenant_id=tenant_id,
            owner_id=owner_id,
            knowledge_base_ids=tuple(str(item) for item in knowledge_base_ids),
            knowledge_base_catalog=selected_document_catalog(
                database, knowledge_base_ids, tenant_id=tenant_id, owner_id=owner_id,
            ),
            side_effect_lock=asyncio.Lock(),
        )

    def _goal_context(self, base: GraphContext) -> GoalContext:
        """Adapt only platform dependencies into the independent Goal context."""
        return GoalContext(
            model=base.model,
            catalog=base.catalog,
            registry=base.registry,
            executor=base.executor,
            events=base.events,
            database=base.database,
            run_id=base.run_id,
            conversation_id=base.conversation_id,
            run_attempt=base.run_attempt,
            tenant_id=base.tenant_id,
            owner_id=base.owner_id,
            knowledge_base_ids=base.knowledge_base_ids,
            knowledge_base_catalog=base.knowledge_base_catalog,
            side_effect_lock=base.side_effect_lock,
        )

    async def _dispatch_product_mode(
        self,
        context: GraphContext,
        *,
        run_id: str,
        user_text: str,
    ) -> dict[str, Any]:
        """Resolve Auto before selecting the graph that owns this run.

        This is intentionally a runtime-level dispatch step.  Team and Goal
        graphs start only after this returns their respective mode; neither
        graph owns the product-level decision.
        """
        return await resolve_product_mode(
            {
                "run_id": run_id,
                "user_text": user_text,
                "agent_mode": "auto",
            },
            context,
            requested="auto",
            route_id=f"run-{run_id}:mode-dispatch",
        )

    async def _invoke_graph(
        self,
        graph: Any,
        graph_input: Any,
        *,
        conversation_id: str,
        context: GraphContext | GoalContext,
    ) -> Mapping[str, Any] | None:
        config = self._graph_config(conversation_id, graph)
        try:
            last_update: Mapping[str, Any] | None = None
            compression_recorded = False
            async for chunk in graph.astream(
                graph_input,
                config,
                context=context,
                stream_mode=["messages", "updates"],
                version="v2",
                # Preserve child-graph namespaces in the v2 envelope. Team
                # workers keep their own identity in the durable event
                # contract; exposing ``ns`` here also makes native subgraph
                # streams auditable without rematerialising them as root
                # assistant prose.
                subgraphs=True,
                # A Team checkpoint is the recovery cursor for all named
                # per-invocation subgraphs. Persist each super-step before
                # advancing so a process restart cannot outrun the durable
                # parent/child checkpoint boundary.
                durability="sync",
            ):
                if not isinstance(chunk, Mapping):
                    continue
                chunk_type = str(chunk.get("type") or "")
                data = chunk.get("data")
                if chunk_type == "messages" and isinstance(data, (list, tuple)) and data:
                    message = data[0]
                    metadata = data[1] if len(data) > 1 and isinstance(data[1], Mapping) else {}
                    if metadata.get("lc_source") == "summarization":
                        if not compression_recorded:
                            compression_recorded = True
                            context.events.stage(
                                "context",
                                "compressed",
                                "LangChain 上下文摘要已完成，后续模型轮次使用压缩后的上下文",
                                details={
                                    "source": "langchain_summarization_middleware",
                                    "phase": "context_compression",
                                    "message_character_count": len(
                                        str(getattr(message, "content", "") or "")
                                    ),
                                },
                            )
                        continue
                    if metadata.get("lc_source") == "planning":
                        continue
                    if isinstance(message, (AIMessageChunk, AIMessage)):
                        context.events.model_message(message)
                elif chunk_type == "updates" and isinstance(data, Mapping):
                    last_update = data
            return last_update
        except ModelProviderReportedTimeoutError:
            return await self._terminate_partial(
                graph,
                config=config,
                context=context,
                error_code="model_provider_timeout",
                message="上游模型服务返回超时；已保留已有工具观察和证据。",
            )
        except ModelProviderUnavailableError:
            return await self._terminate_partial(
                graph,
                config=config,
                context=context,
                error_code="model_provider_unavailable",
                message="模型服务暂时不可用；已保留已有工具观察和证据。",
            )
        except ModelContextWindowExceededError:
            return await self._terminate_partial(
                graph,
                config=config,
                context=context,
                error_code="model_context_window_exceeded",
                message="本轮上下文超过模型可用窗口；已保留会话记录，请缩小本轮输入后继续。",
            )
        except GraphRecursionError:
            return await self._terminate_partial(
                graph,
                config=config,
                context=context,
                error_code="agent_loop_budget_exceeded",
                message="本轮工具/循环预算已耗尽；已保留已有观察并停止继续调用。",
            )

    async def get_state_history(
        self,
        conversation_id: str,
        *,
        limit: int = 20,
        before_checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        """Read native LangGraph checkpoint history without mutating a thread.

        ``agent_runs`` remains the authority for request ownership and worker
        lifecycle.  This method deliberately reads the graph thread directly
        so diagnostics can distinguish a durable Run state from the latest
        graph revision.  It does not call ``update_state`` or execute tools.
        """
        graph = await self._graph_for_checkpoint(conversation_id)
        safe_limit = max(1, min(100, int(limit)))
        config = self._graph_config(conversation_id, graph)
        before_config: Mapping[str, Any] | None = None
        normalized_before = str(before_checkpoint_id or "").strip()
        if normalized_before:
            before_config = self._graph_config(conversation_id, graph)
            before_config["configurable"]["checkpoint_id"] = normalized_before

        snapshots: list[dict[str, Any]] = []
        async for snapshot in graph.aget_state_history(
            config,
            before=before_config,
            limit=safe_limit + 1,
        ):
            snapshots.append(_checkpoint_summary(snapshot))

        has_more = len(snapshots) > safe_limit
        items = snapshots[:safe_limit]
        current = await graph.aget_state(config)
        return {
            "conversation_id": conversation_id,
            "thread_id": self.goal_thread_id(conversation_id) if graph is self.goal_graph else self.thread_id(conversation_id),
            "current_checkpoint_id": _checkpoint_id(getattr(current, "config", None)),
            "checkpoint_authority": "langgraph_checkpointer",
            "run_lifecycle_authority": "agent_runs",
            "read_only": True,
            "items": items,
            "has_more": has_more,
            "next_before_checkpoint_id": (
                items[-1].get("checkpoint_id") if has_more and items else None
            ),
        }

    async def _terminate_partial(
        self,
        graph: Any,
        *,
        config: Mapping[str, Any],
        context: GraphContext,
        error_code: str,
        message: str,
    ) -> Mapping[str, Any]:
        snapshot = await graph.aget_state(config)
        state = dict(snapshot.values or {})
        structured_answer = structured_answer_mapping(state.get("structured_answer"))
        accepted_answer = str(state.get("answer_final") or "").strip()
        answer_draft = str(state.get("answer_draft") or "").strip()
        answer = accepted_answer or answer_draft
        factual_evidence = [
            item
            for item in state.get("evidence") or []
            if evidence_record_is_eligible(item)
            and str(item.get("effect") or "read") != "side_effect"
        ]
        tool_results = [
            item for item in state.get("tool_results") or [] if isinstance(item, Mapping)
        ]
        rejected_candidate = False
        if not answer and structured_answer:
            answer = render_structured_answer(
                structured_answer,
                factual_evidence,
                tool_results,
            )
        if structured_answer and not accepted_answer:
            candidate_issues = structured_answer_contract_issues(
                structured_answer,
                user_text=state.get("user_text"),
            )
            candidate_ledger = build_structured_claim_evidence_ledger(
                structured_answer.get("blocks") or [],
                factual_evidence,
                tool_results,
                profile=structured_answer_profile(structured_answer),
            )
            candidate_issues.extend(
                str(item) for item in candidate_ledger.get("issues") or []
            )
            if state.get("knowledge_base_ids"):
                from src.rag.citations import validate_pdf_page_references

                candidate_issues.extend(
                    "PDF页码未被关联的知识库命中覆盖"
                    for _ in validate_pdf_page_references(
                        structured_answer.get("blocks") or [],
                        factual_evidence,
                    )
                )
            if candidate_issues:
                # answer_draft can hold an unaccepted candidate while the
                # middleware is asking the model to repair it. A provider
                # timeout in that repair must not promote the draft to a
                # user-visible terminal answer.
                rejected_candidate = True
                structured_answer = None
                answer = (
                    "检索已完成，但模型服务未能返回完整且通过校验的答案；"
                    "未发布不完整内容。"
                )
            else:
                answer = render_structured_answer(
                    structured_answer,
                    factual_evidence,
                    tool_results,
                )
        elif answer_draft and not accepted_answer and not structured_answer:
            draft_issues = structured_answer_contract_issues(
                {
                    "profile": "research",
                    "blocks": [{"kind": "fact", "content": answer_draft}],
                },
                user_text=state.get("user_text"),
            )
            if draft_issues:
                rejected_candidate = True
                answer = (
                    "模型服务未能返回符合要求的完整答案；"
                    "未发布不完整内容。"
                )
        if is_non_answer_agent_message(answer):
            answer = ""
        answer, _ = prepare_answer_for_client(answer, factual_evidence)
        content_targets, pending_content_reads = build_content_access_targets(
            tool_results=state.get("tool_results") or [],
            existing_targets=state.get("content_access_targets") or [],
        )
        required_content_reads = required_content_access_targets(
            answer=answer,
            evidence=state.get("evidence") or [],
            tool_results=state.get("tool_results") or [],
            targets=content_targets,
        )
        cited_access = cited_reference_access_status(
            answer=answer,
            evidence=state.get("evidence") or [],
            tool_results=state.get("tool_results") or [],
            targets=content_targets,
        )
        selection_required = [
            access
            for access in cited_access.values()
            if access.get("selection_required") is True
        ]
        target_urls = {canonical_url(item.get("url")) for item in content_targets}
        successful_urls = successful_content_read_urls(state.get("tool_results") or []) & target_urls
        required_pending = [
            item
            for item in required_content_reads
            if canonical_url(item.get("url")) not in successful_urls
        ]
        pending_urls = {canonical_url(item.get("url")) for item in required_pending}
        pending_content_reads = [
            *required_pending,
            *[
                item
                for item in pending_content_reads
                if canonical_url(item.get("url")) not in pending_urls
            ],
        ]
        terminal_error_code = error_code
        terminal_detail = message
        if pending_content_reads or selection_required:
            terminal_error_code = "content_access_incomplete"
            terminal_detail = (
                "正文取证未完成："
                + message
                + "以上结论仅基于来源链接、标题/摘要或结构化字段，正文未核验"
            )
            answer = finalize_terminal_answer(
                answer or "已获取来源索引，但本轮未完成正文读取。",
                status="partial",
                error_code=terminal_error_code,
                detail=terminal_detail,
            )
        else:
            answer = finalize_terminal_answer(
                answer,
                status="partial",
                error_code=terminal_error_code,
                detail=terminal_detail,
            )
        claim_evidence = (
            build_claim_evidence_ledger(
                answer,
                factual_evidence,
                [
                    item
                    for item in state.get("tool_results") or []
                    if isinstance(item, Mapping)
                ],
            ).get("claims")
            if factual_evidence
            else []
        )
        update = {
            "answer_final": answer,
        }
        if rejected_candidate:
            update.update(
                {
                    "answer_draft": answer,
                    "structured_answer": None,
                    "claim_evidence": [],
                }
            )
        update.update(
            _terminal_checkpoint_update(
                state,
                status="partial",
                error_code=terminal_error_code,
                terminal_detail=terminal_detail,
            )
        )
        if structured_answer:
            update["structured_answer"] = structured_answer
        if factual_evidence and not rejected_candidate:
            if structured_answer:
                update["claim_evidence"] = list(
                    build_structured_claim_evidence_ledger(
                        structured_answer.get("blocks") or [],
                        factual_evidence,
                        tool_results,
                        profile=structured_answer_profile(structured_answer),
                    ).get("claims")
                    or []
                )
            else:
                update["claim_evidence"] = list(claim_evidence or [])
        if content_targets or pending_content_reads:
            update.update(
                {
                    "content_access_targets": content_targets,
                    "required_content_reads": required_content_reads,
                    "pending_content_reads": pending_content_reads,
                    "content_access_feedback": "",
                }
            )
        try:
            await graph.aupdate_state(config, update)
        except Exception:
            pass
        context.events.close_open_stages(
            status="failed",
            reason=terminal_detail,
            error_code=terminal_error_code,
        )
        context.events.stage(
            "publish",
            "failed",
            terminal_detail,
            error_code=terminal_error_code,
        )
        if answer:
            context.events.text(answer)
        return {**state, **update}

    async def finalize_checkpoint(
        self,
        conversation_id: str,
        *,
        status: str,
        error_code: str,
        terminal_detail: str,
    ) -> dict[str, Any]:
        """Close a checkpoint after cancellation or an exception.

        The background runner owns the durable ``agent_runs`` transaction, but
        cleanup can happen after LangGraph has written a checkpoint and before
        the normal terminal node runs.  Marking only ``agent_runs`` in that
        window leaves the native checkpoint advertising a live Team stage.
        Keep both authorities at the same terminal boundary without replaying
        a graph node or any tool.
        """
        try:
            graph = await self._graph_for_checkpoint(conversation_id)
        except Exception:
            return {}
        config = self._graph_config(conversation_id, graph)
        try:
            snapshot = await graph.aget_state(config)
            state = dict(snapshot.values or {})
        except Exception:
            return {}
        if not state:
            return state
        update = _terminal_checkpoint_update(
            state,
            status=status,
            error_code=error_code,
            terminal_detail=terminal_detail,
        )
        try:
            await graph.aupdate_state(config, update)
            snapshot = await graph.aget_state(config)
            return dict(snapshot.values or {**state, **update})
        except Exception:
            return {**state, **update}

    async def run_new(
        self,
        *,
        messages: list[dict[str, Any]],
        user_text: str,
        system_prompt: str,
        llm_config: Mapping[str, Any],
        database: Any | None,
        controller: Any | None,
        run_id: str,
        conversation_id: str,
        run_attempt: int,
        tenant_id: str,
        owner_id: str,
        knowledge_base_ids: Sequence[str] = (),
        model: Any | None = None,
        executor: Any | None = None,
        history_mode: str = "auto",
        planning_mode: str = "direct",
        agent_mode: str | None = None,
    ) -> GraphRunResult:
        requested_agent_mode = normalize_product_mode(agent_mode)
        base_context = self._context(
            llm_config=llm_config,
            database=database,
            controller=controller,
            run_id=run_id,
            conversation_id=conversation_id,
            run_attempt=run_attempt,
            tenant_id=tenant_id,
            owner_id=owner_id,
            knowledge_base_ids=knowledge_base_ids,
            model=model,
            executor=executor,
        )
        selected_knowledge_bases = tuple(
            dict.fromkeys(str(item).strip() for item in knowledge_base_ids if str(item).strip())
        )
        # Auto routing is a Runtime concern and therefore always uses the
        # shared platform context.  Only after the owning graph is selected
        # may a Goal run receive its independent GoalContext adapter.
        context: GraphContext | GoalContext = base_context
        route_state: dict[str, Any] = {}
        effective_agent_mode = requested_agent_mode
        if requested_agent_mode == "auto":
            # This is deliberately outside ``build_team_graph``.  Auto makes
            # one structured product-level decision, then the selected graph
            # owns the rest of the run and its checkpoint lifecycle.
            route_state = await self._dispatch_product_mode(
                context,
                run_id=run_id,
                user_text=user_text,
            )
            effective_agent_mode = str(route_state.get("resolved_agent_mode") or "").strip().lower()
            if effective_agent_mode not in {"direct", "plan", "team", "goal"}:
                # Route resolution is fail-closed.  Never silently execute a
                # different product mode after the dispatcher fails.
                failure_state = {
                    **_reset_turn_state(),
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                    "user_text": user_text,
                    "agent_mode": "auto",
                    "resolved_agent_mode": "",
                    "orchestrator_mode": "direct_agent_loop",
                    "orchestrator_route": "failed",
                    "orchestrator_route_reason": str(
                        route_state.get("orchestrator_route_reason") or "Auto 模式路由失败"
                    ),
                    "status": "failed",
                    "error_code": str(route_state.get("error_code") or "orchestrator_route_failed"),
                    "terminal_detail": str(
                        route_state.get("terminal_detail") or "产品模式路由未能完成，本轮未执行 Agent。"
                    ),
                }
                try:
                    standard_graph = self._require_graph()
                    await standard_graph.aupdate_state(
                        self._graph_config(conversation_id, standard_graph), failure_state
                    )
                    return await self._result(
                        {},
                        graph=standard_graph,
                        conversation_id=conversation_id,
                        events=context.events,
                    )
                except Exception:
                    # A route failure before a graph has a checkpoint must
                    # still produce a terminal API result.  The normal path
                    # persists it through the shared checkpointer above.
                    return GraphRunResult(
                        status="failed",
                        final_text="",
                        state=failure_state,
                        stage_history=context.events.stage_history,
                        interrupted=False,
                        pending_interrupt=None,
                        error_code=str(failure_state["error_code"]),
                    )
        if effective_agent_mode == "team":
            graph = self._require_team_graph()
        elif effective_agent_mode == "goal":
            graph = self._require_goal_graph()
        else:
            graph = self._require_graph()
        if graph is self.goal_graph:
            context = self._goal_context(base_context)

        normalized_history_mode = str(history_mode or "auto").strip().lower()
        graph_config = self._graph_config(conversation_id, graph)
        checkpoint = await graph.aget_state(graph_config)
        continue_checkpoint = (
            _checkpoint_has_messages(checkpoint)
            and normalized_history_mode not in {"replace", "branch", "reset"}
        )
        graph_messages = _latest_turn_messages(messages) if continue_checkpoint else [dict(item) for item in messages]
        checkpoint_cleanup = (
            _checkpoint_cleanup_for_continuation(checkpoint)
            if continue_checkpoint
            else []
        )
        limits = get_agent_runtime_limits()
        resolved_planning_mode = (
            "planned"
            if effective_agent_mode == "plan"
            else "direct"
            if effective_agent_mode in {"direct", "goal"}
            else resolve_planning_mode(user_text, planning_mode)
        )
        team_enabled = effective_agent_mode == "team"
        resolved_agent_mode = effective_agent_mode
        if effective_agent_mode == "goal":
            goal_context = self._goal_context(context)
            goal_input: Any = {
                **goal_turn_defaults(tool_call_limit=limits.max_tool_calls),
                "run_id": run_id,
                "conversation_id": conversation_id,
                "user_text": user_text,
                "system_prompt": system_prompt,
                "knowledge_base_ids": list(selected_knowledge_bases),
                "reference_time": datetime.now().astimezone().isoformat(),
                "agent_mode": requested_agent_mode,
                "resolved_agent_mode": "goal",
                "orchestrator_mode": "goal_v1",
                "messages": (
                    [*checkpoint_cleanup, *convert_to_messages(graph_messages)]
                    if continue_checkpoint
                    else Overwrite(convert_to_messages(graph_messages))
                ),
            }
            output = await self._invoke_graph(
                graph,
                goal_input,
                conversation_id=conversation_id,
                context=goal_context,
            )
            return await self._result(
                output,
                graph=graph,
                conversation_id=conversation_id,
                events=context.events,
            )
        input_state: AgentGraphInput = {
            **_reset_turn_state(),
            **({} if continue_checkpoint else {"conversation_context": None}),
            "messages": (
                [*checkpoint_cleanup, *convert_to_messages(graph_messages)]
                if continue_checkpoint
                else Overwrite(convert_to_messages(graph_messages))
            ),
            "engine": "langgraph_agent_loop",
            # Team owns its collaboration plan; Plan remains on the native
            # PlanningCoordinator path.
            "planning_enabled": (not team_enabled) and resolved_planning_mode != "direct",
            "planning_mode": resolved_planning_mode if not team_enabled else "direct",
            "planning_status": "not_started",
            "planning_replan_limit": PLANNING_DEFAULT_REPLAN_LIMIT,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "user_text": user_text,
            "system_prompt": system_prompt,
            "knowledge_base_ids": list(selected_knowledge_bases),
            "reference_time": datetime.now().astimezone().isoformat(),
            "tool_call_limit": limits.max_tool_calls,
            "evidence_repair_limit": _evidence_repair_limit(),
            "content_access_repair_limit": _content_access_repair_limit(),
            "response_repair_limit": _response_repair_limit(),
            "fallback_repair_limit": _fallback_repair_limit(),
            "structured_output_required": self.response_format is not None,
            "orchestrator_mode": "multi_agent_team" if team_enabled else "direct_agent_loop",
            "agent_mode": requested_agent_mode,
            "resolved_agent_mode": resolved_agent_mode,
            "team_id": f"team-{run_id}"[:96] if team_enabled else "",
            "orchestrator_route": str(route_state.get("orchestrator_route") or ""),
            "orchestrator_route_reason": str(route_state.get("orchestrator_route_reason") or ""),
            "orchestrator_execution_strategy": str(
                route_state.get("orchestrator_execution_strategy") or ""
            ),
            "team_status": str(route_state.get("team_status") or "not_started"),
            "team_contract_call_count": int(route_state.get("team_contract_call_count") or 0),
            "status": "running",
        }
        output = await self._invoke_graph(
            graph,
            input_state,
            conversation_id=conversation_id,
            context=context,
        )
        return await self._result(
            output,
            graph=graph,
            conversation_id=conversation_id,
            events=context.events,
        )

    async def resume(
        self,
        *,
        interrupt_id: str,
        decision: Mapping[str, Any],
        llm_config: Mapping[str, Any],
        database: Any | None,
        controller: Any | None,
        run_id: str,
        conversation_id: str,
        run_attempt: int,
        tenant_id: str,
        owner_id: str,
        model: Any | None = None,
        executor: Any | None = None,
    ) -> GraphRunResult:
        graph = await self._graph_for_checkpoint(conversation_id)
        knowledge_base_ids = await self._checkpoint_knowledge_base_ids(graph, conversation_id)
        base_context = self._context(
            llm_config=llm_config,
            database=database,
            controller=controller,
            run_id=run_id,
            conversation_id=conversation_id,
            run_attempt=run_attempt,
            tenant_id=tenant_id,
            owner_id=owner_id,
            knowledge_base_ids=knowledge_base_ids,
            model=model,
            executor=executor,
        )
        context: GraphContext | GoalContext = (
            self._goal_context(base_context) if graph is self.goal_graph else base_context
        )
        output = await self._invoke_graph(
            graph,
            Command(resume={str(interrupt_id): dict(decision)}),
            conversation_id=conversation_id,
            context=context,
        )
        return await self._result(
            output,
            graph=graph,
            conversation_id=conversation_id,
            events=context.events,
        )

    async def recover(
        self,
        *,
        llm_config: Mapping[str, Any],
        database: Any | None,
        controller: Any | None,
        run_id: str,
        conversation_id: str,
        run_attempt: int,
        tenant_id: str,
        owner_id: str,
        model: Any | None = None,
        executor: Any | None = None,
    ) -> GraphRunResult:
        graph = await self._graph_for_checkpoint(conversation_id)
        knowledge_base_ids = await self._checkpoint_knowledge_base_ids(graph, conversation_id)
        base_context = self._context(
            llm_config=llm_config,
            database=database,
            controller=controller,
            run_id=run_id,
            conversation_id=conversation_id,
            run_attempt=run_attempt,
            tenant_id=tenant_id,
            owner_id=owner_id,
            knowledge_base_ids=knowledge_base_ids,
            model=model,
            executor=executor,
        )
        context: GraphContext | GoalContext = (
            self._goal_context(base_context) if graph is self.goal_graph else base_context
        )
        output = await self._invoke_graph(graph, None, conversation_id=conversation_id, context=context)
        return await self._result(
            output,
            graph=graph,
            conversation_id=conversation_id,
            events=context.events,
        )

    async def _result(
        self,
        output: Mapping[str, Any] | None,
        *,
        graph: Any,
        conversation_id: str,
        events: GraphEventBridge,
    ) -> GraphRunResult:
        raw_output = dict(output or {})
        interrupts = list(raw_output.pop("__interrupt__", []) or [])
        snapshot = await graph.aget_state(self._graph_config(conversation_id, graph))
        state = dict(snapshot.values or raw_output)
        if not interrupts:
            # ``astream(version="v2")`` may expose an interrupt inside an
            # updates chunk rather than as the return value of ``ainvoke``.
            # The checkpointer is the authoritative source for both forms.
            for task in getattr(snapshot, "tasks", ()) or ():
                interrupts.extend(getattr(task, "interrupts", ()) or ())
        if interrupts:
            item = interrupts[0]
            value = dict(getattr(item, "value", {}) or {})
            pending = {
                "interrupt_id": str(getattr(item, "id", "")),
                **value,
                "created_at": datetime.now().astimezone().isoformat(),
            }
            # Emit only when the native graph actually suspends. Code before
            # interrupt() is replayed on resume and must not append UI prose.
            events.stage(
                "approval", "started",
                f"{value.get('tool_name')} 会产生外部副作用，正在等待用户批准",
                action_id=value.get("action_id"), tool_call_id=value.get("action_id"),
                user_message="这一步可能产生外部影响，我会先等你确认后再继续。",
                details={"tool_name": value.get("tool_name"), "arguments": value.get("arguments")},
            )
            events.approval_required(pending)
            return GraphRunResult(
                status="interrupted",
                final_text=str(state.get("answer_final") or state.get("answer_draft") or ""),
                state=state,
                stage_history=events.stage_history,
                interrupted=True,
                pending_interrupt=pending,
                error_code=None,
            )
        return GraphRunResult(
            status=str(state.get("status") or "failed"),
            final_text=str(state.get("answer_final") or state.get("answer_draft") or ""),
            state=state,
            stage_history=events.stage_history,
            interrupted=False,
            pending_interrupt=None,
            error_code=(str(state.get("error_code")) if state.get("error_code") else None),
        )

    async def pending_interrupt(self, conversation_id: str) -> dict[str, Any] | None:
        graph = await self._graph_for_checkpoint(conversation_id)
        snapshot = await graph.aget_state(self._graph_config(conversation_id, graph))
        for task in snapshot.tasks:
            for item in getattr(task, "interrupts", ()) or ():
                value = dict(getattr(item, "value", {}) or {})
                return {"interrupt_id": str(getattr(item, "id", "")), **value}
        return None

    async def get_state(self, conversation_id: str) -> dict[str, Any]:
        graph = await self._graph_for_checkpoint(conversation_id)
        snapshot = await graph.aget_state(self._graph_config(conversation_id, graph))
        return dict(snapshot.values or {})

    async def has_checkpoint(self, conversation_id: str, *, run_id: str | None = None) -> bool:
        graph = await self._graph_for_checkpoint(conversation_id)
        snapshot = await graph.aget_state(self._graph_config(conversation_id, graph))
        state = dict(snapshot.values or {})
        if not snapshot.config or not state:
            return False
        if run_id is not None:
            return str(state.get("run_id") or "") == str(run_id)
        return True

    async def delete_thread(self, conversation_id: str) -> None:
        if self.checkpointer is None:
            return
        await self.checkpointer.adelete_thread(self.thread_id(conversation_id))
        await self.checkpointer.adelete_thread(self.goal_thread_id(conversation_id))


agent_graph_runtime = LangGraphRuntimeManager()


__all__ = [
    "CHECKPOINT_NAMESPACE",
    "CHECKPOINT_THREAD_PREFIX",
    "GraphRunResult",
    "LangGraphRuntimeManager",
    "agent_graph_runtime",
]
