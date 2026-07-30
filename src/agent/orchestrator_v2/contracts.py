# -*- coding: utf-8 -*-
"""Strongly typed contracts for the unified Agent orchestration control plane.

This module deliberately contains no provider, tool-registry, or chat-endpoint
logic.  It is the stable boundary shared by planning, compilation, execution,
state persistence, and observability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Awaitable, Callable, Generic, Literal, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


class AgentErrorCode(str, Enum):
    PLANNER_TIMEOUT = "planner_timeout"
    PLANNER_SCHEMA_INVALID = "planner_schema_invalid"
    CLARIFICATION_REQUIRED = "clarification_required"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    POLICY_BLOCKED = "policy_blocked"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CIRCUIT_OPEN = "circuit_open"
    BUDGET_EXCEEDED = "budget_exceeded"
    TOOL_FAILED = "tool_failed"
    COVERAGE_INCOMPLETE = "coverage_incomplete"
    SYNTHESIS_FAILED = "synthesis_failed"


class AgentStage(str, Enum):
    OUTLINE = "outline"
    PARAMETERIZATION = "parameterization"
    NORMALIZATION = "normalization"
    RESOURCE_BINDING = "resource_binding"
    COMPILATION = "compilation"
    POLICY = "policy"
    EXECUTION = "execution"
    BENEFIT_OUTLINE = "benefit_outline"
    CATALOG_LOADING = "catalog_loading"
    CATALOG_MAPPING = "catalog_mapping"
    RESULT_VALIDATION = "result_validation"
    RESOURCE_PUBLISHED = "resource_published"
    SYNTHESIS = "synthesis"
    COMPLETED = "completed"


class StageStatus(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class OutcomeStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ResourceType(str, Enum):
    SECURITY_COLLECTION = "security_collection"
    DOMAIN_COLLECTION = "domain_collection"
    EVIDENCE_COLLECTION = "evidence_collection"
    MARKET_MAINLINE_SNAPSHOT = "market_mainline_snapshot"
    GENERIC_RESULT = "generic_result"


class EffectLevel(str, Enum):
    READ = "read"
    MUTATION = "mutation"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"
    TRADE = "trade"


class CacheReuseScope(str, Enum):
    RUN_ONLY = "run_only"
    CROSS_RUN = "cross_run"


class RendererMode(str, Enum):
    DETERMINISTIC = "deterministic"
    EVIDENCE_SYNTHESIS = "evidence_synthesis"


class Capability(str, Enum):
    """Complete standard-capability surface of the unified control plane."""

    GENERAL_RESPONSE = "general_response"
    SECURITY_LOOKUP = "security_lookup"
    REALTIME_QUOTE = "realtime_quote"
    PRICE_HISTORY = "price_history"
    TECHNICAL_ANALYSIS = "technical_analysis"
    FUNDAMENTAL_ANALYSIS = "fundamental_analysis"
    VALUATION_ANALYSIS = "valuation_analysis"
    FINANCIAL_STATEMENT_ANALYSIS = "financial_statement_analysis"
    NEWS_ANALYSIS = "news_analysis"
    ANNOUNCEMENT_ANALYSIS = "announcement_analysis"
    RISK_ANALYSIS = "risk_analysis"
    REGULATORY_ANALYSIS = "regulatory_analysis"
    RESEARCH_REPORT_ANALYSIS = "research_report_analysis"
    CATALYST_ANALYSIS = "catalyst_analysis"
    SOCIAL_SENTIMENT_ANALYSIS = "social_sentiment_analysis"
    STOCK_COMPARISON = "stock_comparison"
    STOCK_DEEP_RESEARCH = "stock_deep_research"
    INVESTMENT_DECISION = "investment_decision"
    MARKET_OVERVIEW = "market_overview"
    SECTOR_ANALYSIS = "sector_analysis"
    CAPITAL_FLOW_ANALYSIS = "capital_flow_analysis"
    MACRO_ANALYSIS = "macro_analysis"
    INDUSTRY_RESEARCH = "industry_research"
    THEME_STOCK_DISCOVERY = "theme_stock_discovery"
    THEME_BUSINESS_EVIDENCE = "theme_business_evidence"
    STOCK_SCREENING = "stock_screening"
    COLLECTION_FINANCIAL_FILTER = "collection_financial_filter"
    WATCHLIST_QUERY = "watchlist_query"
    WATCHLIST_MUTATION = "watchlist_mutation"
    WATCHLIST_GROUP_MANAGEMENT = "watchlist_group_management"
    DATA_HEALTH = "data_health"
    FORMAL_ANALYSIS = "formal_analysis"
    ANALYSIS_HISTORY = "analysis_history"
    ANALYSIS_TEMPLATE_MANAGEMENT = "analysis_template_management"
    BATCH_ANALYSIS = "batch_analysis"
    BATCH_RUN_MANAGEMENT = "batch_run_management"
    ANALYSIS_SCHEDULE_MANAGEMENT = "analysis_schedule_management"
    NOTIFICATION = "notification"
    FINANCIAL_SOURCE_DISCOVERY = "financial_source_discovery"
    FINANCIAL_FEED_READ = "financial_feed_read"
    FINANCIAL_ARTICLE_READ = "financial_article_read"
    WEBPAGE_FEED_TRANSFORM = "webpage_feed_transform"
    FINANCIAL_FEED_EXPORT = "financial_feed_export"
    PUBLIC_WEB_RESEARCH = "public_web_research"
    TRADE_EXECUTION = "trade_execution"


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
        if self.mode == SelectionMode.TOP_K and (
            self.max_items is None or self.max_items < 2
        ):
            raise ValueError("top_k requires max_items>=2")
        if self.mode == SelectionMode.ALL_RELEVANT and self.max_items is not None:
            raise ValueError("all_relevant requires max_items=null")
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
        identities = [
            (item.source, item.node_id, item.artifact_id, item.resource_type)
            for item in value
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("input_refs must be unique")
        return value


class IntentOutlineV2(StrictModel):
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
            dependencies = {
                ref.node_id
                for ref in node.input_refs
                if ref.source == "node" and ref.node_id is not None
            }
            unknown = dependencies - known
            if unknown:
                raise ValueError(
                    f"{node.node_id} references unknown nodes: {sorted(unknown)}"
                )
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
        if (
            self.reuse_scope == CacheReuseScope.CROSS_RUN
            and self.max_age_seconds is None
        ):
            raise ValueError("cross-run reuse requires max_age_seconds")
        if (
            self.reuse_scope == CacheReuseScope.RUN_ONLY
            and self.max_age_seconds is not None
        ):
            raise ValueError("run-only reuse cannot declare max_age_seconds")
        return self


class ExecutionPolicy(StrictModel):
    effect: EffectLevel = EffectLevel.READ
    confirmation_required: bool = False
    max_calls: int = Field(default=8, ge=0, le=10_000)
    max_parallelism: int = Field(default=4, ge=1, le=64)
    timeout_seconds: float = Field(default=180.0, ge=1.0, le=3_600.0)
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
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


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
        has_issues = bool(
            self.missing_capabilities
            or self.extraneous_node_ids
            or self.resource_issues
        )
        if self.accepted == has_issues:
            raise ValueError(
                "accepted must be true exactly when no semantic issues exist"
            )
        return self


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
        self.details = details or (
            ErrorDetailV2(code=code, message=message),
        )
        self.metadata = dict(metadata or {})


IntentT = TypeVar("IntentT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)
ArgsT = TypeVar("ArgsT", bound=BaseModel)


@dataclass(frozen=True)
class NormalizedIntent(Generic[IntentT]):
    intent: IntentT
    execution_parameters: Mapping[str, Any]
    assumptions: tuple[AssumptionRecord, ...] = ()


Compiler = Callable[
    [str, str, IntentT, tuple[InputReferenceV2, ...], ResultSelectionV2 | None, int],
    NormalizedIntent[IntentT],
]


@dataclass(frozen=True)
class CapabilitySpec(Generic[IntentT, ResultT]):
    capability: Capability
    version: str
    title: str
    description: str
    intent_model: type[IntentT]
    result_model: type[ResultT]
    input_resources: frozenset[ResourceType]
    output_resources: frozenset[ResourceType]
    compiler: Compiler[IntentT]
    execution_policy: ExecutionPolicy
    freshness_policy: FreshnessPolicy
    projector: Callable[
        [ResultT, ResourceType],
        ProjectedResourceV2 | None,
    ]
    renderer: RendererMode
    allow_direct_entities: bool = False
    supports_result_selection: bool = False
    program_default_fields: frozenset[str] = field(default_factory=frozenset)
    subsumes_capabilities: frozenset[Capability] = field(default_factory=frozenset)

    @property
    def schema_version(self) -> str:
        schema = self.intent_model.model_json_schema()
        digest = sha256(
            json.dumps(schema, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        return f"{self.version}:{digest}"


@dataclass(frozen=True)
class CompiledCallV2(Generic[ArgsT]):
    task_id: str
    step_id: str
    tool_name: str
    arguments: ArgsT
    depends_on_steps: tuple[str, ...]
    after_steps: tuple[str, ...]
    result_bindings: tuple[tuple[str, str], ...]
    idempotency_key: str


StageObserver = Callable[[AgentStageEventV2], Awaitable[None] | None]


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "AgentArtifactV2",
    "AgentErrorCode",
    "AgentStage",
    "AgentStageEventV2",
    "AssumptionRecord",
    "CacheReuseScope",
    "CapabilitySpec",
    "CompiledCallV2",
    "CoverageV2",
    "EffectLevel",
    "ErrorDetailV2",
    "ExecutionPolicy",
    "FreshnessPolicy",
    "InputReferenceV2",
    "IntentOutlineNodeV2",
    "IntentOutlineV2",
    "Capability",
    "NormalizedIntent",
    "OrchestratorV2Error",
    "OutcomeStatus",
    "PlanningTraceV2",
    "PlannerVerificationV2",
    "ProjectedResourceV2",
    "RepairIssueV2",
    "RepairRecordV2",
    "RendererMode",
    "ResourceType",
    "ResultSelectionV2",
    "SelectionMode",
    "StageObserver",
    "StageStatus",
    "StrictModel",
    "TaskOutcomeV2",
    "stable_fingerprint",
]
