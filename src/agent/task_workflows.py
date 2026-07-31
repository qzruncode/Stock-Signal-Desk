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
    input_resource_parameters: Mapping[str, TaskResource] = field(default_factory=dict)
    parameter_output_resources: Mapping[str, TaskResource] = field(default_factory=dict)
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


def _call(
    task: ResolvedTask,
    step_id: str,
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    depends_on: Sequence[str] = (),
    after: Sequence[str] = (),
    bind_results: Mapping[str, str] | None = None,
    execute_when: WorkflowExecutionGuard | None = None,
) -> WorkflowCall:
    return WorkflowCall(
        task_id=task.task_id,
        step_id=step_id,
        tool_name=tool_name,
        arguments=dict(arguments or {}),
        depends_on_steps=tuple(depends_on),
        after_steps=tuple(after),
        result_bindings=tuple((bind_results or {}).items()),
        execution_guard=execute_when,
    )


def _params(task: ResolvedTask, allowed: Iterable[str]) -> dict[str, Any]:
    allowed_set = set(allowed)
    return {key: value for key, value in task.parameters.items() if key in allowed_set}


def _symbols_csv(task: ResolvedTask) -> str:
    if not task.symbols:
        raise WorkflowCompileError(f"{task.kind.value} requires at least one resolved entity")
    return ",".join(task.symbols)


def _per_symbol(
    task: ResolvedTask,
    tool_names: Sequence[str],
    argument_keys: Mapping[str, Iterable[str]] | None = None,
    *,
    max_symbols: int | None = None,
) -> list[WorkflowCall]:
    symbols = list(task.symbols)
    if not symbols:
        raise WorkflowCompileError(f"{task.kind.value} requires at least one resolved entity")
    if max_symbols is not None and len(symbols) > max_symbols:
        raise WorkflowCompileError(
            f"{task.kind.value} supports at most {max_symbols} entities per standard task; split the task"
        )
    calls: list[WorkflowCall] = []
    for symbol_index, symbol in enumerate(symbols, 1):
        for tool_index, tool_name in enumerate(tool_names, 1):
            args = {"symbol": symbol}
            args.update(_params(task, (argument_keys or {}).get(tool_name, ())))
            calls.append(_call(task, f"{tool_name}_{symbol_index}_{tool_index}", tool_name, args))
    return calls


def _compile_no_tools(task: ResolvedTask) -> list[WorkflowCall]:
    return []


def _compile_security_lookup(task: ResolvedTask) -> list[WorkflowCall]:
    return [_call(task, "search_security", "search_stocks", _params(task, {"query", "market", "sector", "limit"}))]


def _compile_realtime_quote(task: ResolvedTask) -> list[WorkflowCall]:
    return [_call(task, "quotes", "get_realtime_quotes", {"symbols": _symbols_csv(task)})]


def _compile_price_history(task: ResolvedTask) -> list[WorkflowCall]:
    has_range = bool(task.parameters.get("start_date") and task.parameters.get("end_date"))
    if has_range:
        return _per_symbol(
            task,
            ["get_history_data"],
            {"get_history_data": {"start_date", "end_date", "use_cache"}},
            max_symbols=8,
        )
    return _per_symbol(task, ["get_kline"], {"get_kline": {"count", "use_cache"}}, max_symbols=8)


def _compile_technical(task: ResolvedTask) -> list[WorkflowCall]:
    return _per_symbol(
        task,
        ["get_technical_indicators"],
        {"get_technical_indicators": {"count"}},
        max_symbols=8,
    )


def _compile_fundamental(task: ResolvedTask) -> list[WorkflowCall]:
    if len(task.symbols) > 2:
        return [_call(task, "multi_fundamental_snapshot", "get_multi_stock_snapshot", {"symbols": _symbols_csv(task)})]
    return _per_symbol(
        task,
        ["get_stock_info", "get_financials", "get_business_segments", "get_shareholder_structure"],
        {
            "get_financials": {"periods"},
            "get_business_segments": {"category", "periods"},
        },
        max_symbols=2,
    )


def _compile_valuation(task: ResolvedTask) -> list[WorkflowCall]:
    if len(task.symbols) > 2:
        return [_call(task, "multi_valuation_snapshot", "get_multi_stock_snapshot", {"symbols": _symbols_csv(task)})]
    return _per_symbol(
        task,
        ["get_valuation_ratios", "get_consensus_estimates", "get_peer_comparison"],
        {
            "get_valuation_ratios": {"with_history"},
            "get_consensus_estimates": {"metric"},
            "get_peer_comparison": {"dimension"},
        },
        max_symbols=2,
    )


def _compile_statements(task: ResolvedTask) -> list[WorkflowCall]:
    return _per_symbol(
        task,
        ["get_balance_sheet", "get_income_statement", "get_cashflow"],
        {
            "get_balance_sheet": {"periods"},
            "get_income_statement": {"periods"},
            "get_cashflow": {"periods"},
        },
        max_symbols=2,
    )


def _compile_news(task: ResolvedTask) -> list[WorkflowCall]:
    if not task.symbols or len(task.symbols) > 2:
        query = str(task.parameters.get("query") or " ".join(task.symbols)).strip()
        query = query or task.candidate.objective
        topic = str(task.parameters.get("topic") or "").strip()
        if not topic:
            raise WorkflowCompileError(
                "news_analysis requires a Planner-supplied topic for thematic or multi-company news"
            )
        subjects = task.parameters.get("subjects") or list(task.symbols)
        args = {
            "query": query,
            "topic": topic,
            **({"subjects": subjects} if subjects else {}),
            **_params(task, {"days", "limit", "include_content", "fallback_to_web"}),
        }
        return [_call(task, "multi_company_news", "search_financial_news", args)]
    calls = _per_symbol(task, ["search_news"], {"search_news": {"days", "limit", "use_cache"}}, max_symbols=2)
    calls.extend(_per_symbol(task, ["get_announcements"], {"get_announcements": {"days", "limit"}}, max_symbols=2))
    return calls


def _compile_announcements(task: ResolvedTask) -> list[WorkflowCall]:
    return _per_symbol(
        task,
        ["get_announcements"],
        {"get_announcements": {"days", "limit"}},
        max_symbols=8,
    )


def _compile_risk(task: ResolvedTask) -> list[WorkflowCall]:
    return _per_symbol(
        task,
        ["get_announcements", "get_risk_events"],
        {
            "get_announcements": {"days", "limit"},
            "get_risk_events": {"days", "limit"},
        },
        max_symbols=4,
    )


