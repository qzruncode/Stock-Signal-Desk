"""Typed contracts for the native multi-agent research graph.

The coordinator owns task routing and the workers own domain observations.  A
worker never hands an unbounded prompt transcript to another worker; it hands
back one bounded, typed assessment plus server-owned evidence identifiers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


OrchestratorRouteMode = Literal["direct", "plan", "team"]
TeamReviewVerdict = Literal["pass", "revise", "block"]
TeamWorkerStatus = Literal["completed", "partial", "failed"]
TeamConfidence = Literal["high", "medium", "low", "unknown"]
EvidenceMergeStatus = Literal["completed", "partial", "failed"]
TeamFailureStrategy = Literal["partial", "retry", "replan", "abort"]
ConflictStatus = Literal["none", "conflict", "high_risk"]
ConsensusVerdict = Literal["pass", "revise", "partial", "block"]
CriteriaVerdict = Literal["pass", "fail", "unknown"]
CriteriaStatus = Literal["passed", "partial", "blocked"]
DraftStatus = Literal["completed", "partial", "blocked"]
ReexecutionStatus = Literal["not_needed", "scheduled", "blocked"]

# These are the only plan-level budget controls understood by the server. A
# model may want to describe a per-expert limit, but that belongs on the
# corresponding CollaborationTask (for example ``max_tool_calls``), not in
# this shared budget map. Keeping the key set in the contract makes the
# provider-facing schema and the server normalizer reject the same mistakes.
TEAM_BUDGET_KEYS = frozenset(
    {
        "max_tasks",
        "max_concurrency",
        "max_tool_calls",
        "max_duration_seconds",
        "max_provider_calls",
    }
)


class AgentTask(BaseModel):
    """One independently executable, read-only collaboration task.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=96)
    agent_id: str = Field(min_length=1, max_length=96)
    agent_node: str = Field(min_length=1, max_length=96)
    objective: str = Field(min_length=1, max_length=1_200)
    input_refs: list[str] = Field(default_factory=list, max_length=12)
    # Capability count is not the per-task invocation budget (max_tool_calls).
    # The registered financial expert already owns more than 16 read tools.
    allowed_tools: list[str] = Field(min_length=1, max_length=64)
    output_format: str = Field(default="结构化领域观察、限制和待确认问题", min_length=1, max_length=600)
    timeout_seconds: int = Field(default=120, ge=5, le=300)
    failure_strategy: TeamFailureStrategy = "partial"
    # ``retry`` is bounded at the task boundary.  The parent graph records the
    # attempt and never re-runs a successful sibling worker.
    max_attempts: int = Field(default=2, ge=1, le=3)
    required_evidence: list[str] = Field(default_factory=list, max_length=8)
    success_criteria: list[str] = Field(min_length=1, max_length=8)
    max_tool_calls: int = Field(default=6, ge=1, le=16)
    parallel_group: str = Field(default="research", min_length=1, max_length=64)
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    activation_reason: str = Field(default="", max_length=600)

class AgentTaskDraft(BaseModel):
    """Provider-facing task draft owned by the coordinator.

    The coordinator only proposes the domain split.  Tool permissions and
    runtime limits are filled from the server-owned expert registry after this
    draft is accepted.  Keeping this contract small avoids asking an
    OpenAI-compatible provider to serialize the whole executable task envelope
    in one deeply nested tool call.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default="", max_length=96)
    agent_id: str = Field(min_length=1, max_length=96)
    objective: str = Field(min_length=1, max_length=1_200)
    input_refs: list[str] = Field(default_factory=list, max_length=12)
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    success_criteria: list[str] = Field(min_length=1, max_length=8)
    activation_reason: str = Field(default="", max_length=600)
    # Hints prioritize capabilities within the server-owned expert scope;
    # they do not remove other registered capabilities needed by the goal.
    tool_hints: list[str] = Field(default_factory=list, max_length=16)
    failure_strategy: TeamFailureStrategy = "partial"
    max_attempts: int = Field(default=2, ge=1, le=3)


class TeamPlanDraft(BaseModel):
    """Small provider-facing Team proposal.

    ``TeamPlan`` remains the canonical server-side executable contract.  This
    draft deliberately excludes plan ids, tool permissions, budgets, graph
    nodes, and timeouts; those values are not model-authorized configuration.
    """

    model_config = ConfigDict(extra="forbid")

    progress_text: str = Field(default="", max_length=1_800)
    goal: str = Field(min_length=1, max_length=1_200)
    completion_criteria: list[str] = Field(min_length=1, max_length=8)
    tasks: list[AgentTaskDraft] = Field(min_length=2, max_length=12)
    synthesis_instructions: str = Field(default="", max_length=1_600)


class TeamPlan(BaseModel):
    """The supervisor's bounded plan for one research turn."""

    model_config = ConfigDict(extra="forbid")

    # Keep the user-facing projection first in the provider schema.  With
    # streamed tool-call arguments this lets the coordinator explain its
    # actual delegation before the larger task graph finishes serializing.
    progress_text: str = Field(default="", max_length=1_800)
    plan_id: str = Field(min_length=1, max_length=96)
    goal: str = Field(min_length=1, max_length=1_200)
    completion_criteria: list[str] = Field(min_length=1, max_length=8)
    tasks: list[AgentTask] = Field(min_length=2, max_length=12)
    synthesis_instructions: str = Field(min_length=1, max_length=1_600)
    revision: int = Field(default=1, ge=1, le=32)
    max_reexecution_rounds: int = Field(default=2, ge=0, le=5)
    budget: dict[str, int] = Field(default_factory=dict, max_length=12)

    @field_validator("budget")
    @classmethod
    def validate_budget(cls, value: dict[str, int]) -> dict[str, int]:
        unknown = sorted(set(str(key) for key in value) - TEAM_BUDGET_KEYS)
        if unknown:
            raise ValueError(f"team plan contains unsupported budget keys: {unknown}")
        for key, raw_value in value.items():
            if isinstance(raw_value, bool) or int(raw_value) < 1:
                raise ValueError(f"team budget {key} must be a positive integer")
        return value


