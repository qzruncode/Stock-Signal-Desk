# -*- coding: utf-8 -*-
"""Program-owned standard tasks and immutable workflow registry.

The language model may choose a :class:`StandardTaskKind` and fill its semantic
parameters.  It never sees or chooses the tools below.  Every executable call
is compiled from this registry, which is the single authority for tool access,
ordering, call budgets and side-effect policy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from src.agent.result_contracts import (
    CollectionFinancialFilterSpec,
    DomainBoardQuerySpec,
    InvestmentThesisContext,
    ThemeEvidenceContext,
    project_collection_financial_filter_entities,
)


class StandardTaskKind(str, Enum):
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
    MARKET_MAINLINE_RESEARCH = "market_mainline_research"
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


class EntityScope(str, Enum):
    NONE = "none"
    CURRENT_MESSAGE = "current_message"
    PREVIOUS_ANSWER = "previous_answer"
    CONVERSATION = "conversation"


class ConfirmationState(str, Enum):
    NOT_REQUIRED = "not_required"
    MISSING = "missing"
    EXPLICIT = "explicit"


class EffectClass(str, Enum):
    READ = "read"
    MUTATION = "mutation"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"
    TRADE = "trade"


class ConfirmationPolicy(str, Enum):
    NEVER = "never"
    CONDITIONAL = "conditional"
    ALWAYS = "always"


class TaskResource(str, Enum):
    SECURITY_COLLECTION = "security_collection"
    DOMAIN_COLLECTION = "domain_collection"
    RSS_SOURCE_COLLECTION = "rss_source_collection"
    RSS_ITEM_COLLECTION = "rss_item_collection"
    TEXT_DOCUMENT_COLLECTION = "text_document_collection"
    EVIDENCE_COLLECTION = "evidence_collection"


class CollectionBehavior(str, Enum):
    NONE = "none"
    SOURCE = "source"
    PASSTHROUGH = "passthrough"
    FILTER = "filter"


class ResultSelectionMode(str, Enum):
    BEST_ONE = "best_one"
    TOP_K = "top_k"
    ALL_RELEVANT = "all_relevant"


class ResultSelectionSpec(BaseModel):
    """Typed cardinality contract for ranked collection results."""

    model_config = ConfigDict(extra="forbid")

    mode: ResultSelectionMode
    max_items: int | None = Field(default=None, ge=1, le=512)

    @model_validator(mode="after")
    def _validate_cardinality(self) -> "ResultSelectionSpec":
        if self.mode == ResultSelectionMode.BEST_ONE and self.max_items != 1:
            raise ValueError("best_one requires max_items=1")
        if self.mode == ResultSelectionMode.TOP_K:
            if self.max_items is None or self.max_items < 2:
                raise ValueError("top_k requires max_items between 2 and 512")
        if self.mode == ResultSelectionMode.ALL_RELEVANT and self.max_items is not None:
            raise ValueError("all_relevant requires max_items=null")
        return self


class StandardTask(BaseModel):
    """Internal immutable-workflow input produced only by typed compilers."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    kind: StandardTaskKind
    objective: str = Field(min_length=1, max_length=400)
    entity_scope: EntityScope = EntityScope.NONE
    entities: list[str] = Field(default_factory=list, max_length=100)
    execution_parameters: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("execution_parameters", "parameters"),
    )
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    result_selection: ResultSelectionSpec | None = None
    output_requirements: list[str] = Field(default_factory=list, max_length=20)
    confirmation: ConfirmationState = ConfirmationState.NOT_REQUIRED
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @property
    def parameters(self) -> Mapping[str, Any]:
        """Read-only compatibility view for fixed workflow compilers."""
        return MappingProxyType(self.execution_parameters)

    @field_validator("entities", "depends_on", "output_requirements", mode="before")
    @classmethod
    def _unique_text_list(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        result: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result


class TaskPlan(BaseModel):
    """Internal executable DAG projected from the frozen typed intent graph."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[StandardTask] = Field(default_factory=list, max_length=12)
    needs_clarification: bool = False
    clarification_question: str | None = None
    source: str = "semantic"

    @field_validator("tasks", mode="before")
    @classmethod
    def _decode_provider_encoded_tasks(cls, value: Any) -> Any:
        """Accept only a complete JSON array when a Provider encodes it twice."""
        if not isinstance(value, str):
            return value
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("tasks string must be a complete JSON array") from exc
        if not isinstance(decoded, list):
            raise ValueError("tasks string must decode to a JSON array")
        return decoded

    @model_validator(mode="after")
    def _validate_graph(self) -> "TaskPlan":
        if self.needs_clarification and not self.clarification_question:
            raise ValueError("clarification_question is required")
        if not self.needs_clarification and not self.tasks:
            raise ValueError("an executable plan must contain at least one task")
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_id values must be unique")
        known = set(task_ids)
        graph = {task.task_id: set(task.depends_on) for task in self.tasks}
        for task_id, dependencies in graph.items():
            unknown = dependencies - known
            if unknown:
                raise ValueError(f"{task_id} has unknown dependencies: {sorted(unknown)}")
            if task_id in dependencies:
                raise ValueError(f"{task_id} cannot depend on itself")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("task dependency graph contains a cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in task_ids:
            visit(task_id)
        return self


@dataclass(frozen=True)
class SecurityEntity:
    symbol: str
    name: str


@dataclass(frozen=True)
class ResolvedTask:
    candidate: StandardTask
    symbols: tuple[str, ...] = ()
    entity_names: tuple[tuple[str, str], ...] = ()

    @property
    def task_id(self) -> str:
        return self.candidate.task_id

    @property
    def kind(self) -> StandardTaskKind:
        return self.candidate.kind

    @property
    def parameters(self) -> Mapping[str, Any]:
        return self.candidate.parameters

    @property
    def result_selection(self) -> ResultSelectionSpec | None:
        return self.candidate.result_selection

    @property
    def entities(self) -> tuple[SecurityEntity, ...]:
        names = dict(self.entity_names)
        return tuple(SecurityEntity(symbol=symbol, name=names.get(symbol, symbol)) for symbol in self.symbols)


@dataclass(frozen=True)
class WorkflowExecutionGuard:
    """Program-owned condition that may project a result without tool execution."""

    source_step: str
    result_path: tuple[str, ...]
    allowed_values: frozenset[Any]
    blocked_reason: str


@dataclass(frozen=True)
class WorkflowCall:
    task_id: str
    step_id: str
    tool_name: str
    arguments: dict[str, Any]
    depends_on_steps: tuple[str, ...] = ()
    after_steps: tuple[str, ...] = ()
    result_bindings: tuple[tuple[str, str], ...] = ()
    execution_guard: WorkflowExecutionGuard | None = None


class WorkflowCompileError(ValueError):
    pass


Compiler = Callable[[ResolvedTask], list[WorkflowCall]]


@dataclass(frozen=True)
class ParameterRequirement:
    """Program-owned entity and confirmation policy for one action shape."""

    when: tuple[tuple[str, frozenset[Any]], ...] = ()
    requires_entities: bool = False
    confirmation_required: bool = False

    def applies(self, parameters: Mapping[str, Any]) -> bool:
        return all(parameters.get(key) in values for key, values in self.when)


@dataclass(frozen=True)
class WorkflowSpec:
    kind: StandardTaskKind
    title: str
    description: str
    tool_whitelist: frozenset[str]
    compiler: Compiler
    requires_entities: bool = False
    max_tool_calls: int = 8
    max_parallel_steps: int = 8
    allow_partial_tool_failures: bool = False
    effect: EffectClass = EffectClass.READ
    confirmation_policy: ConfirmationPolicy = ConfirmationPolicy.NEVER
    confirmation_actions: frozenset[str] = field(default_factory=frozenset)
    enabled: bool = True
    state_machine: tuple[str, ...] = ()
    parameter_requirements: tuple[ParameterRequirement, ...] = ()
    resource_bindings: frozenset[str] = field(default_factory=frozenset)
    input_resources: frozenset[TaskResource] = field(default_factory=frozenset)
    required_input_resources: frozenset[TaskResource] = field(default_factory=frozenset)
    alternative_input_resource_groups: tuple[frozenset[TaskResource], ...] = ()
    input_resource_parameters: Mapping[str, TaskResource] = field(default_factory=dict)
    parameter_output_resources: Mapping[str, TaskResource] = field(default_factory=dict)
    output_resource_paths: Mapping[
        TaskResource,
        tuple[str, ...],
    ] = field(default_factory=dict)
    output_resources: frozenset[TaskResource] = field(default_factory=frozenset)
    collection_behavior: CollectionBehavior = CollectionBehavior.NONE
    result_processor: str | None = None
    result_contract: str | None = None
    supports_result_selection: bool = False
    max_input_entities: int | None = None
    max_output_entities: int | None = None

    def requires_confirmation(self, parameters: Mapping[str, Any]) -> bool:
        if self.confirmation_policy == ConfirmationPolicy.ALWAYS:
            return True
        if self.confirmation_policy == ConfirmationPolicy.NEVER:
            return False
        action = str(parameters.get("action") or "")
        return action in self.confirmation_actions or any(
            requirement.confirmation_required and requirement.applies(parameters)
            for requirement in self.parameter_requirements
        )


def parameter_requirement_issues(
    task: StandardTask,
    spec: WorkflowSpec,
    *,
    symbols: Sequence[str] | None = None,
    check_confirmation: bool = True,
) -> list[str]:
    """Return declarative contract violations for one standard task."""
    issues: list[str] = []
    for requirement in spec.parameter_requirements:
        if not requirement.applies(task.parameters):
            continue
        if requirement.requires_entities and symbols is not None and not symbols:
            issues.append("requires resolved entities for the selected operation")
    if (
        check_confirmation
        and spec.requires_confirmation(task.parameters)
        and task.confirmation == ConfirmationState.NOT_REQUIRED
    ):
        issues.append("selected operation must declare confirmation state")
    return issues


from src.agent.workflow_compilers_primary import (
    compile_announcements as _compile_announcements,
    compile_catalyst_analysis as _compile_catalyst_analysis,
    compile_capital_flow as _compile_capital_flow,
    compile_comparison as _compile_comparison,
    compile_decision_packet as _compile_decision_packet,
    compile_fundamental as _compile_fundamental,
    compile_industry as _compile_industry,
    compile_macro as _compile_macro,
    compile_market as _compile_market,
    compile_market_mainline_research as _compile_market_mainline_research,
    compile_news as _compile_news,
    compile_no_tools as _compile_no_tools,
    compile_price_history as _compile_price_history,
    compile_professional_buy_analysis as _compile_professional_buy_analysis,
    compile_realtime_quote as _compile_realtime_quote,
    compile_regulatory as _compile_regulatory,
    compile_reports as _compile_reports,
    compile_risk as _compile_risk,
    compile_sector as _compile_sector,
    compile_security_lookup as _compile_security_lookup,
    compile_sentiment as _compile_sentiment,
    compile_statements as _compile_statements,
    compile_technical as _compile_technical,
    compile_valuation as _compile_valuation,
)
from src.agent.workflow_compilers_extended import (
    compile_article as _compile_article,
    compile_batch_analysis as _compile_batch_analysis,
    compile_batch_management as _compile_batch_management,
    compile_collection_filter as _compile_collection_filter,
    compile_data_health as _compile_data_health,
    compile_export as _compile_export,
    compile_feed_read as _compile_feed_read,
    compile_formal_analysis as _compile_formal_analysis,
    compile_group as _compile_group,
    compile_history as _compile_history,
    compile_notification as _compile_notification,
    compile_schedule as _compile_schedule,
    compile_screening as _compile_screening,
    compile_source_discovery as _compile_source_discovery,
    compile_template as _compile_template,
    compile_theme_discovery as _compile_theme_discovery,
    compile_theme_evidence as _compile_theme_evidence,
    compile_transform as _compile_transform,
    compile_watchlist_mutation as _compile_watchlist_mutation,
    compile_watchlist_query as _compile_watchlist_query,
    compile_web as _compile_web,
)


def _dedupe_security_entities(
    values: Iterable[Mapping[str, Any] | SecurityEntity],
) -> tuple[SecurityEntity, ...]:
    entities: list[SecurityEntity] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, SecurityEntity):
            symbol = value.symbol
            name = value.name
        else:
            symbol = str(value.get("symbol") or "").strip()
            name = str(value.get("name") or symbol).strip()
        if len(symbol) != 6 or not symbol.isdigit() or symbol in seen:
            continue
        seen.add(symbol)
        entities.append(SecurityEntity(symbol=symbol, name=name or symbol))
    return tuple(entities)


def _entities_from_result_context(
    result_context: Iterable[Mapping[str, Any]],
) -> tuple[SecurityEntity, ...]:
    found: list[dict[str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            symbol = str(value.get("symbol") or value.get("code") or value.get("stock_code") or "").strip()
            if len(symbol) == 6 and symbol.isdigit():
                found.append(
                    {
                        "symbol": symbol,
                        "name": str(value.get("name") or value.get("stock_name") or symbol).strip(),
                    }
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    for packet in result_context:
        result = (
            packet.get("result")
            if isinstance(packet, Mapping) and isinstance(packet.get("result"), Mapping)
            else packet
        )
        if isinstance(result, Mapping) and result.get("success") is False:
            continue
        visit(result)
    return _dedupe_security_entities(found)


def project_task_collection(
    task: ResolvedTask,
    result_context: Iterable[Mapping[str, Any]],
) -> tuple[SecurityEntity, ...]:
    """Project a task's declared collection output from typed execution data."""
    spec = workflow_for(task.kind)
    if TaskResource.SECURITY_COLLECTION not in spec.output_resources:
        return ()
    packets = list(result_context)
    if spec.collection_behavior == CollectionBehavior.SOURCE:
        return _entities_from_result_context(packets)
    if spec.collection_behavior == CollectionBehavior.PASSTHROUGH:
        discovered = {entity.symbol: entity.name for entity in _entities_from_result_context(packets)}
        return tuple(
            SecurityEntity(
                symbol=entity.symbol,
                name=discovered.get(entity.symbol, entity.name),
            )
            for entity in task.entities
        )
    if spec.collection_behavior == CollectionBehavior.FILTER:
        projected = project_collection_financial_filter_entities(
            ({"symbol": entity.symbol, "name": entity.name} for entity in task.entities),
            packets,
            task.parameters,
        )
        return _dedupe_security_entities(projected)
    return ()


from src.agent.workflow_registry import (
    WORKFLOW_REGISTRY,
    compile_task,
    registered_workflow_tools,
    workflow_for,
)


__all__ = [
    "CollectionBehavior",
    "ConfirmationPolicy",
    "ConfirmationState",
    "EffectClass",
    "EntityScope",
    "ParameterRequirement",
    "ResultSelectionMode",
    "ResultSelectionSpec",
    "ResolvedTask",
    "SecurityEntity",
    "StandardTask",
    "StandardTaskKind",
    "TaskPlan",
    "TaskResource",
    "WorkflowCall",
    "WorkflowExecutionGuard",
    "WorkflowCompileError",
    "WorkflowSpec",
    "WORKFLOW_REGISTRY",
    "compile_task",
    "parameter_requirement_issues",
    "project_task_collection",
    "registered_workflow_tools",
    "workflow_for",
]