def _compile_regulatory(task: ResolvedTask) -> list[WorkflowCall]:
    return [
        _call(
            task,
            "regulatory_updates",
            "get_regulatory_updates",
            _params(
                task,
                {
                    "keyword",
                    "event_type",
                    "market",
                    "days",
                    "limit",
                    "include_content",
                    "fallback_to_web",
                    "project_type",
                    "project_stage",
                    "project_status",
                },
            ),
        )
    ]


def _compile_reports(task: ResolvedTask) -> list[WorkflowCall]:
    return _per_symbol(
        task,
        ["get_research_report"],
        {"get_research_report": {"days", "limit"}},
        max_symbols=8,
    )


def _compile_sentiment(task: ResolvedTask) -> list[WorkflowCall]:
    return _per_symbol(
        task,
        ["get_social_sentiment"],
        {"get_social_sentiment": {"days", "limit", "max_pages"}},
        max_symbols=8,
    )


def _compile_comparison(task: ResolvedTask) -> list[WorkflowCall]:
    calls = [_call(task, "comparison_snapshot", "get_multi_stock_snapshot", {"symbols": _symbols_csv(task)})]
    if len(task.symbols) <= 7 and bool(task.parameters.get("include_peers", True)):
        calls.extend(
            _per_symbol(
                task,
                ["get_peer_comparison"],
                {"get_peer_comparison": {"dimension"}},
                max_symbols=7,
            )
        )
    return calls


def _compile_decision_packet(task: ResolvedTask) -> list[WorkflowCall]:
    if len(task.symbols) > 8:
        raise WorkflowCompileError(
            "stock_deep_research supports at most 8 entities per task; narrow the research scope"
        )
    return [
        _call(
            task,
            "decision_evidence",
            "get_multi_stock_decision_evidence",
            {"symbols": _symbols_csv(task), "thesis": str(task.parameters.get("thesis") or "")},
        )
    ]


def _compile_catalyst_analysis(task: ResolvedTask) -> list[WorkflowCall]:
    symbols = list(task.symbols)
    if not symbols:
        raise WorkflowCompileError("catalyst_analysis requires at least one resolved entity")
    if len(symbols) > 8:
        raise WorkflowCompileError("catalyst_analysis supports at most 8 entities per standard task; narrow the scope")
    return [
        _call(
            task,
            f"catalyst_{index}",
            "analyze_stock_catalysts",
            {"symbols": symbol},
        )
        for index, symbol in enumerate(symbols, 1)
    ]


def _compile_professional_buy_analysis(task: ResolvedTask) -> list[WorkflowCall]:
    from src.services.buy_criteria.mainline_policy import (
        normalize_mainline_strategy,
    )

    symbols = list(task.symbols)
    if not symbols:
        raise WorkflowCompileError("investment_decision requires at least one resolved entity")
    if len(symbols) > 300:
        raise WorkflowCompileError("investment_decision supports at most 300 entities; narrow the collection first")
    thesis = str(task.parameters.get("thesis") or "").strip()
    raw_thesis_context = task.parameters.get("thesis_context")
    thesis_context = (
        InvestmentThesisContext.model_validate(raw_thesis_context).model_dump()
        if raw_thesis_context is not None
        else None
    )
    common_arguments = {
        "thesis": thesis,
        "mainline_strategy": normalize_mainline_strategy(task.parameters.get("mainline_strategy")).value,
        **({"thesis_context": thesis_context} if thesis_context is not None else {}),
    }
    snapshot_step = "market_mainline_snapshot"
    mainline_gate_step = "market_mainline_gate"
    return [
        _call(
            task,
            snapshot_step,
            "prepare_market_mainline_snapshot",
            {},
        ),
        _call(
            task,
            mainline_gate_step,
            "evaluate_market_mainline_gate",
            common_arguments,
            depends_on=(snapshot_step,),
            bind_results={
                "market_mainline_snapshot": snapshot_step,
            },
        ),
        *[
            _call(
                task,
                f"professional_buy_{index:03d}",
                "evaluate_multi_stock_buy_criteria",
                {
                    "symbols": symbol,
                    **common_arguments,
                },
                depends_on=(snapshot_step, mainline_gate_step),
                bind_results={
                    "market_mainline_snapshot": snapshot_step,
                    "market_mainline_assessment": mainline_gate_step,
                    "market_mainline_model_error": mainline_gate_step,
                },
                execute_when=WorkflowExecutionGuard(
                    source_step=mainline_gate_step,
                    result_path=("market_mainline_assessment", "status"),
                    allowed_values=frozenset({"pass"}),
                    blocked_reason=(
                        "共享市场主线第一关未通过，程序已按八维顺序" "直接形成逐股终态，后续七维不得执行。"
                    ),
                ),
            )
            for index, symbol in enumerate(symbols, 1)
        ],
    ]


def _compile_market(task: ResolvedTask) -> list[WorkflowCall]:
    calls = [
        _call(task, "market_status", "get_market_status"),
        _call(task, "market_breadth", "get_market_breadth"),
    ]
    if bool(task.parameters.get("include_index", True)):
        calls.append(_call(task, "index", "get_index_data", _params(task, {"index_code", "days"})))
    return calls


def _compile_market_mainline_research(
    task: ResolvedTask,
) -> list[WorkflowCall]:
    return [
        _call(
            task,
            "market_mainline_snapshot",
            "prepare_market_mainline_snapshot",
            {"force": False},
        )
    ]


def _compile_sector(task: ResolvedTask) -> list[WorkflowCall]:
    base = _params(task, {"type"})
    flow = _params(task, {"type", "period", "top_n"})
    calls = [
        _call(task, "sector_list", "get_sector_list", base),
        _call(task, "sector_flow", "get_sector_flow", flow),
    ]
    query = str(task.parameters.get("query") or task.candidate.objective).strip()
    if query:
        calls.append(
            _call(
                task,
                "sector_news",
                "search_financial_news",
                {
                    "query": query,
                    "topic": "industry",
                    **_params(task, {"subjects", "days", "limit", "include_content"}),
                },
            )
        )
    return calls


def _compile_capital_flow(task: ResolvedTask) -> list[WorkflowCall]:
    return _per_symbol(
        task,
        ["get_stock_capital_flow"],
        {"get_stock_capital_flow": {"days"}},
        max_symbols=8,
    )