# Names used by the PDF-oriented collaboration contract.
CollaborationTask = AgentTask
CollaborationPlan = TeamPlan


class EvidenceMerge(BaseModel):
    """Server-owned canonical evidence handoff for the review chain."""

    model_config = ConfigDict(extra="forbid")

    status: EvidenceMergeStatus
    summary: str = Field(default="", max_length=1_000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)
    worker_evidence: dict[str, list[str]] = Field(default_factory=dict, max_length=8)
    finding_evidence: dict[str, list[str]] = Field(default_factory=dict, max_length=96)
    missing_task_ids: list[str] = Field(default_factory=list, max_length=8)
    invalid_evidence_ids: list[str] = Field(default_factory=list, max_length=24)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    duplicate_count: int = Field(default=0, ge=0, le=80)


class DraftSection(BaseModel):
    """One evidence-bounded section in the coordinator's working draft."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=96)
    agent_id: str = Field(min_length=1, max_length=96)
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(default="", max_length=4_000)
    status: TeamWorkerStatus = "partial"
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)
    limitations: list[str] = Field(default_factory=list, max_length=8)


class DraftAggregation(BaseModel):
    """Typed, server-owned draft handoff before cross-worker review."""

    model_config = ConfigDict(extra="forbid")

    status: DraftStatus
    summary: str = Field(default="", max_length=1_200)
    sections: list[DraftSection] = Field(default_factory=list, max_length=12)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)
    unresolved_task_ids: list[str] = Field(default_factory=list, max_length=12)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=24)
    limitations: list[str] = Field(default_factory=list, max_length=12)


class ReexecutionDecision(BaseModel):
    """Server-owned decision describing the next targeted Team execution."""

    model_config = ConfigDict(extra="forbid")

    status: ReexecutionStatus
    round: int = Field(ge=0, le=5)
    max_rounds: int = Field(ge=0, le=5)
    task_ids: list[str] = Field(default_factory=list, max_length=12)
    reasons: list[str] = Field(default_factory=list, max_length=12)
    repair_instructions: dict[str, list[str]] = Field(default_factory=dict, max_length=12)
    next_action: str = Field(default="", max_length=600)


class ConflictIssue(BaseModel):
    """One bounded issue found while comparing worker claims and evidence."""

    model_config = ConfigDict(extra="forbid")

    category: Literal[
        "claim",
        "evidence",
        "time_scope",
        "entity_scope",
        "stance",
        "risk",
        "coverage",
        "other",
    ] = "other"
    severity: Literal["low", "medium", "high"] = "medium"
    reason: str = Field(min_length=1, max_length=700)
    task_ids: list[str] = Field(default_factory=list, max_length=8)
    # Models cite stable per-run source slots.  Canonical evidence hashes are
    # resolved by the server and remain an internal publication identifier.
    source_ids: list[int] = Field(default_factory=list, max_length=24)


class ConflictAssessment(BaseModel):
    """Typed result of the independent conflict and risk gate."""

    model_config = ConfigDict(extra="forbid")

    status: ConflictStatus
    reason: str = Field(default="", max_length=900)
    progress_text: str = Field(default="", max_length=1_200)
    issues: list[ConflictIssue] = Field(default_factory=list, max_length=12)
    risk_flags: list[str] = Field(default_factory=list, max_length=12)
    requires_adversarial_review: bool = False


class CaseReview(BaseModel):
    """One bounded adversarial stance review."""

    model_config = ConfigDict(extra="forbid")

    stance: Literal["bull", "bear"]
    summary: str = Field(min_length=1, max_length=1_600)
    progress_text: str = Field(default="", max_length=1_200)
    arguments: list[str] = Field(default_factory=list, max_length=10)
    supporting_source_ids: list[int] = Field(default_factory=list, max_length=40)
    counter_source_ids: list[int] = Field(default_factory=list, max_length=40)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    risks: list[str] = Field(default_factory=list, max_length=10)
    confidence: TeamConfidence = "unknown"


class BullCaseReview(CaseReview):
    """The positive-case reviewer contract."""

    stance: Literal["bull"] = "bull"


class BearCaseReview(CaseReview):
    """The negative-case reviewer contract."""

    stance: Literal["bear"] = "bear"


class ConsensusResolution(BaseModel):
    """Typed policy decision after the two adversarial reviews."""

    model_config = ConfigDict(extra="forbid")

    verdict: ConsensusVerdict
    conclusion: str = Field(min_length=1, max_length=1_800)
    rationale: str = Field(default="", max_length=1_200)
    progress_text: str = Field(default="", max_length=1_200)
    source_ids: list[int] = Field(default_factory=list, max_length=80)
    unresolved_conflicts: list[str] = Field(default_factory=list, max_length=12)
    confidence: TeamConfidence = "unknown"
    allow_final_answer: bool = False
    needs_replan: bool = False


class OrchestratorRoute(BaseModel):
    """Top-level execution route chosen before work is started."""

    model_config = ConfigDict(extra="forbid")

    progress_text: str = Field(default="", max_length=1_200)
    mode: OrchestratorRouteMode
    reason: str = Field(min_length=1, max_length=600)
    execution_strategy: Literal["single_agent", "team"] = "single_agent"


class WorkerAssessment(BaseModel):
    """Typed handoff from one domain worker to the coordinator."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_400)
    # The worker's user-facing projection is model-authored and scoped to its
    # own lane; it is separate from the server lifecycle summary.
    progress_text: str = Field(default="", max_length=1_800)
    findings: list[str] = Field(default_factory=list, max_length=12)
    finding_evidence_refs: list[list[str]] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    open_questions: list[str] = Field(default_factory=list, max_length=8)
    confidence: TeamConfidence = "unknown"


