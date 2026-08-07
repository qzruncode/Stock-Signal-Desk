"""Serializable state and model contracts for the generic Agent graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Callable, Literal, Mapping, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


def merge_records(
    current: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Append graph fan-out results while deduplicating replayed records."""
    merged: list[dict[str, Any]] = [dict(item) for item in current or []]
    positions: dict[str, int] = {}
    for index, item in enumerate(merged):
        key = str(item.get("id") or item.get("action_id") or "")
        if key:
            positions[key] = index
    for raw in incoming or []:
        item = dict(raw)
        key = str(item.get("id") or item.get("action_id") or "")
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
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*(current or []), *(incoming or [])]:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)
    return merged


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentUnderstanding(StrictContract):
    objective: str = Field(min_length=1, max_length=2_000)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    deliverable: str = Field(default="直接回答用户问题", max_length=1_000)
    search_queries: list[str] = Field(default_factory=list, max_length=8)
    needs_tools: bool = False
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_clarification(self) -> "IntentUnderstanding":
        if self.needs_clarification and not str(self.clarification_question or "").strip():
            raise ValueError("clarification_question is required when needs_clarification=true")
        return self


class ToolRanking(StrictContract):
    selected_tools: list[str] = Field(default_factory=list, max_length=8)
    supplemental_queries: list[str] = Field(default_factory=list, max_length=4)
    rationale: str = Field(default="", max_length=1_500)


class PlannedAction(StrictContract):
    action_id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")
    objective: str = Field(min_length=1, max_length=1_000)
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=16)
    expected_evidence: list[str] = Field(default_factory=list, max_length=12)


class ClarificationRequirement(StrictContract):
    """One genuinely missing model-visible required tool argument."""

    tool_name: str = Field(min_length=1, max_length=128)
    field_names: list[str] = Field(min_length=1, max_length=12)
    reason: str = Field(min_length=1, max_length=1_000)


class ActionPlan(StrictContract):
    actions: list[PlannedAction] = Field(default_factory=list, max_length=8)
    finalize_without_tools: bool = False
    clarification_question: str | None = Field(default=None, max_length=1_000)
    clarification_requirements: list[ClarificationRequirement] = Field(
        default_factory=list,
        max_length=8,
    )
    rationale: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def validate_clarification_contract(self) -> "ActionPlan":
        has_question = bool(str(self.clarification_question or "").strip())
        if has_question and not self.clarification_requirements:
            raise ValueError(
                "clarification requires concrete missing model-visible tool fields"
            )
        if self.clarification_requirements and not has_question:
            raise ValueError("clarification_requirements require clarification_question")
        return self


class ReflectionDecision(StrictContract):
    decision: Literal["discover", "replan", "finalize", "partial"]
    reason: str = Field(default="", max_length=2_000)
    search_queries: list[str] = Field(default_factory=list, max_length=6)


class ClaimAssessment(StrictContract):
    claim: str = Field(min_length=1, max_length=2_000)
    material: bool = True
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    supported: bool
    issue: str | None = Field(default=None, max_length=1_000)


class AnswerVerification(StrictContract):
    accepted: bool
    instruction_adherent: bool
    instruction_issues: list[str] = Field(default_factory=list, max_length=20)
    claims: list[ClaimAssessment] = Field(default_factory=list, max_length=80)
    missing_evidence_queries: list[str] = Field(default_factory=list, max_length=6)
    revised_answer: str | None = Field(default=None, max_length=40_000)
    summary: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def validate_instruction_assessment(self) -> "AnswerVerification":
        if not self.instruction_adherent and not self.instruction_issues:
            raise ValueError(
                "instruction_issues are required when instruction_adherent=false"
            )
        return self


class AgentGraphInput(TypedDict, total=False):
    input_run_id: str
    input_conversation_id: str
    input_messages: list[dict[str, Any]]
    input_user_text: str
    input_system_prompt: str


class AgentState(TypedDict, total=False):
    # Fresh input. These fields are overwritten by every new user turn.
    input_run_id: str
    input_conversation_id: str
    input_messages: list[dict[str, Any]]
    input_user_text: str
    input_system_prompt: str

    # Persisted, JSON-safe orchestration state.
    engine: str
    run_id: str
    conversation_id: str
    messages: list[dict[str, Any]]
    user_text: str
    system_prompt: str
    intent: dict[str, Any]
    search_queries: list[str]
    tool_candidates: list[dict[str, Any]]
    selected_tools: list[str]
    selected_tool_schemas: list[dict[str, Any]]
    plan: dict[str, Any]
    ready_read_actions: list[dict[str, Any]]
    pending_action: dict[str, Any] | None
    deferred_actions: list[dict[str, Any]]
    dispatch_action: dict[str, Any]
    tool_results: Annotated[list[dict[str, Any]], merge_records]
    evidence: Annotated[list[dict[str, Any]], merge_records]
    completed_action_ids: Annotated[list[str], merge_strings]
    reflection: dict[str, Any]
    verification: dict[str, Any]
    answer_draft: str
    answer_final: str
    status: str
    error_code: str | None
    plan_round: int
    search_expansions: int
    verification_round: int
    max_plan_rounds: int
    max_search_expansions: int
    max_verification_rounds: int
    max_elapsed_seconds: int
    budget_limits: dict[str, int]


@dataclass(frozen=True)
class GraphContext:
    """Run-scoped dependencies that must never enter a checkpoint."""

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


StructuredModelCall = Callable[..., Any]


__all__ = [
    "ActionPlan",
    "AgentGraphInput",
    "AgentState",
    "AnswerVerification",
    "ClaimAssessment",
    "ClarificationRequirement",
    "GraphContext",
    "IntentUnderstanding",
    "PlannedAction",
    "ReflectionDecision",
    "ToolRanking",
    "merge_records",
    "merge_strings",
]