def _compile_macro(task: ResolvedTask) -> list[WorkflowCall]:
    indicators = task.parameters.get("indicators") or []
    if isinstance(indicators, str):
        indicators = [indicators]
    calls = [
        _call(
            task,
            f"macro_{index}",
            "get_macro_indicator",
            {
                "indicator": indicator,
                **_params(task, {"periods"}),
            },
        )
        for index, indicator in enumerate(indicators[:5], 1)
    ]
    if bool(task.parameters.get("include_bond_yield")):
        calls.append(_call(task, "bond_yield", "get_bond_yield", _params(task, {"country", "term", "days"})))
    if bool(task.parameters.get("include_monetary_operations")):
        calls.append(
            _call(
                task,
                "monetary_operations",
                "get_monetary_policy_operations",
                _params(task, {"days", "instrument", "limit", "include_content", "fallback_to_web"}),
            )
        )
    query = str(task.parameters.get("query") or "").strip()
    if query and len(calls) < 8:
        subjects = task.parameters.get("subjects")
        if not isinstance(subjects, list) or not subjects:
            raise WorkflowCompileError("macro research requires Planner-supplied semantic subjects")
        calls.append(
            _call(
                task,
                "macro_research",
                "search_research_library",
                {
                    "query": query,
                    "category": "macro",
                    "subjects": subjects,
                    **_params(task, {"days", "limit", "include_content", "fallback_to_web"}),
                },
            )
        )
    if not calls:
        raise WorkflowCompileError("macro_analysis requires indicators or an explicitly requested macro source")
    return calls


def _compile_industry(task: ResolvedTask) -> list[WorkflowCall]:
    domains = task.parameters.get("domains")
    if not isinstance(domains, list) or not domains:
        raise WorkflowCompileError("industry_research requires a non-empty domains array")
    labels = [
        str(domain if isinstance(domain, str) else domain.get("label") if isinstance(domain, Mapping) else "").strip()
        for domain in domains
    ]
    if not all(labels):
        raise WorkflowCompileError("industry_research domains must contain semantic topic labels")
    return [
        _call(task, "domain_board_catalog", "get_domain_board_catalog", {}),
    ]


def _compile_theme_discovery(task: ResolvedTask) -> list[WorkflowCall]:
    domains = task.parameters.get("domains")
    if not isinstance(domains, list) or not domains:
        raise WorkflowCompileError("theme_stock_discovery requires a non-empty domains array")
    try:
        domain_specs = [DomainBoardQuerySpec.model_validate(domain).model_dump(exclude_none=True) for domain in domains]
    except Exception as exc:
        raise WorkflowCompileError(f"theme_stock_discovery requires resolved domain-board objects: {exc}") from exc
    args = {"domains": domain_specs}
    return [_call(task, "domain_candidates", "get_domain_stock_candidates", args)]


def _compile_theme_evidence(task: ResolvedTask) -> list[WorkflowCall]:
    domains = task.parameters.get("domains")
    candidate_scope = str(task.parameters.get("candidate_scope") or "").strip()
    query = str(task.parameters.get("query") or task.candidate.objective).strip()
    if not isinstance(domains, list) or not domains:
        raise WorkflowCompileError("theme_business_evidence requires resolved domains")
    if candidate_scope == "candidate_collection" and not task.symbols:
        raise WorkflowCompileError("theme_business_evidence requires the upstream candidate collection")
    subjects: list[str] = []
    for domain in domains:
        label = domain if isinstance(domain, str) else domain.get("label") if isinstance(domain, Mapping) else ""
        text = str(label or "").strip()
        if text and text not in subjects:
            subjects.append(text)
    if not subjects:
        raise WorkflowCompileError("theme_business_evidence requires semantic domain labels")
    try:
        evidence_context = ThemeEvidenceContext.model_validate(task.parameters.get("evidence_context"))
    except Exception as exc:
        raise WorkflowCompileError(f"theme_business_evidence requires a parent-theme evidence context: {exc}") from exc
    if candidate_scope == "candidate_collection":
        names = dict(task.entity_names)
        days = max(30, min(int(task.parameters.get("days") or 365), 730))
        return [
            _call(
                task,
                f"company_{index:04d}_{symbol}",
                "get_company_theme_evidence",
                {
                    "symbol": symbol,
                    "company_name": names.get(symbol, symbol),
                    "target_topics": evidence_context.target_topics,
                    "domains": subjects,
                    "objective": task.candidate.objective,
                    "days": days,
                },
            )
            for index, symbol in enumerate(task.symbols, 1)
        ]

    common = _params(task, {"days", "limit", "include_content", "fallback_to_web"})
    common.setdefault("days", 365)
    common.setdefault("limit", 12)
    common["include_content"] = True
    common["fallback_to_web"] = True
    calls: list[WorkflowCall] = []
    for index, subject in enumerate(subjects, 1):
        retrieval_subjects = list(
            dict.fromkeys(
                [
                    *evidence_context.target_topics,
                    subject,
                ]
            )
        )
        parent_topic = "、".join(evidence_context.target_topics)
        subject_query = f"{parent_topic}中的{subject}：{query}"
        calls.extend(
            [
                _call(
                    task,
                    f"business_news_{index}",
                    "search_financial_news",
                    {
                        "query": subject_query,
                        "topic": "industry",
                        "subjects": retrieval_subjects,
                        **common,
                    },
                ),
                _call(
                    task,
                    f"business_research_{index}",
                    "search_research_library",
                    {
                        "query": subject_query,
                        "category": "industry",
                        "subjects": retrieval_subjects,
                        **common,
                    },
                ),
            ]
        )
    return calls


def _compile_screening(task: ResolvedTask) -> list[WorkflowCall]:
    screen_spec = task.parameters.get("screen_spec")
    if not isinstance(screen_spec, dict):
        raise WorkflowCompileError("stock_screening requires a complete screen_spec")
    args = {
        "screen_spec": screen_spec,
        "refresh_if_stale": bool(task.parameters.get("refresh_if_stale", True)),
    }
    if task.parameters.get("save_group_name"):
        args["save_group_name"] = task.parameters["save_group_name"]
    return [_call(task, "screen", "screen_atr_volatility_stocks", args)]


