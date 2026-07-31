"""Typed contract model group 1."""

from __future__ import annotations

import src.agent.orchestrator_v2.contracts as _contracts

for _name, _value in vars(_contracts).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = ['SelectionMode', 'ResultSelectionV2', 'ClaimRequirementV2', 'GoalContractV2', 'InputReferenceV2', 'IntentOutlineNodeV2', 'IntentOutlineV2', 'AssumptionRecord', 'FreshnessPolicy', 'ExecutionPolicy', 'CoverageV2', 'EvidenceV2', 'ErrorDetailV2', 'TaskOutcomeV2', 'ProjectedResourceV2', 'AgentStageEventV2', 'AgentArtifactV2', 'RepairIssueV2', 'RepairRecordV2', 'PlanningTraceV2', 'PlannerVerificationV2', 'ClaimStatus', 'EvidenceQuality', 'GoalDisposition', 'GoalTerminalReason', 'EvidenceLedgerEntryV2', 'ClaimAssessmentV2', 'GoalBudgetV2', 'GoalEvaluationV2', 'GoalRunStateV2', 'OrchestratorV2Error']

class SelectionMode(str, Enum):
    BEST_ONE = "best_one"
    TOP_K = "top_k"
    ALL_RELEVANT = "all_relevant"

class ResultSelectionV2(StrictModel):
    mode: SelectionMode = SelectionMode.TOP_K
    max_items: int | None = Field(default=16, ge=1, le=512)

    @model_validator(mode="after")
    def _validate_mode(self) -> "ResultSelectionV2":
        if self.mode == SelectionMode.BEST_ONE and self.max_items != 1:
            raise ValueError("best_one requires max_items=1")
        if self.mode == SelectionMode.TOP_K and (self.max_items is None or self.max_items < 2):
            raise ValueError("top_k requires max_items>=2")
        if self.mode == SelectionMode.ALL_RELEVANT and self.max_items is not None:
            raise ValueError("all_relevant requires max_items=null")
        return self

class ClaimRequirementV2(StrictModel):
    """One user-facing claim that must be supported before the run can finish."""

    claim_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    question: str = Field(min_length=1, max_length=500)
    required_dimensions: tuple[EvidenceDimension, ...] = Field(
        min_length=1,
        max_length=12,
    )
    optional_dimensions: tuple[EvidenceDimension, ...] = Field(
        default_factory=tuple,
        max_length=12,
    )
    mandatory: bool = True

    @model_validator(mode="after")
    def _unique_dimensions(self) -> "ClaimRequirementV2":
        if len(self.required_dimensions) != len(set(self.required_dimensions)):
            raise ValueError("required_dimensions must be unique")
        if len(self.optional_dimensions) != len(set(self.optional_dimensions)):
            raise ValueError("optional_dimensions must be unique")
        if set(self.required_dimensions) & set(self.optional_dimensions):
            raise ValueError("required_dimensions and optional_dimensions cannot overlap")
        return self

class GoalContractV2(StrictModel):
    """Typed definition of what a successful answer must deliver."""

    objective: str = Field(min_length=1, max_length=1_000)
    question_type: QuestionType
    uncertainty_mode: UncertaintyMode
    time_horizon: str | None = Field(default=None, max_length=120)
    deliverables: tuple[str, ...] = Field(min_length=1, max_length=12)
    claims: tuple[ClaimRequirementV2, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def _validate_goal(self) -> "GoalContractV2":
        claim_ids = [item.claim_id for item in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("goal claim_id values must be unique")
        if self.question_type == QuestionType.FORECAST and self.uncertainty_mode != UncertaintyMode.SCENARIO:
            raise ValueError("forecast goals require scenario uncertainty mode")
        if self.question_type == QuestionType.OPERATION and self.uncertainty_mode != UncertaintyMode.NOT_APPLICABLE:
            raise ValueError("operation goals require not_applicable uncertainty mode")
        return self

class InputReferenceV2(StrictModel):
    source: Literal["node", "artifact"] = "node"
    node_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,31}$",
    )
    artifact_id: str | None = Field(default=None, max_length=64)
    resource_type: ResourceType

    @model_validator(mode="after")
    def _validate_source_identity(self) -> "InputReferenceV2":
        if self.source == "node":
            if not self.node_id or self.artifact_id is not None:
                raise ValueError("node source requires only node_id")
        elif not self.artifact_id or self.node_id is not None:
            raise ValueError("artifact source requires only artifact_id")
        return self