class CoordinatorHandoffNarration(BaseModel):
    """Model-authored narration after worker reports reach the coordinator.

    Worker reports are the structured collaboration boundary. This contract
    gives the coordinator a separate, user-facing sentence for the main
    narrative instead of leaking a worker's terminal answer into the root
    message stream. Task ids are advisory and are canonicalized by the parent
    graph before they are persisted.
    """

    model_config = ConfigDict(extra="forbid")

    progress_text: str = Field(min_length=1, max_length=1_200)
    received_task_ids: list[str] = Field(default_factory=list, max_length=12)
    pending_task_ids: list[str] = Field(default_factory=list, max_length=12)
    next_action: str = Field(default="", max_length=600)


class CriterionCheck(BaseModel):
    """One model proposal that the server will verify against evidence."""

    # Provider-added annotations (e.g. confidence) have no authority. Keep
    # only this DTO's declared fields; required verdict/index/source checks
    # are still validated below and by the server evidence gate.
    model_config = ConfigDict(extra="ignore")

    criterion_index: int = Field(ge=1, le=8)
    # The server replaces this display text with the canonical plan criterion;
    # keeping it optional makes the model contract smaller and less forgeable.
    criterion: str = Field(default="", max_length=600)
    verdict: CriteriaVerdict
    explanation: str = Field(min_length=1, max_length=800)
    source_ids: list[int] = Field(default_factory=list, max_length=80)


class CriteriaAssessment(BaseModel):
    """Complete per-criterion proposal for a worker task or Team goal."""

    model_config = ConfigDict(extra="forbid")

    checks: list[CriterionCheck] = Field(min_length=1, max_length=8)