def _compile_collection_filter(task: ResolvedTask) -> list[WorkflowCall]:
    symbols = list(task.symbols)
    if not symbols:
        raise WorkflowCompileError("collection_financial_filter requires the previous company collection")
    try:
        filter_spec = CollectionFinancialFilterSpec.model_validate(task.parameters)
    except Exception as exc:
        raise WorkflowCompileError(f"collection_financial_filter has an invalid semantic contract: {exc}") from exc
    calls: list[WorkflowCall] = []
    for condition_index, condition in enumerate(filter_spec.conditions, 1):
        common_arguments: dict[str, Any] = {
            "metric": condition.metric,
            "period_basis": condition.period_basis,
        }
        if condition.fiscal_year is not None:
            common_arguments["fiscal_year"] = condition.fiscal_year
        calls.extend(
            _call(
                task,
                f"condition_{condition_index}_batch_{index // 24 + 1}",
                "get_multi_stock_financials",
                {
                    "symbols": ",".join(symbols[index : index + 24]),
                    **common_arguments,
                },
            )
            for index in range(0, len(symbols), 24)
        )
    if len(symbols) > 300 or len(calls) > 104:
        raise WorkflowCompileError("collection_financial_filter supports at most 300 companies per turn")
    return calls


def _compile_watchlist_query(task: ResolvedTask) -> list[WorkflowCall]:
    domains = task.parameters.get("domains")
    if domains:
        return [
            _call(
                task,
                "filter_watchlist",
                "filter_watchlist_by_theme",
                {
                    "domains": domains,
                    **_params(task, {"group"}),
                },
            )
        ]
    return [_call(task, "list_watchlist", "manage_watchlist", {"action": "list"})]


def _compile_watchlist_mutation(task: ResolvedTask) -> list[WorkflowCall]:
    action = str(task.parameters.get("action") or "")
    return [
        _call(
            task,
            "mutate_watchlist",
            "manage_watchlist",
            {
                "action": action,
                "symbols": _symbols_csv(task),
            },
        )
    ]


def _compile_group(task: ResolvedTask) -> list[WorkflowCall]:
    args = _params(task, {"action", "group", "new_name"})
    if args.get("action") in {"add", "remove"} and not task.symbols:
        raise WorkflowCompileError("watchlist group add/remove requires resolved entities")
    args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
    if task.symbols:
        args["symbols"] = _symbols_csv(task)
    return [_call(task, "manage_group", "manage_watchlist_groups", args)]


def _compile_data_health(task: ResolvedTask) -> list[WorkflowCall]:
    return [_call(task, "data_health", "get_data_health")]


def _compile_formal_analysis(task: ResolvedTask) -> list[WorkflowCall]:
    action = str(task.parameters.get("action") or "start")
    if action == "status":
        return [_call(task, "analysis_status", "get_analysis_status", _params(task, {"task_id", "status", "limit"}))]
    if not task.symbols:
        raise WorkflowCompileError("starting formal analysis requires one resolved entity")
    return [
        _call(
            task,
            "start_analysis",
            "run_stock_analysis",
            {
                "symbol": task.symbols[0] if task.symbols else "",
                **_params(task, {"force_refresh", "notify_on_complete", "prompt_template_id"}),
            },
        )
    ]


def _compile_history(task: ResolvedTask) -> list[WorkflowCall]:
    action = str(task.parameters.get("action") or "search")
    if action == "read":
        return [
            _call(
                task,
                "read_report",
                "read_analysis_report",
                _params(task, {"record_id", "include_markdown", "include_news"}),
            )
        ]
    if action == "delete":
        args = _params(task, {"record_ids"})
        args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
        return [_call(task, "delete_history", "delete_analysis_history", args)]
    args = _params(task, {"start_date", "end_date", "page", "limit"})
    if task.symbols:
        args["symbol"] = task.symbols[0]
    return [_call(task, "search_history", "search_analysis_history", args)]


def _compile_template(task: ResolvedTask) -> list[WorkflowCall]:
    args = _params(task, {"action", "template_id", "name", "content", "set_default"})
    args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
    return [_call(task, "manage_template", "manage_analysis_templates", args)]


def _compile_batch_analysis(task: ResolvedTask) -> list[WorkflowCall]:
    scope = str(task.parameters.get("scope") or "")
    args = _params(
        task,
        {
            "scope",
            "group_name",
            "analysis_mode",
            "prompt_template_id",
            "force_refresh",
        },
    )
    if scope == "symbols":
        if not task.symbols:
            raise WorkflowCompileError("batch_analysis scope=symbols requires resolved entities")
        if len(task.symbols) > 50:
            raise WorkflowCompileError("batch_analysis supports at most 50 resolved entities")
        args["symbols"] = _symbols_csv(task)
    return [_call(task, "run_batch", "run_batch_analysis", args)]


def _compile_batch_management(task: ResolvedTask) -> list[WorkflowCall]:
    args = _params(task, {"action", "run_id", "symbols", "limit"})
    args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
    return [_call(task, "manage_batch", "manage_batch_run", args)]


def _compile_schedule(task: ResolvedTask) -> list[WorkflowCall]:
    args = _params(task, {"action", "enabled", "times", "prompt_template_id"})
    args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
    return [_call(task, "manage_schedule", "manage_analysis_schedule", args)]


def _compile_notification(task: ResolvedTask) -> list[WorkflowCall]:
    action = str(task.parameters.get("action") or "status")
    if action == "status":
        return [_call(task, "notification_status", "get_notification_status")]
    args = _params(task, {"content_type", "message", "record_id", "batch_run_id", "title"})
    args["confirmed"] = task.candidate.confirmation == ConfirmationState.EXPLICIT
    return [_call(task, "send_notification", "send_notification", args)]


def _compile_source_discovery(task: ResolvedTask) -> list[WorkflowCall]:
    route_path = str(task.parameters.get("route_path") or "").strip()
    if route_path:
        return [
            _call(task, "inspect_source", "inspect_financial_source", _params(task, {"route_path", "keyword", "force"}))
        ]
    return [
        _call(
            task,
            "list_sources",
            "list_financial_sources",
            _params(task, {"keyword", "namespace", "capability", "force", "limit"}),
        )
    ]


def _compile_feed_read(task: ResolvedTask) -> list[WorkflowCall]:
    return [
        _call(
            task,
            "read_feed",
            "read_financial_feed",
            _params(task, {"route_path", "params", "options", "namespace", "limit", "force"}),
        )
    ]