class IntentOutlineNodeV2(StrictModel):
    node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    capability: Capability
    objective: str = Field(min_length=1, max_length=500)
    input_refs: tuple[InputReferenceV2, ...] = Field(default_factory=tuple, max_length=12)
    result_selection: ResultSelectionV2 | None = None

    @field_validator("input_refs")
    @classmethod
    def _unique_input_resources(
        cls,
        value: tuple[InputReferenceV2, ...],
    ) -> tuple[InputReferenceV2, ...]:
        identities = [(item.source, item.node_id, item.artifact_id, item.resource_type) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("input_refs must be unique")
        return value

class IntentOutlineV2(StrictModel):
    goal: GoalContractV2
    nodes: tuple[IntentOutlineNodeV2, ...] = Field(min_length=1, max_length=12)
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _validate_graph(self) -> "IntentOutlineV2":
        if self.needs_clarification:
            if not self.clarification_question:
                raise ValueError("clarification_question is required")
            return self
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node_id values must be unique")
        known = set(node_ids)
        graph: dict[str, set[str]] = {}
        for node in self.nodes:
            dependencies = {ref.node_id for ref in node.input_refs if ref.source == "node" and ref.node_id is not None}
            unknown = dependencies - known
            if unknown:
                raise ValueError(f"{node.node_id} references unknown nodes: {sorted(unknown)}")
            if node.node_id in dependencies:
                raise ValueError(f"{node.node_id} cannot reference itself")
            graph[node.node_id] = dependencies

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("intent graph contains a cycle")
            if node_id in visited:
                return
            visiting.add(node_id)
            for dependency in graph[node_id]:
                visit(dependency)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in node_ids:
            visit(node_id)
        return self

class AssumptionRecord(StrictModel):
    node_id: str
    field_path: str
    value: Any
    reason: str = Field(min_length=1, max_length=300)
    source: str = "program_default"

class FreshnessPolicy(StrictModel):
    """Capability-owned cross-run freshness contract.

    Same-run exact-call reuse is always controlled by the Workflow executor.
    This policy only decides whether a successful read may cross a run
    boundary, and for how long.
    """

    reuse_scope: CacheReuseScope = CacheReuseScope.RUN_ONLY
    max_age_seconds: int | None = Field(default=None, ge=1, le=604_800)
    market_session_sensitive: bool = False
    require_observed_at: bool = False

    @model_validator(mode="after")
    def _validate_scope(self) -> "FreshnessPolicy":
        if self.reuse_scope == CacheReuseScope.CROSS_RUN and self.max_age_seconds is None:
            raise ValueError("cross-run reuse requires max_age_seconds")
        if self.reuse_scope == CacheReuseScope.RUN_ONLY and self.max_age_seconds is not None:
            raise ValueError("run-only reuse cannot declare max_age_seconds")
        return self

class ExecutionPolicy(StrictModel):
    effect: EffectLevel = EffectLevel.READ
    confirmation_required: bool = False
    max_calls: int = Field(default=8, ge=0, le=10_000)
    max_parallelism: int = Field(default=4, ge=1, le=64)
    max_attempts: int = Field(default=2, ge=1, le=5)
    retry_backoff_seconds: float = Field(default=0.5, ge=0.0, le=30.0)
    retry_backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    retryable_error_codes: tuple[str, ...] = (
        "timeout",
        "connection_error",
        "provider_rate_limited",
        "provider_unavailable",
        "tool_process_crashed",
    )

    @model_validator(mode="after")
    def _validate_effect_policy(self) -> "ExecutionPolicy":
        if self.effect != EffectLevel.READ and self.max_attempts != 1:
            raise ValueError(
                "non-read capabilities require max_attempts=1; retries must be "
                "dispatched through an effect-specific outbox adapter"
            )
        return self

class CoverageV2(StrictModel):
    requested: int = Field(default=0, ge=0)
    covered: int = Field(default=0, ge=0)
    missing: tuple[str, ...] = ()
    complete: bool = False

    @model_validator(mode="after")
    def _validate_counts(self) -> "CoverageV2":
        if self.covered > self.requested:
            raise ValueError("covered cannot exceed requested")
        expected_complete = self.covered == self.requested and not self.missing
        if self.complete != expected_complete:
            raise ValueError("complete must match requested/covered/missing")
        return self

class EvidenceV2(StrictModel):
    source: str
    locator: str | None = None
    observed_at: datetime | None = None
    summary: str | None = Field(default=None, max_length=2_000)

class ErrorDetailV2(StrictModel):
    code: AgentErrorCode
    message: str = Field(min_length=1, max_length=2_000)
    path: str | None = None
    retryable: bool = False

class TaskOutcomeV2(StrictModel):
    task_id: str
    status: OutcomeStatus
    coverage: CoverageV2
    artifact_refs: tuple[str, ...] = ()
    evidence: tuple[EvidenceV2, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[ErrorDetailV2, ...] = ()
    result: Any = None

    @model_validator(mode="after")
    def _validate_status(self) -> "TaskOutcomeV2":
        if self.status == OutcomeStatus.SUCCEEDED and not self.coverage.complete:
            raise ValueError("succeeded outcome requires complete coverage")
        if self.status in {OutcomeStatus.FAILED, OutcomeStatus.BLOCKED} and not self.errors:
            raise ValueError("failed or blocked outcome requires an error")
        return self

class ProjectedResourceV2(StrictModel):
    resource_type: ResourceType
    coverage: CoverageV2
    payload: Any

class AgentStageEventV2(StrictModel):
    event: str = "agent_stage_v2"
    run_id: str
    stage: AgentStage
    status: StageStatus
    task_id: str | None = None
    error_code: AgentErrorCode | None = None
    summary: str = Field(default="", max_length=500)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AgentArtifactV2(StrictModel):
    artifact_id: str
    schema_version: str
    run_id: str
    conversation_id: str
    producer_node_id: str
    resource_type: ResourceType
    coverage: CoverageV2
    sources: tuple[EvidenceV2, ...] = ()
    produced_at: datetime
    fingerprint: str
    lineage: tuple[str, ...] = ()
    payload: Any

class RepairIssueV2(StrictModel):
    pointer: str
    code: str
    expected: str
    allowed: tuple[Any, ...] = ()
    message: str

class RepairRecordV2(StrictModel):
    node_id: str | None = None
    function_name: str
    invalid_payload: Any
    issues: tuple[RepairIssueV2, ...]
    succeeded: bool

class PlanningTraceV2(StrictModel):
    run_id: str
    schema_version: str
    raw_outline: Any = None
    normalized_outline: Any = None
    raw_intents: Mapping[str, Any] = Field(default_factory=dict)
    normalized_intents: Mapping[str, Any] = Field(default_factory=dict)
    assumptions: tuple[AssumptionRecord, ...] = ()
    repairs: tuple[RepairRecordV2, ...] = ()
    verification: Any = None
    goal_state: Any = None
    plan_revision: int = Field(default=0, ge=0, le=16)
    stage_durations_ms: Mapping[str, int] = Field(default_factory=dict)

class PlannerVerificationV2(StrictModel):
    """Independent semantic review of a frozen capability/resource graph."""

    accepted: bool
    confidence: float = Field(ge=0.0, le=1.0)
    missing_capabilities: tuple[Capability, ...] = ()
    extraneous_node_ids: tuple[str, ...] = ()
    resource_issues: tuple[str, ...] = Field(default=(), max_length=24)
    rationale: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def _accepted_has_no_issues(self) -> "PlannerVerificationV2":
        has_issues = bool(self.missing_capabilities or self.extraneous_node_ids or self.resource_issues)
        # A positive verdict is only valid when every typed diagnostic list is
        # empty.  A negative verdict may still rely on ``rationale`` when the
        # verifier identifies a semantic mismatch that does not fit one of the
        # bounded diagnostic categories.  Treat that as a real rejection for
        # bounded replanning instead of turning a valid semantic veto into a
        # schema failure.
        if self.accepted and has_issues:
            raise ValueError("accepted cannot be true when semantic issues exist")
        return self

class ClaimStatus(str, Enum):
    UNASSESSED = "unassessed"
    SUPPORTED = "supported"
    CONTESTED = "contested"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"

class EvidenceQuality(str, Enum):
    AUTHORITATIVE = "authoritative"
    DEGRADED = "degraded"
    FAILED = "failed"

class GoalDisposition(str, Enum):
    COMPLETE = "complete"
    EXPAND_READS = "expand_reads"
    CLARIFY = "clarify"
    BEST_EFFORT = "best_effort"
    FAILED = "failed"

class GoalTerminalReason(str, Enum):
    GOAL_SATISFIED = "goal_satisfied"
    COMPLETED_WITH_UNCERTAINTY = "completed_with_uncertainty"
    USER_INPUT_REQUIRED = "user_input_required"
    POLICY_BLOCKED = "policy_blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_SAFE_EXPANSION = "no_safe_expansion"
    EXECUTION_FAILED = "execution_failed"

class EvidenceLedgerEntryV2(StrictModel):
    evidence_id: str = Field(pattern=r"^e_[a-f0-9]{16}$")
    task_id: str
    capability: Capability
    dimensions: tuple[EvidenceDimension, ...]
    quality: EvidenceQuality
    source: str
    locator: str | None = None
    observed_at: datetime | None = None
    summary: str | None = Field(default=None, max_length=2_000)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

class ClaimAssessmentV2(StrictModel):
    claim_id: str
    status: ClaimStatus
    confidence: float = Field(ge=0.0, le=1.0)
    supported_by: tuple[str, ...] = ()
    missing_dimensions: tuple[EvidenceDimension, ...] = ()
    conflicts: tuple[str, ...] = ()
    rationale: str = Field(min_length=1, max_length=1_000)

class GoalBudgetV2(StrictModel):
    max_plan_revisions: int = Field(default=2, ge=0, le=8)
    plan_revisions_used: int = Field(default=0, ge=0, le=8)
    max_provider_calls: int = Field(default=64, ge=1, le=1_000)
    provider_calls_used: int = Field(default=0, ge=0, le=1_000)
    max_tool_calls: int = Field(default=1_000, ge=0, le=10_000)
    tool_calls_used: int = Field(default=0, ge=0, le=10_000)

    @property
    def can_revise(self) -> bool:
        return (
            self.plan_revisions_used < self.max_plan_revisions
            and self.provider_calls_used < self.max_provider_calls
            and self.tool_calls_used < self.max_tool_calls
        )

class GoalEvaluationV2(StrictModel):
    disposition: GoalDisposition
    assessments: tuple[ClaimAssessmentV2, ...]
    missing_dimensions: tuple[EvidenceDimension, ...] = ()
    proposed_capabilities: tuple[Capability, ...] = ()
    terminal_reason: GoalTerminalReason | None = None
    rationale: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def _validate_disposition(self) -> "GoalEvaluationV2":
        if self.disposition == GoalDisposition.EXPAND_READS and not self.proposed_capabilities:
            raise ValueError("expand_reads requires at least one proposed capability")
        if (
            self.disposition
            in {
                GoalDisposition.COMPLETE,
                GoalDisposition.BEST_EFFORT,
                GoalDisposition.CLARIFY,
                GoalDisposition.FAILED,
            }
            and self.terminal_reason is None
        ):
            raise ValueError("terminal disposition requires terminal_reason")
        return self

class GoalRunStateV2(StrictModel):
    version: str = "goal-state-1"
    goal: GoalContractV2
    claim_ledger: tuple[ClaimAssessmentV2, ...] = ()
    evidence_ledger: tuple[EvidenceLedgerEntryV2, ...] = ()
    attempted_capabilities: tuple[Capability, ...] = ()
    plan_revision: int = Field(default=0, ge=0, le=16)
    budget: GoalBudgetV2
    evaluation: GoalEvaluationV2 | None = None
    terminal_reason: GoalTerminalReason | None = None

class OrchestratorV2Error(RuntimeError):
    """Stable failure crossing Planner, compiler, executor, and chat layers."""

    def __init__(
        self,
        code: AgentErrorCode,
        message: str,
        *,
        task_id: str | None = None,
        details: tuple[ErrorDetailV2, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.task_id = task_id
        self.details = details or (ErrorDetailV2(code=code, message=message),)
        self.metadata = dict(metadata or {})