class AgentResult(BaseModel):
    """Server-owned, checkpoint-safe result for one worker task."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=96)
    task_id: str = Field(min_length=1, max_length=96)
    agent_id: str = Field(min_length=1, max_length=96)
    agent_node: str = Field(default="", max_length=64)
    expert_id: str = Field(default="", max_length=96)
    status: TeamWorkerStatus
    # A task's local ``max_attempts`` remains bounded at three.  Team-level
    # re-execution is a separate, server-owned repair budget and can add up to
    # five more rounds, so the persisted report must accept the combined
    # attempt number instead of crashing the parent graph on a valid repair.
    attempt: int = Field(default=1, ge=1, le=8)
    summary: str = Field(default="", max_length=2_400)
    findings: list[str] = Field(default_factory=list, max_length=12)
    finding_evidence_refs: list[list[str]] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    open_questions: list[str] = Field(default_factory=list, max_length=8)
    confidence: TeamConfidence = "unknown"
    failure_strategy: TeamFailureStrategy = "partial"
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)
    tool_call_count: int = Field(default=0, ge=0, le=64)
    model_turn_count: int = Field(default=0, ge=0, le=64)
    assessment_status: Literal["typed", "fallback"] = "typed"
    criteria_status: CriteriaStatus = "blocked"
    criteria_checks: list[CriterionCheck] = Field(default_factory=list, max_length=8)
    unmet_criteria: list[str] = Field(default_factory=list, max_length=8)
    error_code: str | None = Field(default=None, max_length=128)
    error_detail: str = Field(default="", max_length=600)


AgentReport = AgentResult


class CollaborationMessage(BaseModel):
    """Bounded typed message exchanged inside one Team run."""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=128)
    collaboration_id: str = Field(min_length=1, max_length=96)
    task_id: str = Field(min_length=1, max_length=96)
    agent_id: str = Field(min_length=1, max_length=96)
    message_type: Literal["task", "report", "review_feedback", "reexecute_request"]
    input_refs: list[str] = Field(default_factory=list, max_length=24)
    evidence_refs: list[str] = Field(default_factory=list, max_length=80)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=12)
    next_action: str = Field(default="", max_length=600)


class CollaborationEvent(BaseModel):
    """Stable event envelope for live stream and durable Team replay."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="team.v1", max_length=24)
    run_id: str = Field(min_length=1, max_length=96)
    collaboration_id: str = Field(min_length=1, max_length=96)
    sequence: int = Field(ge=0)
    occurred_at: str = Field(min_length=1, max_length=64)
    namespace: str = Field(default="", max_length=192)
    scope: Literal["coordinator", "expert", "review"]
    agent_id: str = Field(default="", max_length=96)
    task_id: str = Field(default="", max_length=96)
    phase: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=64)
    status: str = Field(default="", max_length=32)
    attempt: int = Field(default=0, ge=0, le=32)
    summary: str = Field(default="", max_length=1_000)
    parent_event_id: str = Field(default="", max_length=128)
    safe_details: dict[str, object] = Field(default_factory=dict, max_length=24)


class TeamReviewIssue(BaseModel):
    """One actionable cross-worker consistency issue."""

    # Ignore non-authoritative provider annotations, not missing required
    # fields or invalid resolution values. Plan/tool permissions stay strict.
    model_config = ConfigDict(extra="ignore")

    category: Literal[
        "coverage",
        "conflict",
        "evidence",
        "time_scope",
        "entity_scope",
        "risk",
        "other",
    ] = "other"
    severity: Literal["low", "medium", "high"] = "medium"
    reason: str = Field(min_length=1, max_length=600)
    task_ids: list[str] = Field(default_factory=list, max_length=8)
    repair_instruction: str = Field(default="", max_length=600)
    resolution: Literal["research", "qualify", "block"] = Field(
        default="research",
        description=(
            "research: an expert must retrieve missing evidence; qualify: the final synthesizer "
            "must disclose an established limitation without repeating research; block: unsafe to conclude"
        ),
    )


class CriticReview(BaseModel):
    """Independent review of coverage, evidence, and semantic safety."""

    model_config = ConfigDict(extra="forbid")

    verdict: TeamReviewVerdict
    summary: str = Field(default="", max_length=900)
    progress_text: str = Field(default="", max_length=1_200)
    issues: list[TeamReviewIssue] = Field(default_factory=list, max_length=12)


__all__ = [
    "AgentResult",
    "AgentTaskDraft",
    "AgentTask",
    "AgentReport",
    "BearCaseReview",
    "BullCaseReview",
    "CaseReview",
    "CollaborationEvent",
    "CollaborationMessage",
    "CollaborationPlan",
    "CollaborationTask",
    "ConflictAssessment",
    "ConflictIssue",
    "ConflictStatus",
    "ConsensusResolution",
    "CoordinatorHandoffNarration",
    "ConsensusVerdict",
    "CriteriaAssessment",
    "CriteriaStatus",
    "CriteriaVerdict",
    "CriterionCheck",
    "CriticReview",
    "DraftAggregation",
    "DraftSection",
    "DraftStatus",
    "EvidenceMerge",
    "EvidenceMergeStatus",
    "ReexecutionDecision",
    "ReexecutionStatus",
    "TeamFailureStrategy",
    "TeamConfidence",
    "TeamPlan",
    "TeamPlanDraft",
    "TeamReviewIssue",
    "OrchestratorRoute",
    "OrchestratorRouteMode",
    "TeamWorkerStatus",
    "WorkerAssessment",
]