def _compile_article(task: ResolvedTask) -> list[WorkflowCall]:
    return [_call(task, "read_article", "read_financial_article", dict(task.parameters))]


def _compile_transform(task: ResolvedTask) -> list[WorkflowCall]:
    return [_call(task, "transform_feed", "transform_webpage_to_feed", dict(task.parameters))]


def _compile_export(task: ResolvedTask) -> list[WorkflowCall]:
    return [_call(task, "export_feed", "export_financial_feed", dict(task.parameters))]


def _compile_web(task: ResolvedTask) -> list[WorkflowCall]:
    url = str(task.parameters.get("url") or "").strip()
    query = str(task.parameters.get("query") or task.candidate.objective).strip()
    if url:
        return [
            _call(
                task,
                "fetch_public_page",
                "webfetch",
                {
                    "url": url,
                    **_params(task, {"format"}),
                },
            )
        ]
    return [
        _call(
            task,
            "search_public_web",
            "websearch",
            {
                "query": query,
                **_params(task, {"numResults", "livecrawl", "type", "contextMaxCharacters", "includeContent"}),
            },
        )
    ]


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


def _require(
    *,
    when: Mapping[str, Iterable[Any]] | None = None,
    entities: bool = False,
    confirmation: bool = False,
) -> ParameterRequirement:
    return ParameterRequirement(
        when=tuple((key, frozenset(values)) for key, values in (when or {}).items()),
        requires_entities=entities,
        confirmation_required=confirmation,
    )


def _spec(
    kind: StandardTaskKind,
    title: str,
    description: str,
    tools: Iterable[str],
    compiler: Compiler,
    *,
    entities: bool = False,
    effect: EffectClass = EffectClass.READ,
    confirmation_policy: ConfirmationPolicy | None = None,
    confirmation_actions: Iterable[str] = (),
    max_tool_calls: int = 8,
    max_parallel_steps: int = 8,
    allow_partial_tool_failures: bool = False,
    enabled: bool = True,
    state_machine: Sequence[str] = (),
    requirements: Sequence[ParameterRequirement] = (),
    resources: Iterable[str] = (),
    input_resources: Iterable[TaskResource] = (),
    input_resource_parameters: Mapping[str, TaskResource] | None = None,
    parameter_output_resources: Mapping[str, TaskResource] | None = None,
    output_resources: Iterable[TaskResource] = (),
    collection: CollectionBehavior | None = None,
    result_processor: str | None = None,
    result_contract: str | None = None,
    supports_result_selection: bool = False,
    max_input_entities: int | None = None,
    max_output_entities: int | None = None,
) -> WorkflowSpec:
    explicit_input_resources = frozenset(input_resources)
    collection_behavior = (
        collection
        if collection is not None
        else CollectionBehavior.PASSTHROUGH if entities else CollectionBehavior.NONE
    )
    return WorkflowSpec(
        kind=kind,
        title=title,
        description=description,
        tool_whitelist=frozenset(tools),
        compiler=compiler,
        requires_entities=entities,
        effect=effect,
        confirmation_policy=(
            confirmation_policy
            if confirmation_policy is not None
            else (
                ConfirmationPolicy.NEVER
                if effect == EffectClass.READ
                else (
                    ConfirmationPolicy.ALWAYS
                    if effect
                    in {
                        EffectClass.DESTRUCTIVE,
                        EffectClass.EXTERNAL,
                        EffectClass.TRADE,
                    }
                    else ConfirmationPolicy.CONDITIONAL
                )
            )
        ),
        confirmation_actions=frozenset(confirmation_actions),
        max_tool_calls=max(1, min(int(max_tool_calls), 6000)),
        max_parallel_steps=max(1, min(int(max_parallel_steps), 8)),
        allow_partial_tool_failures=allow_partial_tool_failures,
        enabled=enabled,
        state_machine=tuple(state_machine),
        parameter_requirements=tuple(requirements),
        resource_bindings=frozenset(resources),
        input_resources=frozenset(
            {
                *explicit_input_resources,
                *({TaskResource.SECURITY_COLLECTION} if entities else set()),
                *(input_resource_parameters or {}).values(),
            }
        ),
        input_resource_parameters=MappingProxyType(dict(input_resource_parameters or {})),
        parameter_output_resources=MappingProxyType(dict(parameter_output_resources or {})),
        output_resources=frozenset(
            {
                *({TaskResource.SECURITY_COLLECTION} if collection_behavior != CollectionBehavior.NONE else set()),
                *(parameter_output_resources or {}).values(),
                *output_resources,
            }
        ),
        collection_behavior=collection_behavior,
        result_processor=result_processor,
        result_contract=result_contract,
        supports_result_selection=supports_result_selection,
        max_input_entities=(
            max_input_entities
            if max_input_entities is not None
            else 300 if (entities or TaskResource.SECURITY_COLLECTION in explicit_input_resources) else None
        ),
        max_output_entities=(
            max_output_entities
            if max_output_entities is not None
            else 300 if collection_behavior != CollectionBehavior.NONE else None
        ),
    )


