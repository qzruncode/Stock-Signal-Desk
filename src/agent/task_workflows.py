# -*- coding: utf-8 -*-
"""Program-owned standard tasks and immutable workflow registry.

The language model may choose a :class:`StandardTaskKind` and fill its semantic
parameters.  It never sees or chooses the tools below.  Every executable call
is compiled from this registry, which is the single authority for tool access,
ordering, call budgets and side-effect policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.agent.result_contracts import CollectionFinancialFilterSpec, DomainBoardQuerySpec


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


class StandardTask(BaseModel):
    """One model-proposed task before the program compiles any tool call."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    kind: StandardTaskKind
    objective: str = Field(min_length=1, max_length=400)
    entity_scope: EntityScope = EntityScope.NONE
    entities: list[str] = Field(default_factory=list, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    output_requirements: list[str] = Field(default_factory=list, max_length=20)
    confirmation: ConfirmationState = ConfirmationState.NOT_REQUIRED
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

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
    """A validated candidate DAG emitted by the semantic planner."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[StandardTask] = Field(default_factory=list, max_length=12)
    needs_clarification: bool = False
    clarification_question: str | None = None
    source: str = "semantic"

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
class ResolvedTask:
    candidate: StandardTask
    symbols: tuple[str, ...] = ()

    @property
    def task_id(self) -> str:
        return self.candidate.task_id

    @property
    def kind(self) -> StandardTaskKind:
        return self.candidate.kind

    @property
    def parameters(self) -> Mapping[str, Any]:
        return self.candidate.parameters


@dataclass(frozen=True)
class WorkflowCall:
    task_id: str
    step_id: str
    tool_name: str
    arguments: dict[str, Any]
    depends_on_steps: tuple[str, ...] = ()


class WorkflowCompileError(ValueError):
    pass


Compiler = Callable[[ResolvedTask], list[WorkflowCall]]


@dataclass(frozen=True)
class ParameterRequirement:
    """Declarative conditional contract enforced before a Tool is exposed.

    ``when`` is an AND of parameter predicates; each predicate accepts one of
    the configured values.  ``required_all`` checks presence/non-empty values,
    while ``required_any`` requires at least one meaningful (non-empty and, for
    booleans, true) value.  This keeps action-specific validation in the fixed
    Workflow Registry instead of scattering it through prompt keywords or Tool
    implementations.
    """

    when: tuple[tuple[str, frozenset[Any]], ...] = ()
    required_all: frozenset[str] = field(default_factory=frozenset)
    required_any: frozenset[str] = field(default_factory=frozenset)
    requires_entities: bool = False
    confirmation_required: bool = False

    def applies(self, parameters: Mapping[str, Any]) -> bool:
        return all(parameters.get(key) in values for key, values in self.when)

    def planner_contract(self) -> dict[str, Any]:
        return {
            "when": {key: sorted(values) for key, values in self.when},
            "required_parameters": sorted(self.required_all),
            "one_of_parameters": sorted(self.required_any),
            "requires_entities": self.requires_entities,
            "confirmation_required": self.confirmation_required,
        }


@dataclass(frozen=True)
class WorkflowSpec:
    kind: StandardTaskKind
    title: str
    description: str
    tool_whitelist: frozenset[str]
    compiler: Compiler
    required_parameters: frozenset[str] = field(default_factory=frozenset)
    allowed_parameters: frozenset[str] = field(default_factory=frozenset)
    requires_entities: bool = False
    max_tool_calls: int = 8
    max_parallel_steps: int = 8
    max_attempts: int = 1
    effect: EffectClass = EffectClass.READ
    confirmation_actions: frozenset[str] = field(default_factory=frozenset)
    enabled: bool = True
    state_machine: tuple[str, ...] = ()
    parameter_notes: tuple[str, ...] = ()
    parameter_enums: Mapping[str, frozenset[Any]] = field(default_factory=dict)
    parameter_requirements: tuple[ParameterRequirement, ...] = ()

    def planner_contract(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "description": self.description,
            "requires_entities": self.requires_entities,
            "required_parameters": sorted(self.required_parameters),
            "allowed_parameters": sorted(self.allowed_parameters),
            "effect": self.effect.value,
            "enabled": self.enabled,
            "parameter_notes": list(self.parameter_notes),
            "parameter_enums": {
                key: sorted(values)
                for key, values in self.parameter_enums.items()
            },
            "conditional_requirements": [
                requirement.planner_contract()
                for requirement in self.parameter_requirements
            ],
        }


def _parameter_is_present(parameters: Mapping[str, Any], key: str) -> bool:
    if key not in parameters or parameters[key] is None:
        return False
    value = parameters[key]
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return bool(value)
    return True


def _parameter_is_meaningful(parameters: Mapping[str, Any], key: str) -> bool:
    if not _parameter_is_present(parameters, key):
        return False
    value = parameters[key]
    return value is not False


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
        missing = sorted(
            key
            for key in requirement.required_all
            if not _parameter_is_present(task.parameters, key)
        )
        if missing:
            issues.append(f"missing conditional parameters {missing}")
        if requirement.required_any and not any(
            _parameter_is_meaningful(task.parameters, key)
            for key in requirement.required_any
        ):
            issues.append(
                "requires at least one of " + str(sorted(requirement.required_any))
            )
        if requirement.requires_entities and symbols is not None and not symbols:
            issues.append("requires resolved entities for the selected operation")
        if (
            check_confirmation
            and requirement.confirmation_required
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
) -> WorkflowCall:
    return WorkflowCall(
        task_id=task.task_id,
        step_id=step_id,
        tool_name=tool_name,
        arguments=dict(arguments or {}),
        depends_on_steps=tuple(depends_on),
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
        args = {"query": query, **_params(task, {"topic", "days", "limit", "include_content", "fallback_to_web"})}
        return [_call(task, "multi_company_news", "search_financial_news", args)]
    calls = _per_symbol(task, ["search_news"], {"search_news": {"days", "limit", "use_cache"}}, max_symbols=2)
    calls.extend(_per_symbol(task, ["get_announcements"], {"get_announcements": {"days", "limit"}}, max_symbols=2))
    return calls


def _compile_announcements(task: ResolvedTask) -> list[WorkflowCall]:
    return _per_symbol(
        task,
        ["get_announcements"],
        {"get_announcements": {"days", "type", "limit"}},
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
    return [_call(task, "regulatory_updates", "get_regulatory_updates", _params(
        task,
        {
            "keyword", "event_type", "market", "days", "limit", "include_content",
            "fallback_to_web", "project_type", "project_stage", "project_status",
        },
    ))]


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
    return [_call(
        task,
        "decision_evidence",
        "get_multi_stock_decision_evidence",
        {"symbols": _symbols_csv(task), "thesis": str(task.parameters.get("thesis") or "")},
    )]


def _compile_catalyst_analysis(task: ResolvedTask) -> list[WorkflowCall]:
    symbols = list(task.symbols)
    if not symbols:
        raise WorkflowCompileError("catalyst_analysis requires at least one resolved entity")
    if len(symbols) > 8:
        raise WorkflowCompileError(
            "catalyst_analysis supports at most 8 entities per standard task; narrow the scope"
        )
    return [
        _call(
            task,
            f"catalyst_{index}",
            "analyze_stock_catalysts",
            {"symbols": symbol},
        )
        for index, symbol in enumerate(symbols, 1)
    ]


def _compile_strict_buy_decision(task: ResolvedTask) -> list[WorkflowCall]:
    symbols = list(task.symbols)
    if not symbols:
        raise WorkflowCompileError("investment_decision requires at least one resolved entity")
    if len(symbols) > 300:
        raise WorkflowCompileError(
            "investment_decision supports at most 300 entities; narrow the collection first"
        )
    thesis = str(task.parameters.get("thesis") or "").strip()
    return [
        _call(
            task,
            f"strict_buy_batch_{batch_index}",
            "evaluate_multi_stock_buy_criteria",
            # One strict stock evaluation can consume most of the five-minute
            # tool envelope.  Two-stock shards match the process runner's safe
            # concurrency and prevent an eight-stock shard from timing out
            # after only its first waves have finished.
            {"symbols": ",".join(symbols[start:start + 2]), "thesis": thesis},
        )
        for batch_index, start in enumerate(range(0, len(symbols), 2), 1)
    ]


def _compile_market(task: ResolvedTask) -> list[WorkflowCall]:
    calls = [
        _call(task, "market_status", "get_market_status"),
        _call(task, "market_breadth", "get_market_breadth"),
    ]
    if bool(task.parameters.get("include_index", True)):
        calls.append(_call(task, "index", "get_index_data", _params(task, {"index_code", "days"})))
    return calls


def _compile_sector(task: ResolvedTask) -> list[WorkflowCall]:
    base = _params(task, {"type"})
    flow = _params(task, {"type", "period", "top_n"})
    calls = [
        _call(task, "sector_list", "get_sector_list", base),
        _call(task, "sector_flow", "get_sector_flow", flow),
    ]
    query = str(task.parameters.get("query") or task.candidate.objective).strip()
    if query:
        calls.append(_call(task, "sector_news", "search_financial_news", {
            "query": query,
            "topic": "industry",
            **_params(task, {"days", "limit", "include_content"}),
        }))
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
        _call(task, f"macro_{index}", "get_macro_indicator", {
            "indicator": indicator,
            **_params(task, {"periods"}),
        })
        for index, indicator in enumerate(indicators[:5], 1)
    ]
    if bool(task.parameters.get("include_bond_yield")):
        calls.append(_call(task, "bond_yield", "get_bond_yield", _params(task, {"country", "term", "days"})))
    if bool(task.parameters.get("include_monetary_operations")):
        calls.append(_call(task, "monetary_operations", "get_monetary_policy_operations", _params(
            task, {"days", "instrument", "limit", "include_content", "fallback_to_web"}
        )))
    query = str(task.parameters.get("query") or "").strip()
    if query and len(calls) < 8:
        calls.append(_call(task, "macro_research", "search_research_library", {
            "query": query,
            "category": "macro",
            **_params(task, {"days", "limit", "include_content", "fallback_to_web"}),
        }))
    if not calls:
        raise WorkflowCompileError("macro_analysis requires indicators or an explicitly requested macro source")
    return calls


def _compile_industry(task: ResolvedTask) -> list[WorkflowCall]:
    query = str(task.parameters.get("query") or task.candidate.objective).strip()
    common = _params(task, {"days", "limit", "include_content", "fallback_to_web"})
    return [
        _call(task, "industry_news", "search_financial_news", {"query": query, "topic": "industry", **common}),
        _call(task, "industry_research", "search_research_library", {"query": query, "category": "industry", **common}),
    ]


def _compile_theme_discovery(task: ResolvedTask) -> list[WorkflowCall]:
    domains = task.parameters.get("domains")
    if not isinstance(domains, list) or not domains:
        raise WorkflowCompileError("theme_stock_discovery requires a non-empty domains array")
    try:
        domain_specs = [
            DomainBoardQuerySpec.model_validate(domain).model_dump()
            for domain in domains
        ]
    except Exception as exc:
        raise WorkflowCompileError(
            f"theme_stock_discovery requires resolved domain-board objects: {exc}"
        ) from exc
    context_theme = task.parameters.get("context_theme")
    args = {"domains": domain_specs, **_params(task, {"limit_per_domain"})}
    # ``context_theme`` only narrows an already valid domain lookup.  If the
    # planner returns a sentence instead of a short board name, omit this
    # optional optimization rather than blocking the complete task.
    if isinstance(context_theme, str) and 0 < len(context_theme.strip()) <= 12:
        args["context_theme"] = context_theme.strip()
    return [_call(task, "domain_candidates", "get_domain_stock_candidates", args)]


def _compile_theme_evidence(task: ResolvedTask) -> list[WorkflowCall]:
    theme = str(task.parameters.get("theme") or "").strip()
    query = str(task.parameters.get("query") or task.candidate.objective).strip()
    if not theme:
        raise WorkflowCompileError("theme_business_evidence requires theme")
    common = _params(task, {"days", "limit", "include_content", "fallback_to_web"})
    candidate_args: dict[str, Any] = {"theme": theme}
    if task.parameters.get("candidate_limit") is not None:
        candidate_args["limit"] = task.parameters["candidate_limit"]
    return [
        _call(task, "theme_candidates", "get_theme_stock_candidates", candidate_args),
        _call(task, "business_news", "search_financial_news", {"query": query, "topic": "industry", **common}),
        _call(task, "business_research", "search_research_library", {"query": query, "category": "industry", **common}),
    ]


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
        raise WorkflowCompileError(
            f"collection_financial_filter has an invalid semantic contract: {exc}"
        ) from exc
    common_arguments: dict[str, Any] = {
        "metric": filter_spec.metric,
        "period_basis": filter_spec.period_basis,
    }
    if filter_spec.fiscal_year is not None:
        common_arguments["fiscal_year"] = filter_spec.fiscal_year
    calls = [
        _call(task, f"financial_batch_{index // 12 + 1}", "get_multi_stock_financials", {
            "symbols": ",".join(symbols[index:index + 12]),
            **common_arguments,
        })
        for index in range(0, len(symbols), 12)
    ]
    if len(calls) > 8:
        raise WorkflowCompileError("collection_financial_filter supports at most 96 companies per turn")
    return calls


def _compile_watchlist_query(task: ResolvedTask) -> list[WorkflowCall]:
    theme = str(task.parameters.get("theme") or "").strip()
    if theme:
        return [_call(task, "filter_watchlist", "filter_watchlist_by_theme", {
            "theme": theme,
            **_params(task, {"group"}),
        })]
    return [_call(task, "list_watchlist", "manage_watchlist", {"action": "list"})]


def _compile_watchlist_mutation(task: ResolvedTask) -> list[WorkflowCall]:
    action = str(task.parameters.get("action") or "")
    return [_call(task, "mutate_watchlist", "manage_watchlist", {
        "action": action,
        "symbols": _symbols_csv(task),
    })]


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
    return [_call(task, "start_analysis", "run_stock_analysis", {
        "symbol": task.symbols[0] if task.symbols else "",
        **_params(task, {"force_refresh", "notify_on_complete", "prompt_template_id"}),
    })]


def _compile_history(task: ResolvedTask) -> list[WorkflowCall]:
    action = str(task.parameters.get("action") or "search")
    if action == "read":
        return [_call(task, "read_report", "read_analysis_report", _params(
            task, {"record_id", "include_markdown", "include_news"}
        ))]
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
    args = _params(task, {
        "scope", "group_name", "analysis_mode", "prompt_template_id", "force_refresh",
    })
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
        return [_call(task, "inspect_source", "inspect_financial_source", _params(
            task, {"route_path", "keyword", "force"}
        ))]
    return [_call(task, "list_sources", "list_financial_sources", _params(
        task, {"keyword", "namespace", "capability", "force", "limit"}
    ))]


def _compile_feed_read(task: ResolvedTask) -> list[WorkflowCall]:
    return [_call(task, "read_feed", "read_financial_feed", _params(
        task, {"route_path", "params", "options", "namespace", "limit", "force"}
    ))]


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
        return [_call(task, "fetch_public_page", "webfetch", {
            "url": url,
            **_params(task, {"format", "timeout"}),
        })]
    return [_call(task, "search_public_web", "websearch", {
        "query": query,
        **_params(task, {"numResults", "livecrawl", "type", "contextMaxCharacters", "includeContent"}),
    })]


def _require(
    *,
    when: Mapping[str, Iterable[Any]] | None = None,
    all_of: Iterable[str] = (),
    one_of: Iterable[str] = (),
    entities: bool = False,
    confirmation: bool = False,
) -> ParameterRequirement:
    return ParameterRequirement(
        when=tuple(
            (key, frozenset(values))
            for key, values in (when or {}).items()
        ),
        required_all=frozenset(all_of),
        required_any=frozenset(one_of),
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
    required: Iterable[str] = (),
    allowed: Iterable[str] = (),
    entities: bool = False,
    effect: EffectClass = EffectClass.READ,
    confirmation_actions: Iterable[str] = (),
    max_tool_calls: int = 8,
    max_parallel_steps: int = 8,
    max_attempts: int = 1,
    enabled: bool = True,
    state_machine: Sequence[str] = (),
    notes: Sequence[str] = (),
    enums: Mapping[str, Iterable[Any]] | None = None,
    requirements: Sequence[ParameterRequirement] = (),
) -> WorkflowSpec:
    return WorkflowSpec(
        kind=kind,
        title=title,
        description=description,
        tool_whitelist=frozenset(tools),
        compiler=compiler,
        required_parameters=frozenset(required),
        allowed_parameters=frozenset(allowed),
        requires_entities=entities,
        effect=effect,
        confirmation_actions=frozenset(confirmation_actions),
        max_tool_calls=max(1, min(int(max_tool_calls), 160)),
        max_parallel_steps=max(1, min(int(max_parallel_steps), 8)),
        max_attempts=max(1, min(int(max_attempts), 2)),
        enabled=enabled,
        state_machine=tuple(state_machine),
        parameter_notes=tuple(notes),
        parameter_enums=MappingProxyType({
            key: frozenset(values)
            for key, values in (enums or {}).items()
        }),
        parameter_requirements=tuple(requirements),
    )


_WORKFLOW_REGISTRY: dict[StandardTaskKind, WorkflowSpec] = {
    StandardTaskKind.GENERAL_RESPONSE: _spec(StandardTaskKind.GENERAL_RESPONSE, "通用回答", "无需外部或实时数据的日常知识、解释、写作或计算。", (), _compile_no_tools),
    StandardTaskKind.SECURITY_LOOKUP: _spec(StandardTaskKind.SECURITY_LOOKUP, "证券识别", "按名称、代码、市场或行业查询证券身份。", {"search_stocks"}, _compile_security_lookup, allowed={"query", "market", "sector", "limit"}),
    StandardTaskKind.REALTIME_QUOTE: _spec(StandardTaskKind.REALTIME_QUOTE, "实时行情", "查询一只或多只证券的当前行情。", {"get_realtime_quotes"}, _compile_realtime_quote, entities=True),
    StandardTaskKind.PRICE_HISTORY: _spec(StandardTaskKind.PRICE_HISTORY, "历史行情", "查询近期或指定日期区间的历史行情。", {"get_kline", "get_history_data"}, _compile_price_history, allowed={"start_date", "end_date", "count", "use_cache"}, entities=True, notes=("指定区间时 start_date 和 end_date 必须同时为 YYYYMMDD；否则用 count。",)),
    StandardTaskKind.TECHNICAL_ANALYSIS: _spec(StandardTaskKind.TECHNICAL_ANALYSIS, "技术分析", "计算趋势、动量、波动和量价技术指标。", {"get_technical_indicators"}, _compile_technical, allowed={"count"}, entities=True),
    StandardTaskKind.FUNDAMENTAL_ANALYSIS: _spec(StandardTaskKind.FUNDAMENTAL_ANALYSIS, "基本面分析", "分析公司资料、核心财务、主营构成和股东结构。", {"get_stock_info", "get_financials", "get_business_segments", "get_shareholder_structure", "get_multi_stock_snapshot"}, _compile_fundamental, allowed={"periods", "category"}, entities=True),
    StandardTaskKind.VALUATION_ANALYSIS: _spec(StandardTaskKind.VALUATION_ANALYSIS, "估值分析", "分析当前、历史、预期和同行相对估值。", {"get_valuation_ratios", "get_consensus_estimates", "get_peer_comparison", "get_multi_stock_snapshot"}, _compile_valuation, allowed={"with_history", "metric", "dimension"}, entities=True),
    StandardTaskKind.FINANCIAL_STATEMENT_ANALYSIS: _spec(StandardTaskKind.FINANCIAL_STATEMENT_ANALYSIS, "财报分析", "分析资产负债表、利润表和现金流量表。", {"get_balance_sheet", "get_income_statement", "get_cashflow"}, _compile_statements, allowed={"periods"}, entities=True),
    StandardTaskKind.NEWS_ANALYSIS: _spec(StandardTaskKind.NEWS_ANALYSIS, "新闻分析", "查询公司或主题新闻并分析影响。", {"search_news", "get_announcements", "search_financial_news"}, _compile_news, allowed={"query", "topic", "days", "limit", "use_cache", "include_content", "fallback_to_web"}, notes=("主题资讯使用 query；具体公司新闻使用 entities。",)),
    StandardTaskKind.ANNOUNCEMENT_ANALYSIS: _spec(StandardTaskKind.ANNOUNCEMENT_ANALYSIS, "公告分析", "查询并分析正式公司公告。", {"get_announcements"}, _compile_announcements, allowed={"days", "type", "limit"}, entities=True),
    StandardTaskKind.RISK_ANALYSIS: _spec(StandardTaskKind.RISK_ANALYSIS, "风险分析", "核验公告和规则筛查风险事件。", {"get_announcements", "get_risk_events"}, _compile_risk, allowed={"days", "limit"}, entities=True),
    StandardTaskKind.REGULATORY_ANALYSIS: _spec(StandardTaskKind.REGULATORY_ANALYSIS, "监管信息", "查询交易所披露、问询、项目和上市监管动态。", {"get_regulatory_updates"}, _compile_regulatory, allowed={"keyword", "event_type", "market", "days", "limit", "include_content", "fallback_to_web", "project_type", "project_stage", "project_status"}),
    StandardTaskKind.RESEARCH_REPORT_ANALYSIS: _spec(StandardTaskKind.RESEARCH_REPORT_ANALYSIS, "个股研报", "查询单只证券的券商研报和一致预期证据。", {"get_research_report"}, _compile_reports, allowed={"days", "limit"}, entities=True),
    StandardTaskKind.CATALYST_ANALYSIS: _spec(
        StandardTaskKind.CATALYST_ANALYSIS,
        "未来催化事件",
        "核验公司未来6—12个月具有明确时间窗和可回查来源的催化事件。",
        {"analyze_stock_catalysts"},
        _compile_catalyst_analysis,
        entities=True,
        max_tool_calls=8,
        max_parallel_steps=2,
        max_attempts=1,
        notes=("只研究催化事件；不替代完整九项买入判断，也不使用网络搜索。",),
    ),
    StandardTaskKind.SOCIAL_SENTIMENT_ANALYSIS: _spec(StandardTaskKind.SOCIAL_SENTIMENT_ANALYSIS, "舆情分析", "采样并分析个股公开讨论情绪。", {"get_social_sentiment"}, _compile_sentiment, allowed={"days", "limit", "max_pages"}, entities=True),
    StandardTaskKind.STOCK_COMPARISON: _spec(StandardTaskKind.STOCK_COMPARISON, "股票对比", "横向比较多只证券的行情、估值、技术与财务。", {"get_multi_stock_snapshot", "get_peer_comparison"}, _compile_comparison, allowed={"include_peers", "dimension"}, entities=True),
    StandardTaskKind.STOCK_DEEP_RESEARCH: _spec(StandardTaskKind.STOCK_DEEP_RESEARCH, "个股深度研究", "收集完整业务、财务、估值、交易状态和风险证据。", {"get_multi_stock_decision_evidence"}, _compile_decision_packet, allowed={"thesis"}, entities=True),
    StandardTaskKind.INVESTMENT_DECISION: _spec(
        StandardTaskKind.INVESTMENT_DECISION,
        "严格买入判断",
        "对完整股票集合逐只执行九项固定门槛，首项失败即停止，全部通过才可买入。",
        {"evaluate_multi_stock_buy_criteria"},
        _compile_strict_buy_decision,
        allowed={"thesis"},
        entities=True,
        max_tool_calls=150,
        max_parallel_steps=1,
        max_attempts=1,
        notes=(
            "门槛顺序固定：主线受益、产业竞争力、三年空间、景气上行、非内卷、6—12个月催化、重大风险、估值及利好透支、买入位置与风险收益比。",
            "Agent 工作流每批2只并覆盖完整集合；底层工具单次上限8只，不得用网络搜索或通用证据工具替代。",
        ),
    ),
    StandardTaskKind.MARKET_OVERVIEW: _spec(StandardTaskKind.MARKET_OVERVIEW, "市场概览", "分析指数、市场宽度与整体交易状态。", {"get_market_status", "get_market_breadth", "get_index_data"}, _compile_market, allowed={"include_index", "index_code", "days"}),
    StandardTaskKind.SECTOR_ANALYSIS: _spec(StandardTaskKind.SECTOR_ANALYSIS, "板块分析", "比较行业或概念板块强弱、资金和近期信息。", {"get_sector_list", "get_sector_flow", "search_financial_news"}, _compile_sector, allowed={"type", "period", "top_n", "query", "days", "limit", "include_content"}, notes=("type 只能是 industry 或 concept；period 只能是 today、5d、10d。",), enums={"type": {"industry", "concept"}, "period": {"today", "5d", "10d"}}),
    StandardTaskKind.CAPITAL_FLOW_ANALYSIS: _spec(StandardTaskKind.CAPITAL_FLOW_ANALYSIS, "资金流分析", "分析个股多周期资金流持续性。", {"get_stock_capital_flow"}, _compile_capital_flow, allowed={"days"}, entities=True),
    StandardTaskKind.MACRO_ANALYSIS: _spec(StandardTaskKind.MACRO_ANALYSIS, "宏观分析", "分析宏观指标、利率或货币政策操作。", {"get_macro_indicator", "get_bond_yield", "get_monetary_policy_operations", "search_research_library"}, _compile_macro, allowed={"indicators", "periods", "include_bond_yield", "country", "term", "days", "include_monetary_operations", "instrument", "limit", "include_content", "fallback_to_web", "query"}, notes=("indicators 只可选 PMI、CPI、PPI、GDP、M2、社融、LPR。", "国债 country 为 cn/us，term 为 2y/5y/10y/30y。")),
    StandardTaskKind.INDUSTRY_RESEARCH: _spec(StandardTaskKind.INDUSTRY_RESEARCH, "产业研究", "研究产业链、价值量、竞争格局和受益环节。", {"search_financial_news", "search_research_library"}, _compile_industry, allowed={"query", "days", "limit", "include_content", "fallback_to_web"}),
    StandardTaskKind.THEME_STOCK_DISCOVERY: _spec(StandardTaskKind.THEME_STOCK_DISCOVERY, "领域找股", "按已解析的产业领域从内部结构化板块和完整股票池找候选。", {"get_domain_stock_candidates"}, _compile_theme_discovery, required={"domains"}, allowed={"domains", "context_theme", "limit_per_domain"}),
    StandardTaskKind.THEME_BUSINESS_EVIDENCE: _spec(StandardTaskKind.THEME_BUSINESS_EVIDENCE, "主题公司举证", "用户明确要求订单、收入、量产或客户等业务事实时核验公司。", {"get_theme_stock_candidates", "search_financial_news", "search_research_library"}, _compile_theme_evidence, required={"theme"}, allowed={"theme", "query", "candidate_limit", "days", "limit", "include_content", "fallback_to_web"}),
    StandardTaskKind.STOCK_SCREENING: _spec(StandardTaskKind.STOCK_SCREENING, "股票筛选", "按完整强类型筛选规格执行全市场量化筛选。", {"screen_atr_volatility_stocks"}, _compile_screening, required={"screen_spec"}, allowed={"screen_spec", "refresh_if_stale", "save_group_name"}),
    StandardTaskKind.COLLECTION_FINANCIAL_FILTER: _spec(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        "集合财务筛选",
        "按指标、报告期、单位和阈值分批筛选上一回答中的完整公司集合。",
        {"get_multi_stock_financials"},
        _compile_collection_filter,
        required={"metric", "period_basis", "operator", "threshold", "threshold_unit", "action"},
        allowed={"metric", "period_basis", "fiscal_year", "operator", "threshold", "threshold_unit", "action"},
        entities=True,
        max_parallel_steps=1,
        max_attempts=2,
        notes=(
            "metric 支持 debt_ratio、revenue、deducted_net_profit。",
            "period_basis 支持 latest_report、ttm、previous_fiscal_year、fiscal_year；明确年度时 fiscal_year 必填。",
            "threshold_unit：比率用 percent，金额用 cny、wan_cny 或 yi_cny。",
            "operator 为 gt/gte/lt/lte/eq；action 为 exclude_matching/keep_matching。",
        ),
        enums={
            "metric": {"debt_ratio", "revenue", "deducted_net_profit"},
            "period_basis": {"latest_report", "ttm", "previous_fiscal_year", "fiscal_year"},
            "threshold_unit": {"percent", "cny", "wan_cny", "yi_cny"},
            "operator": {"gt", "gte", "lt", "lte", "eq"},
            "action": {"exclude_matching", "keep_matching"},
        },
        requirements=(
            _require(when={"period_basis": {"fiscal_year"}}, all_of={"fiscal_year"}),
        ),
    ),
    StandardTaskKind.WATCHLIST_QUERY: _spec(StandardTaskKind.WATCHLIST_QUERY, "自选查询", "查看自选或在指定自选集合内按主题筛选。", {"manage_watchlist", "filter_watchlist_by_theme"}, _compile_watchlist_query, allowed={"theme", "group"}),
    StandardTaskKind.WATCHLIST_MUTATION: _spec(StandardTaskKind.WATCHLIST_MUTATION, "自选修改", "按用户明确要求添加或移除自选股。", {"manage_watchlist"}, _compile_watchlist_mutation, required={"action"}, allowed={"action"}, entities=True, effect=EffectClass.MUTATION, notes=("action 只能是 add 或 remove。",), enums={"action": {"add", "remove"}}),
    StandardTaskKind.WATCHLIST_GROUP_MANAGEMENT: _spec(StandardTaskKind.WATCHLIST_GROUP_MANAGEMENT, "自选分组管理", "查看、创建、重命名、删除或修改自选分组成员。", {"manage_watchlist_groups"}, _compile_group, required={"action"}, allowed={"action", "group", "new_name"}, effect=EffectClass.MUTATION, confirmation_actions={"delete"}, notes=("action 为 list/create/rename/delete/add/remove；delete 需要 explicit confirmation。",), enums={"action": {"list", "create", "rename", "delete", "add", "remove"}}, requirements=(
        _require(when={"action": {"create", "delete", "add", "remove"}}, all_of={"group"}),
        _require(when={"action": {"rename"}}, all_of={"group", "new_name"}),
        _require(when={"action": {"add", "remove"}}, entities=True),
    )),
    StandardTaskKind.DATA_HEALTH: _spec(StandardTaskKind.DATA_HEALTH, "数据健康", "查看股票池、行情和财务数据覆盖与维护状态。", {"get_data_health"}, _compile_data_health),
    StandardTaskKind.FORMAL_ANALYSIS: _spec(StandardTaskKind.FORMAL_ANALYSIS, "正式分析", "启动持久化单股报告，或查询其运行状态。", {"run_stock_analysis", "get_analysis_status"}, _compile_formal_analysis, required={"action"}, allowed={"action", "task_id", "status", "limit", "force_refresh", "notify_on_complete", "prompt_template_id"}, effect=EffectClass.MUTATION, notes=("action 为 start 或 status；start 需要一个实体，status 可用 task_id。",), enums={"action": {"start", "status"}}, requirements=(
        _require(when={"action": {"start"}}, entities=True),
    )),
    StandardTaskKind.ANALYSIS_HISTORY: _spec(StandardTaskKind.ANALYSIS_HISTORY, "分析历史", "搜索、读取或删除已保存的正式分析报告。", {"search_analysis_history", "read_analysis_report", "delete_analysis_history"}, _compile_history, required={"action"}, allowed={"action", "record_id", "record_ids", "include_markdown", "include_news", "start_date", "end_date", "page", "limit"}, effect=EffectClass.MUTATION, confirmation_actions={"delete"}, notes=("action 为 search/read/delete；read 需要 record_id，delete 需要 record_ids 和 explicit confirmation。",), enums={"action": {"search", "read", "delete"}}, requirements=(
        _require(when={"action": {"read"}}, all_of={"record_id"}),
        _require(when={"action": {"delete"}}, all_of={"record_ids"}),
    )),
    StandardTaskKind.ANALYSIS_TEMPLATE_MANAGEMENT: _spec(StandardTaskKind.ANALYSIS_TEMPLATE_MANAGEMENT, "分析模板", "查看或管理正式分析模板。", {"manage_analysis_templates"}, _compile_template, required={"action"}, allowed={"action", "template_id", "name", "content", "set_default"}, effect=EffectClass.MUTATION, confirmation_actions={"delete"}, notes=("action 为 list/get/create/update/set_default/delete；delete 需要 explicit confirmation。",), enums={"action": {"list", "get", "create", "update", "set_default", "delete"}}, requirements=(
        _require(when={"action": {"get", "update", "set_default", "delete"}}, all_of={"template_id"}),
        _require(when={"action": {"create"}}, all_of={"name", "content"}),
        _require(when={"action": {"update"}}, one_of={"name", "content", "set_default"}),
    )),
    StandardTaskKind.BATCH_ANALYSIS: _spec(StandardTaskKind.BATCH_ANALYSIS, "批量分析", "按明确范围启动正式批量分析。", {"run_batch_analysis"}, _compile_batch_analysis, required={"scope", "analysis_mode"}, allowed={"scope", "group_name", "analysis_mode", "prompt_template_id", "force_refresh"}, effect=EffectClass.MUTATION, notes=("scope 为 symbols/watchlist/configured/group；超过10只或使用保存范围时需要 explicit confirmation。",), enums={"scope": {"symbols", "watchlist", "configured", "group"}, "analysis_mode": {"template", "buy_criteria"}}, requirements=(
        _require(when={"scope": {"symbols"}}, entities=True),
        _require(when={"scope": {"group"}}, all_of={"group_name"}, confirmation=True),
        _require(when={"scope": {"watchlist", "configured"}}, confirmation=True),
        _require(when={"analysis_mode": {"template"}}, all_of={"prompt_template_id"}),
    )),
    StandardTaskKind.BATCH_RUN_MANAGEMENT: _spec(StandardTaskKind.BATCH_RUN_MANAGEMENT, "批量任务管理", "查看或控制批量分析任务。", {"manage_batch_run"}, _compile_batch_management, required={"action"}, allowed={"action", "run_id", "symbols", "limit"}, effect=EffectClass.MUTATION, confirmation_actions={"notify", "stop", "delete"}, notes=("action 为 list/status/detail/report/pause/continue/resume_failed/regenerate_report/notify/stop/delete；后三个高影响动作需要 explicit confirmation。",), enums={"action": {"list", "status", "detail", "report", "pause", "continue", "resume_failed", "regenerate_report", "notify", "stop", "delete"}}, requirements=(
        _require(when={"action": {"detail", "report", "resume_failed", "regenerate_report", "notify", "delete"}}, all_of={"run_id"}),
    )),
    StandardTaskKind.ANALYSIS_SCHEDULE_MANAGEMENT: _spec(StandardTaskKind.ANALYSIS_SCHEDULE_MANAGEMENT, "定时分析", "查看或修改自动分析计划。", {"manage_analysis_schedule"}, _compile_schedule, required={"action"}, allowed={"action", "enabled", "times", "prompt_template_id"}, effect=EffectClass.MUTATION, confirmation_actions={"update"}, notes=("action 为 get/update；update 必须明确启停状态并获得确认；启用时必须给出时间和模板。",), enums={"action": {"get", "update"}}, requirements=(
        _require(when={"action": {"update"}}, all_of={"enabled"}),
        _require(when={"action": {"update"}, "enabled": {True}}, all_of={"times", "prompt_template_id"}),
    )),
    StandardTaskKind.NOTIFICATION: _spec(StandardTaskKind.NOTIFICATION, "通知", "检查通知配置或发送用户明确指定的内容。", {"get_notification_status", "send_notification"}, _compile_notification, required={"action"}, allowed={"action", "content_type", "message", "record_id", "batch_run_id", "title"}, effect=EffectClass.EXTERNAL, confirmation_actions={"send"}, notes=("action 为 status/send；send 需要 explicit confirmation。",), enums={"action": {"status", "send"}, "content_type": {"analysis_report", "batch_report", "custom"}}, requirements=(
        _require(when={"action": {"send"}}, all_of={"content_type"}),
        _require(when={"action": {"send"}, "content_type": {"analysis_report"}}, all_of={"record_id"}),
        _require(when={"action": {"send"}, "content_type": {"batch_report"}}, all_of={"batch_run_id"}),
        _require(when={"action": {"send"}, "content_type": {"custom"}}, all_of={"message"}),
    )),
    StandardTaskKind.FINANCIAL_SOURCE_DISCOVERY: _spec(StandardTaskKind.FINANCIAL_SOURCE_DISCOVERY, "资讯源查询", "列出或检查指定财经资讯源。", {"list_financial_sources", "inspect_financial_source"}, _compile_source_discovery, allowed={"route_path", "keyword", "namespace", "capability", "force", "limit"}),
    StandardTaskKind.FINANCIAL_FEED_READ: _spec(StandardTaskKind.FINANCIAL_FEED_READ, "Feed读取", "按已知路由读取一个财经 Feed。", {"read_financial_feed"}, _compile_feed_read, required={"route_path"}, allowed={"route_path", "params", "options", "namespace", "limit", "force"}),
    StandardTaskKind.FINANCIAL_ARTICLE_READ: _spec(StandardTaskKind.FINANCIAL_ARTICLE_READ, "文章读取", "读取用户指定的一篇财经资讯正文。", {"read_financial_article"}, _compile_article, required={"route_path", "title"}, allowed={"route_path", "title", "params", "options", "namespace", "item_id", "link", "list_summary", "list_content_html", "list_image", "published", "author", "tags", "attachments", "offset", "max_chars", "force"}),
    StandardTaskKind.WEBPAGE_FEED_TRANSFORM: _spec(StandardTaskKind.WEBPAGE_FEED_TRANSFORM, "网页转Feed", "按用户提供的网页和选择器生成 Feed 预览。", {"transform_webpage_to_feed"}, _compile_transform, required={"url"}, allowed={"url", "item", "title", "item_title", "item_title_attr", "item_link", "item_link_attr", "item_desc", "item_desc_attr", "item_pubdate", "item_pubdate_attr", "item_content", "encoding", "limit", "options"}),
    StandardTaskKind.FINANCIAL_FEED_EXPORT: _spec(StandardTaskKind.FINANCIAL_FEED_EXPORT, "Feed导出", "导出用户明确指定的财经 Feed。", {"export_financial_feed"}, _compile_export, required={"route_path"}, allowed={"route_path", "params", "options", "namespace", "format", "limit"}, effect=EffectClass.EXTERNAL),
    StandardTaskKind.PUBLIC_WEB_RESEARCH: _spec(StandardTaskKind.PUBLIC_WEB_RESEARCH, "公开网页研究", "仅在用户明确要求联网搜索或读取公开网页时使用。", {"websearch", "webfetch"}, _compile_web, allowed={"query", "url", "numResults", "livecrawl", "type", "contextMaxCharacters", "includeContent", "format", "timeout"}),
    StandardTaskKind.TRADE_EXECUTION: _spec(StandardTaskKind.TRADE_EXECUTION, "交易执行", "交易类请求只能进入独立状态机；当前系统未接入账户、风控和下单工具。", (), _compile_no_tools, effect=EffectClass.TRADE, enabled=False, state_machine=("参数校验", "账户检查", "风控检查", "用户确认", "下单", "订单状态")),
}

# Neither the planner nor request-local code can mutate the production
# Workflow Registry after module initialization.
WORKFLOW_REGISTRY: Mapping[StandardTaskKind, WorkflowSpec] = MappingProxyType(
    _WORKFLOW_REGISTRY
)


def workflow_for(kind: StandardTaskKind) -> WorkflowSpec:
    return WORKFLOW_REGISTRY[kind]


def planner_task_catalog() -> list[dict[str, Any]]:
    """Return semantic task contracts without exposing any tool name."""
    return [WORKFLOW_REGISTRY[kind].planner_contract() for kind in StandardTaskKind]


def compile_task(task: ResolvedTask) -> list[WorkflowCall]:
    spec = workflow_for(task.kind)
    if not spec.enabled:
        raise WorkflowCompileError(f"{spec.title}当前不可执行")
    unknown = set(task.parameters) - spec.allowed_parameters
    if unknown:
        raise WorkflowCompileError(
            f"{task.kind.value} contains unsupported parameters: {sorted(unknown)}"
        )
    missing = spec.required_parameters - set(task.parameters)
    if missing:
        raise WorkflowCompileError(
            f"{task.kind.value} is missing required parameters: {sorted(missing)}"
        )
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
            f"{task.kind.value} violates its conditional contract: "
            + "; ".join(conditional_issues)
        )
    calls = spec.compiler(task)
    if len(calls) > spec.max_tool_calls:
        raise WorkflowCompileError(
            f"{task.kind.value} compiled {len(calls)} calls, exceeding {spec.max_tool_calls}"
        )
    for call in calls:
        if call.tool_name not in spec.tool_whitelist:
            raise WorkflowCompileError(
                f"{call.tool_name} is outside the {task.kind.value} whitelist"
            )
    for parameter, allowed_values in spec.parameter_enums.items():
        if parameter not in task.parameters:
            continue
        if task.parameters[parameter] not in allowed_values:
            raise WorkflowCompileError(
                f"{task.kind.value}.{parameter} must be one of {sorted(allowed_values)}"
            )
    return calls


def registered_workflow_tools() -> frozenset[str]:
    return frozenset(
        tool
        for spec in WORKFLOW_REGISTRY.values()
        for tool in spec.tool_whitelist
    )


__all__ = [
    "ConfirmationState",
    "EffectClass",
    "EntityScope",
    "ParameterRequirement",
    "ResolvedTask",
    "StandardTask",
    "StandardTaskKind",
    "TaskPlan",
    "WorkflowCall",
    "WorkflowCompileError",
    "WorkflowSpec",
    "WORKFLOW_REGISTRY",
    "compile_task",
    "planner_task_catalog",
    "parameter_requirement_issues",
    "registered_workflow_tools",
    "workflow_for",
]