_WORKFLOW_REGISTRY: dict[StandardTaskKind, WorkflowSpec] = {
    StandardTaskKind.GENERAL_RESPONSE: _spec(
        StandardTaskKind.GENERAL_RESPONSE,
        "通用回答",
        "无需外部或实时数据的日常知识、解释、写作或计算。",
        (),
        _compile_no_tools,
    ),
    StandardTaskKind.SECURITY_LOOKUP: _spec(
        StandardTaskKind.SECURITY_LOOKUP,
        "证券识别",
        "按名称、代码、市场或行业查询证券身份。",
        {"search_stocks"},
        _compile_security_lookup,
        collection=CollectionBehavior.SOURCE,
    ),
    StandardTaskKind.REALTIME_QUOTE: _spec(
        StandardTaskKind.REALTIME_QUOTE,
        "实时行情",
        "查询一只或多只证券的当前行情。",
        {"get_realtime_quotes"},
        _compile_realtime_quote,
        entities=True,
    ),
    StandardTaskKind.PRICE_HISTORY: _spec(
        StandardTaskKind.PRICE_HISTORY,
        "历史行情",
        "查询近期或指定日期区间的历史行情。",
        {"get_kline", "get_history_data"},
        _compile_price_history,
        entities=True,
    ),
    StandardTaskKind.TECHNICAL_ANALYSIS: _spec(
        StandardTaskKind.TECHNICAL_ANALYSIS,
        "技术分析",
        "计算趋势、动量、波动和量价技术指标。",
        {"get_technical_indicators"},
        _compile_technical,
        entities=True,
    ),
    StandardTaskKind.FUNDAMENTAL_ANALYSIS: _spec(
        StandardTaskKind.FUNDAMENTAL_ANALYSIS,
        "基本面分析",
        "分析公司资料、核心财务、主营构成和股东结构。",
        {
            "get_stock_info",
            "get_financials",
            "get_business_segments",
            "get_shareholder_structure",
            "get_multi_stock_snapshot",
        },
        _compile_fundamental,
        entities=True,
    ),
    StandardTaskKind.VALUATION_ANALYSIS: _spec(
        StandardTaskKind.VALUATION_ANALYSIS,
        "估值分析",
        "分析当前、历史、预期和同行相对估值。",
        {"get_valuation_ratios", "get_consensus_estimates", "get_peer_comparison", "get_multi_stock_snapshot"},
        _compile_valuation,
        entities=True,
    ),
    StandardTaskKind.FINANCIAL_STATEMENT_ANALYSIS: _spec(
        StandardTaskKind.FINANCIAL_STATEMENT_ANALYSIS,
        "财报分析",
        "分析资产负债表、利润表和现金流量表。",
        {"get_balance_sheet", "get_income_statement", "get_cashflow"},
        _compile_statements,
        entities=True,
    ),
    StandardTaskKind.NEWS_ANALYSIS: _spec(
        StandardTaskKind.NEWS_ANALYSIS,
        "新闻分析",
        "查询公司或主题新闻并分析影响。",
        {"search_news", "get_announcements", "search_financial_news"},
        _compile_news,
    ),
    StandardTaskKind.ANNOUNCEMENT_ANALYSIS: _spec(
        StandardTaskKind.ANNOUNCEMENT_ANALYSIS,
        "公告分析",
        "查询并分析正式公司公告。",
        {"get_announcements"},
        _compile_announcements,
        entities=True,
    ),
    StandardTaskKind.RISK_ANALYSIS: _spec(
        StandardTaskKind.RISK_ANALYSIS,
        "风险分析",
        "获取公告与新闻证据并由模型研判风险。",
        {"get_announcements", "get_risk_events"},
        _compile_risk,
        entities=True,
    ),
    StandardTaskKind.REGULATORY_ANALYSIS: _spec(
        StandardTaskKind.REGULATORY_ANALYSIS,
        "监管信息",
        "查询交易所披露、问询、项目和上市监管动态。",
        {"get_regulatory_updates"},
        _compile_regulatory,
    ),
    StandardTaskKind.RESEARCH_REPORT_ANALYSIS: _spec(
        StandardTaskKind.RESEARCH_REPORT_ANALYSIS,
        "个股研报",
        "查询单只证券的券商研报和一致预期证据。",
        {"get_research_report"},
        _compile_reports,
        entities=True,
    ),
    StandardTaskKind.CATALYST_ANALYSIS: _spec(
        StandardTaskKind.CATALYST_ANALYSIS,
        "未来催化事件",
        "核验公司未来6—12个月具有明确时间窗和可回查来源的催化事件。",
        {"analyze_stock_catalysts"},
        _compile_catalyst_analysis,
        entities=True,
        max_tool_calls=8,
        max_parallel_steps=2,
    ),
    StandardTaskKind.SOCIAL_SENTIMENT_ANALYSIS: _spec(
        StandardTaskKind.SOCIAL_SENTIMENT_ANALYSIS,
        "舆情分析",
        "采样并分析个股公开讨论情绪。",
        {"get_social_sentiment"},
        _compile_sentiment,
        entities=True,
    ),
    StandardTaskKind.STOCK_COMPARISON: _spec(
        StandardTaskKind.STOCK_COMPARISON,
        "股票对比",
        "横向比较多只证券的行情、估值、技术与财务。",
        {"get_multi_stock_snapshot", "get_peer_comparison"},
        _compile_comparison,
        entities=True,
    ),
    StandardTaskKind.STOCK_DEEP_RESEARCH: _spec(
        StandardTaskKind.STOCK_DEEP_RESEARCH,
        "个股深度研究",
        "收集完整业务、财务、估值、交易状态和风险证据。",
        {"get_multi_stock_decision_evidence"},
        _compile_decision_packet,
        entities=True,
    ),
    StandardTaskKind.INVESTMENT_DECISION: _spec(
        StandardTaskKind.INVESTMENT_DECISION,
        "专业买入分析",
        "对完整股票集合逐只执行资深分析师八维布尔闸门；首个未达到准入条件立即停止，"
        "关键来源或执行故障单独标记分析未完成，八维全部通过才可买入。",
        {
            "prepare_market_mainline_snapshot",
            "evaluate_market_mainline_gate",
            "evaluate_multi_stock_buy_criteria",
        },
        _compile_professional_buy_analysis,
        entities=True,
        max_tool_calls=302,
        max_parallel_steps=4,
        allow_partial_tool_failures=True,
        result_contract="investment_decision",
    ),
    StandardTaskKind.MARKET_OVERVIEW: _spec(
        StandardTaskKind.MARKET_OVERVIEW,
        "市场概览",
        "分析指数、市场宽度与整体交易状态。",
        {"get_market_status", "get_market_breadth", "get_index_data"},
        _compile_market,
    ),
    StandardTaskKind.MARKET_MAINLINE_RESEARCH: _spec(
        StandardTaskKind.MARKET_MAINLINE_RESEARCH,
        "市场主线研究",
        "基于政策、产业供需、技术路线、资本开支和机构策略证据，研判未来一至六个月的当前主线与候选主线；指数涨跌和单日热度不能单独建立主线。",
        {"prepare_market_mainline_snapshot"},
        _compile_market_mainline_research,
        max_tool_calls=1,
        max_parallel_steps=1,
    ),
    StandardTaskKind.SECTOR_ANALYSIS: _spec(
        StandardTaskKind.SECTOR_ANALYSIS,
        "板块分析",
        "比较行业或概念板块强弱、资金和近期信息。",
        {"get_sector_list", "get_sector_flow", "search_financial_news"},
        _compile_sector,
    ),
    StandardTaskKind.CAPITAL_FLOW_ANALYSIS: _spec(
        StandardTaskKind.CAPITAL_FLOW_ANALYSIS,
        "资金流分析",
        "分析个股多周期资金流持续性。",
        {"get_stock_capital_flow"},
        _compile_capital_flow,
        entities=True,
    ),
    StandardTaskKind.MACRO_ANALYSIS: _spec(
        StandardTaskKind.MACRO_ANALYSIS,
        "宏观分析",
        "分析宏观指标、利率或货币政策操作。",
        {"get_macro_indicator", "get_bond_yield", "get_monetary_policy_operations", "search_research_library"},
        _compile_macro,
    ),
    StandardTaskKind.INDUSTRY_RESEARCH: _spec(
        StandardTaskKind.INDUSTRY_RESEARCH,
        "产业研究",
        "拆解产业受益链，从项目完整实时板块目录中选择实际存在的受益板块，并产出可供后续找股复用的结构化板块集合。",
        {"get_domain_board_catalog"},
        _compile_industry,
        output_resources={TaskResource.DOMAIN_COLLECTION},
        result_processor="ranked_domain_selection",
        result_contract="industry_ranked_domains",
        supports_result_selection=True,
    ),
    StandardTaskKind.THEME_STOCK_DISCOVERY: _spec(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        "领域找股",
        "按语义产业领域从内部结构化板块和完整股票池找候选。",
        {"get_domain_stock_candidates"},
        _compile_theme_discovery,
        resources={"concept_board_catalog"},
        input_resource_parameters={"domains": TaskResource.DOMAIN_COLLECTION},
        parameter_output_resources={"domains": TaskResource.DOMAIN_COLLECTION},
        collection=CollectionBehavior.SOURCE,
        result_contract="theme_stock_discovery",
        max_output_entities=6000,
    ),
    StandardTaskKind.THEME_BUSINESS_EVIDENCE: _spec(
        StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        "逐股主题业务分析",
        "对结构化候选集合中的每一只股票分别建立公司资料、主营、公告、个股新闻和个股研报证据档案，再逐股判断主题匹配和发展强度；不得用行业新闻命中代替逐股分析。",
        {
            "get_company_theme_evidence",
            "search_financial_news",
            "search_research_library",
        },
        _compile_theme_evidence,
        requirements=(
            _require(
                when={"candidate_scope": {"candidate_collection"}},
                entities=True,
            ),
        ),
        input_resources={TaskResource.SECURITY_COLLECTION},
        input_resource_parameters={"domains": TaskResource.DOMAIN_COLLECTION},
        collection=CollectionBehavior.SOURCE,
        result_processor="company_evidence_binding",
        result_contract="theme_business_evidence",
        max_tool_calls=6000,
        max_parallel_steps=4,
        allow_partial_tool_failures=True,
        max_input_entities=6000,
        max_output_entities=6000,
    ),
    StandardTaskKind.STOCK_SCREENING: _spec(
        StandardTaskKind.STOCK_SCREENING,
        "股票筛选",
        "按完整强类型筛选规格执行全市场量化筛选。",
        {"screen_atr_volatility_stocks"},
        _compile_screening,
        collection=CollectionBehavior.SOURCE,
        result_contract="stock_screening",
    ),
    StandardTaskKind.COLLECTION_FINANCIAL_FILTER: _spec(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        "集合财务筛选",
        "用一组强类型财务条件筛选完整公司集合；所有条件共同决定最终保留集合。",
        {"get_multi_stock_financials"},
        _compile_collection_filter,
        entities=True,
        max_tool_calls=104,
        max_parallel_steps=4,
        collection=CollectionBehavior.FILTER,
        result_contract="collection_financial_filter",
    ),
    StandardTaskKind.WATCHLIST_QUERY: _spec(
        StandardTaskKind.WATCHLIST_QUERY,
        "自选查询",
        "查看自选或在指定自选集合内按语义领域筛选。",
        {"manage_watchlist", "filter_watchlist_by_theme"},
        _compile_watchlist_query,
        resources={"concept_board_catalog"},
    ),
    StandardTaskKind.WATCHLIST_MUTATION: _spec(
        StandardTaskKind.WATCHLIST_MUTATION,
        "自选修改",
        "按用户明确要求添加或移除自选股。",
        {"manage_watchlist"},
        _compile_watchlist_mutation,
        entities=True,
        effect=EffectClass.MUTATION,
        confirmation_actions={"add", "remove"},
    ),
    StandardTaskKind.WATCHLIST_GROUP_MANAGEMENT: _spec(
        StandardTaskKind.WATCHLIST_GROUP_MANAGEMENT,
        "自选分组管理",
        "查看、创建、重命名、删除或修改自选分组成员。",
        {"manage_watchlist_groups"},
        _compile_group,
        effect=EffectClass.MUTATION,
        confirmation_actions={"create", "rename", "delete", "add", "remove"},
        requirements=(_require(when={"action": {"add", "remove"}}, entities=True),),
    ),
    StandardTaskKind.DATA_HEALTH: _spec(
        StandardTaskKind.DATA_HEALTH,
        "数据健康",
        "查看股票池、行情和财务数据覆盖与维护状态。",
        {"get_data_health"},
        _compile_data_health,
    ),
    StandardTaskKind.FORMAL_ANALYSIS: _spec(
        StandardTaskKind.FORMAL_ANALYSIS,
        "正式分析",
        "启动持久化单股报告，或查询其运行状态。",
        {"run_stock_analysis", "get_analysis_status"},
        _compile_formal_analysis,
        effect=EffectClass.MUTATION,
        confirmation_actions={"start"},
        requirements=(_require(when={"action": {"start"}}, entities=True),),
    ),
    StandardTaskKind.ANALYSIS_HISTORY: _spec(
        StandardTaskKind.ANALYSIS_HISTORY,
        "分析历史",
        "搜索、读取或删除已保存的正式分析报告。",
        {"search_analysis_history", "read_analysis_report", "delete_analysis_history"},
        _compile_history,
        effect=EffectClass.MUTATION,
        confirmation_actions={"delete"},
    ),
    StandardTaskKind.ANALYSIS_TEMPLATE_MANAGEMENT: _spec(
        StandardTaskKind.ANALYSIS_TEMPLATE_MANAGEMENT,
        "分析模板",
        "查看或管理正式分析模板。",
        {"manage_analysis_templates"},
        _compile_template,
        effect=EffectClass.MUTATION,
        confirmation_actions={"create", "update", "set_default", "delete"},
    ),
    StandardTaskKind.BATCH_ANALYSIS: _spec(
        StandardTaskKind.BATCH_ANALYSIS,
        "批量分析",
        "按明确范围启动正式批量分析。",
        {"run_batch_analysis"},
        _compile_batch_analysis,
        effect=EffectClass.MUTATION,
        confirmation_policy=ConfirmationPolicy.ALWAYS,
        requirements=(
            _require(when={"scope": {"symbols"}}, entities=True),
            _require(when={"scope": {"group"}}, confirmation=True),
            _require(when={"scope": {"watchlist", "configured"}}, confirmation=True),
        ),
    ),
    StandardTaskKind.BATCH_RUN_MANAGEMENT: _spec(
        StandardTaskKind.BATCH_RUN_MANAGEMENT,
        "批量任务管理",
        "查看或控制批量分析任务。",
        {"manage_batch_run"},
        _compile_batch_management,
        effect=EffectClass.MUTATION,
        confirmation_actions={"pause", "continue", "resume_failed", "regenerate_report", "notify", "stop", "delete"},
    ),
    StandardTaskKind.ANALYSIS_SCHEDULE_MANAGEMENT: _spec(
        StandardTaskKind.ANALYSIS_SCHEDULE_MANAGEMENT,
        "定时分析",
        "查看或修改自动分析计划。",
        {"manage_analysis_schedule"},
        _compile_schedule,
        effect=EffectClass.MUTATION,
        confirmation_actions={"update"},
    ),
    StandardTaskKind.NOTIFICATION: _spec(
        StandardTaskKind.NOTIFICATION,
        "通知",
        "检查通知配置或发送用户明确指定的内容。",
        {"get_notification_status", "send_notification"},
        _compile_notification,
        effect=EffectClass.EXTERNAL,
        confirmation_policy=ConfirmationPolicy.CONDITIONAL,
        confirmation_actions={"send"},
    ),
    StandardTaskKind.FINANCIAL_SOURCE_DISCOVERY: _spec(
        StandardTaskKind.FINANCIAL_SOURCE_DISCOVERY,
        "资讯源查询",
        "列出或检查指定财经资讯源。",
        {"list_financial_sources", "inspect_financial_source"},
        _compile_source_discovery,
    ),
    StandardTaskKind.FINANCIAL_FEED_READ: _spec(
        StandardTaskKind.FINANCIAL_FEED_READ,
        "Feed读取",
        "按已知路由读取一个财经 Feed。",
        {"read_financial_feed"},
        _compile_feed_read,
    ),
    StandardTaskKind.FINANCIAL_ARTICLE_READ: _spec(
        StandardTaskKind.FINANCIAL_ARTICLE_READ,
        "文章读取",
        "读取用户指定的一篇财经资讯正文。",
        {"read_financial_article"},
        _compile_article,
    ),
    StandardTaskKind.WEBPAGE_FEED_TRANSFORM: _spec(
        StandardTaskKind.WEBPAGE_FEED_TRANSFORM,
        "网页转Feed",
        "按用户提供的网页和选择器生成 Feed 预览。",
        {"transform_webpage_to_feed"},
        _compile_transform,
    ),
    StandardTaskKind.FINANCIAL_FEED_EXPORT: _spec(
        StandardTaskKind.FINANCIAL_FEED_EXPORT,
        "Feed导出",
        "导出用户明确指定的财经 Feed。",
        {"export_financial_feed"},
        _compile_export,
        effect=EffectClass.EXTERNAL,
        confirmation_policy=ConfirmationPolicy.ALWAYS,
    ),
    StandardTaskKind.PUBLIC_WEB_RESEARCH: _spec(
        StandardTaskKind.PUBLIC_WEB_RESEARCH,
        "公开网页研究",
        "当用户明确要求联网，或完成当前时效性研究目标确实缺少内部权威来源时，检索或读取公开网页；不得替代已有结构化金融能力。",
        {"websearch", "webfetch"},
        _compile_web,
    ),
    StandardTaskKind.TRADE_EXECUTION: _spec(
        StandardTaskKind.TRADE_EXECUTION,
        "交易执行",
        "交易类请求只能进入独立状态机；当前系统未接入账户、风控和下单工具。",
        (),
        _compile_no_tools,
        effect=EffectClass.TRADE,
        confirmation_policy=ConfirmationPolicy.ALWAYS,
        enabled=False,
        state_machine=("参数校验", "账户检查", "风控检查", "用户确认", "下单", "订单状态"),
    ),
}

# Neither the planner nor request-local code can mutate the production
# Workflow Registry after module initialization.
WORKFLOW_REGISTRY: Mapping[StandardTaskKind, WorkflowSpec] = MappingProxyType(_WORKFLOW_REGISTRY)


def workflow_for(kind: StandardTaskKind) -> WorkflowSpec:
    return WORKFLOW_REGISTRY[kind]


def compile_task(task: ResolvedTask) -> list[WorkflowCall]:
    spec = workflow_for(task.kind)
    if not spec.enabled:
        raise WorkflowCompileError(f"{spec.title}当前不可执行")
    if spec.supports_result_selection and task.result_selection is None:
        raise WorkflowCompileError(f"{task.kind.value} requires a typed result_selection")
    if not spec.supports_result_selection and task.result_selection is not None:
        raise WorkflowCompileError(f"{task.kind.value} does not support result_selection")
    if spec.requires_entities and not task.symbols:
        raise WorkflowCompileError(f"{task.kind.value} requires resolved entities")
    conditional_issues = parameter_requirement_issues(
        task.candidate,
        spec,
        symbols=task.symbols,
        check_confirmation=False,
    )
    if conditional_issues:
        raise WorkflowCompileError(
            f"{task.kind.value} violates its conditional contract: " + "; ".join(conditional_issues)
        )
    calls = spec.compiler(task)
    if len(calls) > spec.max_tool_calls:
        raise WorkflowCompileError(f"{task.kind.value} compiled {len(calls)} calls, exceeding {spec.max_tool_calls}")
    for call in calls:
        if call.tool_name not in spec.tool_whitelist:
            raise WorkflowCompileError(f"{call.tool_name} is outside the {task.kind.value} whitelist")
    return calls


def registered_workflow_tools() -> frozenset[str]:
    return frozenset(tool for spec in WORKFLOW_REGISTRY.values() for tool in spec.tool_whitelist)


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
