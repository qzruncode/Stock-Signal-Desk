# -*- coding: utf-8 -*-
"""Single-source capability contracts for the unified Agent control plane."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Callable, Mapping, cast

from pydantic import BaseModel

from src.agent.orchestrator_v2 import intents as intent_models
from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    AssumptionRecord,
    CachePolicy,
    Capability,
    CapabilitySpec,
    CoverageV2,
    EffectLevel,
    ExecutionPolicy,
    InputReferenceV2,
    NormalizedIntent,
    OrchestratorV2Error,
    ProjectedResourceV2,
    RendererMode,
    ResourceType,
    ResultSelectionV2,
    TaskOutcomeV2,
)
from src.agent.orchestrator_v2.intents import (
    CollectionFinancialFilterIntent,
    FiscalYearPeriod,
    LatestReportPeriod,
    MoneyAmount,
    OutputRequestV2,
    ThemeStockDiscoveryIntent,
    TtmPeriod,
)
from src.agent.task_workflows import (
    CollectionBehavior,
    EffectClass,
    StandardTaskKind,
    TaskResource,
    workflow_for,
)
from src.services.buy_criteria.mainline_policy import (
    MainlineStrategyProfile,
    normalize_mainline_strategy,
)


_OUTPUT_DEFAULTS: Mapping[str, Any] = MappingProxyType({
    "language": "zh-CN",
    "format": "concise",
    "include_assumptions": True,
})


def _normalize_output_request(
    *,
    node_id: str,
    intent: Any,
) -> tuple[Any, tuple[AssumptionRecord, ...]]:
    supplied = getattr(intent, "output", None)
    values: dict[str, Any] = {}
    assumptions: list[AssumptionRecord] = []
    for field_name, default_value in _OUTPUT_DEFAULTS.items():
        value = getattr(supplied, field_name) if supplied is not None else None
        if value is None:
            value = default_value
            assumptions.append(AssumptionRecord(
                node_id=node_id,
                field_path=f"/output/{field_name}",
                value=value,
                reason="用户未指定该输出偏好，采用能力契约声明的程序默认值。",
            ))
        values[field_name] = value
    return (
        intent.model_copy(update={"output": OutputRequestV2(**values)}),
        tuple(assumptions),
    )


def _project_outcome_resource(
    outcome: TaskOutcomeV2,
    resource_type: ResourceType,
) -> ProjectedResourceV2 | None:
    def projected(
        payload: Any,
        *,
        coverage: CoverageV2 | None = None,
    ) -> ProjectedResourceV2:
        return ProjectedResourceV2(
            resource_type=resource_type,
            coverage=coverage or outcome.coverage,
            payload=payload,
        )

    result = outcome.result if isinstance(outcome.result, Mapping) else {}
    if resource_type == ResourceType.SECURITY_COLLECTION:
        securities = result.get("output_entities")
        if isinstance(securities, list):
            return projected({"securities": list(securities)})
        return None
    resource_outputs = result.get("resource_outputs")
    if isinstance(resource_outputs, Mapping):
        direct = resource_outputs.get(resource_type.value)
        if direct is not None:
            if resource_type == ResourceType.DOMAIN_COLLECTION:
                domain_artifact = next((
                    artifact
                    for packet in result.get("derived_results") or ()
                    if isinstance(packet, Mapping)
                    and isinstance(packet.get("result"), Mapping)
                    for artifact in packet["result"].get("semantic_artifacts") or ()
                    if isinstance(artifact, Mapping)
                    and artifact.get("type") in {
                        "domain_collection_v2",
                        "ranked_domains",
                    }
                ), None)
                return projected({
                    "domains": direct,
                    **(
                        {
                            (
                                "domain_collection_v2"
                                if domain_artifact.get("type")
                                == "domain_collection_v2"
                                else "ranked_domains"
                            ): dict(domain_artifact)
                        }
                        if domain_artifact is not None
                        else {}
                    ),
                })
            return projected(direct)
    if resource_type == ResourceType.EVIDENCE_COLLECTION:
        return projected(result)
    if resource_type == ResourceType.GENERIC_RESULT:
        return projected(result)
    return None


Compiler = Callable[
    [
        str,
        str,
        BaseModel,
        tuple[InputReferenceV2, ...],
        ResultSelectionV2 | None,
        int,
    ],
    NormalizedIntent[Any],
]


def _assumption(
    node_id: str,
    field: str,
    value: Any,
    reason: str,
) -> AssumptionRecord:
    return AssumptionRecord(
        node_id=node_id,
        field_path=f"/{field}",
        value=value,
        reason=reason,
    )


def _simple_compiler(
    *,
    aliases: Mapping[str, str] | None = None,
    defaults: Mapping[str, tuple[Any, str]] | None = None,
    program_fields: Mapping[str, Any] | None = None,
) -> Compiler:
    """Build a closed intent-to-execution projection.

    This is a program compiler, not a planner schema: only fields declared by
    the selected Pydantic intent can enter the projection.
    """

    field_aliases = dict(aliases or {})
    declared_defaults = dict(defaults or {})
    fixed_fields = dict(program_fields or {})

    def compile_intent(
        node_id: str,
        objective: str,
        intent: BaseModel,
        input_refs: tuple[InputReferenceV2, ...],
        result_selection: ResultSelectionV2 | None,
        current_year: int,
    ) -> NormalizedIntent[Any]:
        del objective, input_refs, result_selection, current_year
        raw = intent.model_dump(
            mode="json",
            exclude_none=True,
            exclude_unset=True,
        )
        raw.pop("output", None)
        raw.pop("user_confirmed", None)
        parameters: dict[str, Any] = {}
        for key, value in raw.items():
            parameters[field_aliases.get(key, key)] = value
        assumptions: list[AssumptionRecord] = []
        for key, (value, reason) in declared_defaults.items():
            target = field_aliases.get(key, key)
            if target not in parameters:
                parameters[target] = value
                assumptions.append(_assumption(node_id, key, value, reason))
        parameters.update(fixed_fields)
        return NormalizedIntent(
            intent=intent,
            execution_parameters=MappingProxyType(parameters),
            assumptions=tuple(assumptions),
        )

    return compile_intent


def _price_history_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del objective, input_refs, result_selection, current_year
    period = getattr(intent, "period", None)
    assumptions: tuple[AssumptionRecord, ...] = ()
    if period is None:
        parameters = {"count": 120, "use_cache": True}
        assumptions = (
            _assumption(
                node_id,
                "period",
                {"kind": "recent", "count": 120},
                "用户未指定历史区间，按最近 120 个交易日执行。",
            ),
        )
    elif period.kind == "recent":
        parameters = {"count": period.count, "use_cache": True}
    else:
        parameters = {
            "start_date": period.start_date.strftime("%Y%m%d"),
            "end_date": period.end_date.strftime("%Y%m%d"),
            "use_cache": True,
        }
    return NormalizedIntent(
        intent=intent,
        execution_parameters=MappingProxyType(parameters),
        assumptions=assumptions,
    )


def _theme_compiler(
    node_id: str,
    objective: str,
    intent: ThemeStockDiscoveryIntent,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del objective, input_refs, result_selection, current_year
    normalized, assumptions = _normalize_output_request(node_id=node_id, intent=intent)
    return NormalizedIntent(
        intent=normalized,
        execution_parameters=MappingProxyType({
            "domains": [{"label": theme} for theme in normalized.themes],
        }),
        assumptions=assumptions,
    )


def _industry_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del input_refs, current_year
    explicit_subjects = list(getattr(intent, "explicit_subjects"))
    assumptions = (
        (
            _assumption(
                node_id,
                "result_selection",
                {"mode": "top_k", "max_items": 16},
                "用户未指定返回数量，产业受益板块默认最多返回 16 个。",
            ),
        )
        if result_selection is None
        else ()
    )
    return NormalizedIntent(
        intent=intent,
        execution_parameters=MappingProxyType({
            "query": objective,
            "domains": [{"label": item} for item in explicit_subjects],
            "_assumptions": [
                {
                    "field_path": assumption.field_path,
                    "value": assumption.value,
                    "reason": assumption.reason,
                    "source": assumption.source,
                }
                for assumption in assumptions
            ],
        }),
        assumptions=assumptions,
    )


_SUPPORTED_PERIODS: Mapping[str, frozenset[str]] = MappingProxyType({
    "debt_ratio": frozenset({"latest_report", "previous_fiscal_year", "fiscal_year"}),
    "revenue": frozenset({"ttm", "previous_fiscal_year", "fiscal_year"}),
    "net_profit": frozenset({"previous_fiscal_year", "fiscal_year"}),
    "deducted_net_profit": frozenset({"ttm", "previous_fiscal_year", "fiscal_year"}),
})
_DEFAULT_PERIOD: Mapping[str, str] = MappingProxyType({
    "debt_ratio": "latest_report",
    "revenue": "previous_fiscal_year",
    "net_profit": "previous_fiscal_year",
    "deducted_net_profit": "previous_fiscal_year",
})


def _financial_filter_compiler(
    node_id: str,
    objective: str,
    intent: CollectionFinancialFilterIntent,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del objective, input_refs, result_selection
    normalized_intent, output_assumptions = _normalize_output_request(
        node_id=node_id,
        intent=intent,
    )
    conditions: list[dict[str, Any]] = []
    assumptions: list[AssumptionRecord] = list(output_assumptions)
    canonical_predicates: list[Any] = []
    for index, predicate in enumerate(normalized_intent.predicates):
        period = predicate.period
        if period is None:
            requested_period_basis = _DEFAULT_PERIOD[predicate.metric]
            fiscal_year = None
            assumptions.append(_assumption(
                node_id,
                f"predicates/{index}/period",
                {
                    "kind": requested_period_basis,
                    **(
                        {"resolved_year": current_year - 1}
                        if requested_period_basis == "previous_fiscal_year"
                        else {}
                    ),
                },
                (
                    "资产负债率未指定期间，按最新可用报告期执行。"
                    if predicate.metric == "debt_ratio"
                    else "年度型财务指标未指定期间，按上一完整财年执行。"
                ),
            ))
        else:
            requested_period_basis = period.kind
            fiscal_year = getattr(period, "year", None)
        if requested_period_basis not in _SUPPORTED_PERIODS[predicate.metric]:
            raise OrchestratorV2Error(
                AgentErrorCode.CLARIFICATION_REQUIRED,
                (
                    f"{predicate.metric} 不支持 {requested_period_basis} 口径；"
                    f"可用口径为 {sorted(_SUPPORTED_PERIODS[predicate.metric])}。"
                ),
                task_id=node_id,
            )
        period_basis = requested_period_basis
        if requested_period_basis == "previous_fiscal_year":
            period_basis = "fiscal_year"
            fiscal_year = current_year - 1
        canonical_period = (
            LatestReportPeriod(kind="latest_report")
            if period_basis == "latest_report"
            else TtmPeriod(kind="ttm")
            if period_basis == "ttm"
            else FiscalYearPeriod(kind="fiscal_year", year=int(fiscal_year))
        )
        condition: dict[str, Any] = {
            "metric": predicate.metric,
            "period_basis": period_basis,
            "operator": predicate.operator,
            "action": predicate.action,
        }
        if fiscal_year is not None:
            condition["fiscal_year"] = fiscal_year
        if predicate.metric == "debt_ratio":
            condition.update({
                "threshold": predicate.percent,
                "threshold_unit": "percent",
            })
            canonical_predicates.append(predicate.model_copy(update={"period": canonical_period}))
        else:
            amount_in_cny = predicate.amount.value * {
                "cny": 1.0,
                "wan_cny": 10_000.0,
                "yi_cny": 100_000_000.0,
            }[predicate.amount.unit]
            condition.update({"threshold": amount_in_cny, "threshold_unit": "cny"})
            canonical_predicates.append(predicate.model_copy(update={
                "period": canonical_period,
                "amount": MoneyAmount(value=amount_in_cny, unit="cny"),
            }))
        conditions.append(condition)
    conditions.sort(key=lambda item: (
        str(item["metric"]),
        str(item["period_basis"]),
        int(item.get("fiscal_year") or 0),
        str(item["operator"]),
        float(item["threshold"]),
        str(item["threshold_unit"]),
        str(item["action"]),
    ))
    canonical_predicates.sort(key=lambda predicate: (
        predicate.metric,
        predicate.period.model_dump_json(),
        predicate.operator,
        predicate.percent if predicate.metric == "debt_ratio" else predicate.amount.value,
        predicate.action,
    ))
    normalized_intent = normalized_intent.model_copy(
        update={"predicates": tuple(canonical_predicates)}
    )
    return NormalizedIntent(
        intent=normalized_intent,
        execution_parameters=MappingProxyType({"conditions": conditions}),
        assumptions=tuple(assumptions),
    )


def _investment_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del objective, input_refs, result_selection, current_year
    normalized, assumptions = _normalize_output_request(node_id=node_id, intent=intent)
    requested_profile = getattr(normalized, "mainline_strategy", None)
    profile = normalize_mainline_strategy(requested_profile)
    if requested_profile is None:
        assumptions = (
            *assumptions,
            _assumption(
                node_id,
                "mainline_strategy",
                profile.value,
                "用户未指定主线投资时机，按确认型主线执行。",
            ),
        )
        normalized = normalized.model_copy(update={
            "mainline_strategy": MainlineStrategyProfile.CONFIRMED_MAINLINE,
        })
    return NormalizedIntent(
        intent=normalized,
        execution_parameters=MappingProxyType({
            **({"thesis": normalized.thesis} if normalized.thesis else {}),
            "mainline_strategy": profile.value,
        }),
        assumptions=assumptions,
    )


def _theme_evidence_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del input_refs, result_selection, current_year
    normalized, output_assumptions = _normalize_output_request(
        node_id=node_id,
        intent=intent,
    )
    days = normalized.days
    assumptions = list(output_assumptions)
    if days is None:
        days = 365
        assumptions.append(_assumption(
            node_id,
            "days",
            365,
            "用户未指定公司业务证据窗口，按最近 365 天执行。",
        ))
    return NormalizedIntent(
        intent=normalized,
        execution_parameters=MappingProxyType({
            "candidate_scope": "candidate_collection",
            "query": normalized.focus or objective,
            "days": days,
        }),
        assumptions=tuple(assumptions),
    )


def _named_inputs(values: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(item["name"]): item["value"] for item in values}


def _feed_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del node_id, objective, input_refs, result_selection, current_year
    payload = intent.model_dump(mode="json", exclude_none=True)
    inputs = _named_inputs(payload.pop("inputs", []))
    if inputs:
        payload["params"] = inputs
    payload["force"] = False
    return NormalizedIntent(
        intent=intent,
        execution_parameters=MappingProxyType(payload),
    )


def _webpage_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del node_id, objective, input_refs, result_selection, current_year
    aliases = {
        "item_selector": "item",
        "title_selector": "item_title",
        "link_selector": "item_link",
        "description_selector": "item_desc",
        "published_selector": "item_pubdate",
        "content_selector": "item_content",
    }
    payload = {
        aliases.get(key, key): value
        for key, value in intent.model_dump(
            mode="json",
            exclude_none=True,
            exclude_unset=True,
        ).items()
    }
    return NormalizedIntent(
        intent=intent,
        execution_parameters=MappingProxyType(payload),
    )


_INTENT_MODELS: Mapping[Capability, type[BaseModel]] = MappingProxyType({
    Capability.GENERAL_RESPONSE: intent_models.GeneralResponseIntent,
    Capability.SECURITY_LOOKUP: intent_models.SecurityLookupIntent,
    Capability.REALTIME_QUOTE: intent_models.RealtimeQuoteIntent,
    Capability.PRICE_HISTORY: intent_models.PriceHistoryIntent,
    Capability.TECHNICAL_ANALYSIS: intent_models.TechnicalAnalysisIntent,
    Capability.FUNDAMENTAL_ANALYSIS: intent_models.FundamentalAnalysisIntent,
    Capability.VALUATION_ANALYSIS: intent_models.ValuationAnalysisIntent,
    Capability.FINANCIAL_STATEMENT_ANALYSIS: intent_models.FinancialStatementAnalysisIntent,
    Capability.NEWS_ANALYSIS: intent_models.NewsAnalysisIntent,
    Capability.ANNOUNCEMENT_ANALYSIS: intent_models.AnnouncementAnalysisIntent,
    Capability.RISK_ANALYSIS: intent_models.RiskAnalysisIntent,
    Capability.REGULATORY_ANALYSIS: intent_models.RegulatoryAnalysisIntent,
    Capability.RESEARCH_REPORT_ANALYSIS: intent_models.ResearchReportAnalysisIntent,
    Capability.CATALYST_ANALYSIS: intent_models.CatalystAnalysisIntent,
    Capability.SOCIAL_SENTIMENT_ANALYSIS: intent_models.SocialSentimentAnalysisIntent,
    Capability.STOCK_COMPARISON: intent_models.StockComparisonIntent,
    Capability.STOCK_DEEP_RESEARCH: intent_models.StockDeepResearchIntent,
    Capability.INVESTMENT_DECISION: intent_models.InvestmentDecisionIntent,
    Capability.MARKET_OVERVIEW: intent_models.MarketOverviewIntent,
    Capability.SECTOR_ANALYSIS: intent_models.SectorAnalysisIntent,
    Capability.CAPITAL_FLOW_ANALYSIS: intent_models.CapitalFlowAnalysisIntent,
    Capability.MACRO_ANALYSIS: intent_models.MacroAnalysisIntent,
    Capability.INDUSTRY_RESEARCH: intent_models.IndustryResearchIntent,
    Capability.THEME_STOCK_DISCOVERY: intent_models.ThemeStockDiscoveryIntent,
    Capability.THEME_BUSINESS_EVIDENCE: intent_models.ThemeBusinessEvidenceIntent,
    Capability.STOCK_SCREENING: intent_models.StockScreeningIntent,
    Capability.COLLECTION_FINANCIAL_FILTER: intent_models.CollectionFinancialFilterIntent,
    Capability.WATCHLIST_QUERY: intent_models.WatchlistQueryIntent,
    Capability.WATCHLIST_MUTATION: intent_models.WatchlistMutationIntent,
    Capability.WATCHLIST_GROUP_MANAGEMENT: intent_models.WatchlistGroupManagementIntent,
    Capability.DATA_HEALTH: intent_models.DataHealthIntent,
    Capability.FORMAL_ANALYSIS: intent_models.FormalAnalysisIntent,
    Capability.ANALYSIS_HISTORY: intent_models.AnalysisHistoryIntent,
    Capability.ANALYSIS_TEMPLATE_MANAGEMENT: intent_models.AnalysisTemplateManagementIntent,
    Capability.BATCH_ANALYSIS: intent_models.BatchAnalysisIntent,
    Capability.BATCH_RUN_MANAGEMENT: intent_models.BatchRunManagementIntent,
    Capability.ANALYSIS_SCHEDULE_MANAGEMENT: intent_models.AnalysisScheduleManagementIntent,
    Capability.NOTIFICATION: intent_models.NotificationIntent,
    Capability.FINANCIAL_SOURCE_DISCOVERY: intent_models.FinancialSourceDiscoveryIntent,
    Capability.FINANCIAL_FEED_READ: intent_models.FinancialFeedReadIntent,
    Capability.FINANCIAL_ARTICLE_READ: intent_models.FinancialArticleReadIntent,
    Capability.WEBPAGE_FEED_TRANSFORM: intent_models.WebpageFeedTransformIntent,
    Capability.FINANCIAL_FEED_EXPORT: intent_models.FinancialFeedExportIntent,
    Capability.PUBLIC_WEB_RESEARCH: intent_models.PublicWebResearchIntent,
    Capability.TRADE_EXECUTION: intent_models.TradeExecutionIntent,
})


_COMPILERS: Mapping[Capability, Compiler] = MappingProxyType({
    Capability.PRICE_HISTORY: _price_history_compiler,
    Capability.TECHNICAL_ANALYSIS: _simple_compiler(
        program_fields={"count": 120},
    ),
    Capability.FUNDAMENTAL_ANALYSIS: _simple_compiler(
        aliases={"business_category": "category"},
    ),
    Capability.VALUATION_ANALYSIS: _simple_compiler(aliases={
        "include_history": "with_history",
        "consensus_metric": "metric",
        "peer_dimension": "dimension",
    }),
    Capability.NEWS_ANALYSIS: _simple_compiler(
        program_fields={
            "use_cache": True,
            "include_content": True,
            "fallback_to_web": False,
        },
    ),
    Capability.REGULATORY_ANALYSIS: _simple_compiler(
        program_fields={"include_content": True, "fallback_to_web": False},
    ),
    Capability.SOCIAL_SENTIMENT_ANALYSIS: _simple_compiler(
        program_fields={"max_pages": 5},
    ),
    Capability.MARKET_OVERVIEW: _simple_compiler(defaults={
        "include_index": (
            True,
            "用户未排除指数概览，按包含指数执行。",
        ),
    }),
    Capability.SECTOR_ANALYSIS: _simple_compiler(
        aliases={"sector_type": "type"},
        defaults={
            "sector_type": ("industry", "用户未指定板块类型，按行业板块执行。"),
            "period": ("5d", "用户未指定资金周期，按 5 日执行。"),
            "top_n": (10, "用户未指定板块数量，按前 10 个执行。"),
        },
        program_fields={"include_content": True},
    ),
    Capability.MACRO_ANALYSIS: _simple_compiler(
        aliases={
            "bond_yield": "include_bond_yield",
            "monetary_operations": "include_monetary_operations",
            "research_query": "query",
            "research_subjects": "subjects",
        },
        program_fields={"include_content": True, "fallback_to_web": False},
    ),
    Capability.INDUSTRY_RESEARCH: _industry_compiler,
    Capability.THEME_STOCK_DISCOVERY: _theme_compiler,
    Capability.THEME_BUSINESS_EVIDENCE: _theme_evidence_compiler,
    Capability.STOCK_SCREENING: _simple_compiler(
        program_fields={"refresh_if_stale": True},
    ),
    Capability.COLLECTION_FINANCIAL_FILTER: _financial_filter_compiler,
    Capability.INVESTMENT_DECISION: _investment_compiler,
    Capability.WATCHLIST_QUERY: _simple_compiler(aliases={"themes": "domains"}),
    Capability.FORMAL_ANALYSIS: _simple_compiler(aliases={"template_id": "prompt_template_id"}),
    Capability.ANALYSIS_HISTORY: _simple_compiler(),
    Capability.ANALYSIS_TEMPLATE_MANAGEMENT: _simple_compiler(),
    Capability.BATCH_ANALYSIS: _simple_compiler(aliases={"template_id": "prompt_template_id"}),
    Capability.BATCH_RUN_MANAGEMENT: _simple_compiler(),
    Capability.ANALYSIS_SCHEDULE_MANAGEMENT: _simple_compiler(
        aliases={"template_id": "prompt_template_id"},
    ),
    Capability.FINANCIAL_SOURCE_DISCOVERY: _simple_compiler(
        program_fields={"force": False},
    ),
    Capability.FINANCIAL_FEED_READ: _feed_compiler,
    Capability.FINANCIAL_FEED_EXPORT: _feed_compiler,
    Capability.WEBPAGE_FEED_TRANSFORM: _webpage_compiler,
    Capability.PUBLIC_WEB_RESEARCH: _simple_compiler(program_fields={
        "numResults": 10,
        "livecrawl": "fallback",
        "type": "auto",
        "contextMaxCharacters": 30_000,
        "includeContent": True,
        "format": "markdown",
    }),
})


def _effect(value: EffectClass) -> EffectLevel:
    return EffectLevel(value.value)


def _resources(
    values: frozenset[TaskResource],
) -> frozenset[ResourceType]:
    mapping = {
        TaskResource.SECURITY_COLLECTION: ResourceType.SECURITY_COLLECTION,
        TaskResource.DOMAIN_COLLECTION: ResourceType.DOMAIN_COLLECTION,
    }
    return frozenset(mapping[item] for item in values)


_DETERMINISTIC_CONTRACTS = frozenset({
    "industry_ranked_domains",
    "theme_stock_discovery",
    "theme_business_evidence",
    "collection_financial_filter",
    "stock_screening",
    "investment_decision",
})

_PROGRAM_DEFAULT_FIELDS: Mapping[Capability, frozenset[str]] = MappingProxyType({
    Capability.PRICE_HISTORY: frozenset({"period"}),
    Capability.MARKET_OVERVIEW: frozenset({"include_index"}),
    Capability.SECTOR_ANALYSIS: frozenset({
        "sector_type",
        "period",
        "top_n",
    }),
    Capability.THEME_STOCK_DISCOVERY: frozenset({"output.*"}),
    Capability.THEME_BUSINESS_EVIDENCE: frozenset({"days", "output.*"}),
    Capability.COLLECTION_FINANCIAL_FILTER: frozenset({
        "predicates.*.period",
        "output.*",
    }),
    Capability.INVESTMENT_DECISION: frozenset({
        "mainline_strategy",
        "output.*",
    }),
})

_SUBSUMED_CAPABILITIES: Mapping[
    Capability,
    frozenset[Capability],
] = MappingProxyType({
    Capability.INVESTMENT_DECISION: frozenset({
        Capability.SECURITY_LOOKUP,
        Capability.REALTIME_QUOTE,
        Capability.PRICE_HISTORY,
        Capability.TECHNICAL_ANALYSIS,
        Capability.FUNDAMENTAL_ANALYSIS,
        Capability.VALUATION_ANALYSIS,
        Capability.FINANCIAL_STATEMENT_ANALYSIS,
        Capability.NEWS_ANALYSIS,
        Capability.ANNOUNCEMENT_ANALYSIS,
        Capability.RISK_ANALYSIS,
        Capability.RESEARCH_REPORT_ANALYSIS,
        Capability.CATALYST_ANALYSIS,
        Capability.STOCK_DEEP_RESEARCH,
    }),
})


def _make_spec(capability: Capability) -> CapabilitySpec[Any, TaskOutcomeV2]:
    workflow = workflow_for(StandardTaskKind(capability.value))
    input_resources = _resources(workflow.input_resources)
    output_resources = _resources(workflow.output_resources)
    if not output_resources:
        output_resources = frozenset({ResourceType.GENERIC_RESULT})
    if capability in {
        Capability.INVESTMENT_DECISION,
        Capability.STOCK_DEEP_RESEARCH,
        Capability.CATALYST_ANALYSIS,
    }:
        output_resources = frozenset({
            *output_resources,
            ResourceType.EVIDENCE_COLLECTION,
        })
    policy = ExecutionPolicy(
        effect=_effect(workflow.effect),
        confirmation_required=False,
        max_calls=workflow.max_tool_calls,
        max_parallelism=workflow.max_parallel_steps,
        cache_policy=(
            CachePolicy.READ_ONLY
            if workflow.effect == EffectClass.READ
            else CachePolicy.DISABLED
        ),
        cache_ttl_seconds=(
            1800 if workflow.effect == EffectClass.READ else None
        ),
    )
    compiler = _COMPILERS.get(capability, _simple_compiler())
    capability_version = (
        "3.2.0"
        if capability == Capability.INVESTMENT_DECISION
        else "3.0.0"
    )
    return CapabilitySpec(
        capability=capability,
        version=capability_version,
        title=workflow.title,
        description=workflow.description,
        intent_model=_INTENT_MODELS[capability],
        result_model=TaskOutcomeV2,
        input_resources=input_resources,
        output_resources=output_resources,
        compiler=compiler,
        execution_policy=policy,
        projector=_project_outcome_resource,
        renderer=(
            RendererMode.DETERMINISTIC
            if workflow.result_contract in _DETERMINISTIC_CONTRACTS
            else RendererMode.EVIDENCE_SYNTHESIS
        ),
        allow_direct_entities=(
            workflow.requires_entities
            or any(req.requires_entities for req in workflow.parameter_requirements)
        ),
        supports_result_selection=workflow.supports_result_selection,
        program_default_fields=_PROGRAM_DEFAULT_FIELDS.get(
            capability,
            frozenset(),
        ),
        subsumes_capabilities=_SUBSUMED_CAPABILITIES.get(
            capability,
            frozenset(),
        ),
    )


CAPABILITY_REGISTRY: Mapping[
    Capability,
    CapabilitySpec[Any, TaskOutcomeV2],
] = MappingProxyType({
    capability: _make_spec(capability)
    for capability in Capability
})


def capability_for(
    capability: Capability | str,
) -> CapabilitySpec[Any, TaskOutcomeV2]:
    return CAPABILITY_REGISTRY[Capability(capability)]


def capability_catalog() -> list[dict[str, Any]]:
    return [
        {
            "capability": capability.value,
            "title": spec.title,
            "description": spec.description,
            "input_resources": sorted(item.value for item in spec.input_resources),
            "output_resources": sorted(item.value for item in spec.output_resources),
            "allow_direct_entities": spec.allow_direct_entities,
            "supports_result_selection": spec.supports_result_selection,
            "subsumes_capabilities": sorted(
                item.value for item in spec.subsumes_capabilities
            ),
        }
        for capability, spec in CAPABILITY_REGISTRY.items()
    ]


def migration_coverage() -> dict[str, Any]:
    """Compatibility metadata for health endpoints; the migration is complete."""
    return {
        "migrated": len(CAPABILITY_REGISTRY),
        "total": len(Capability),
        "complete": set(CAPABILITY_REGISTRY) == set(Capability),
        "capabilities": sorted(item.value for item in CAPABILITY_REGISTRY),
    }


def normalize_capability_intent(
    *,
    node_id: str,
    objective: str,
    capability: Capability,
    intent: Any,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    spec = capability_for(capability)
    validated = spec.intent_model.model_validate(intent)
    return cast(
        NormalizedIntent[Any],
        spec.compiler(
            node_id,
            objective,
            validated,
            input_refs,
            result_selection,
            current_year,
        ),
    )


__all__ = [
    "CAPABILITY_REGISTRY",
    "capability_catalog",
    "capability_for",
    "migration_coverage",
    "normalize_capability_intent",
]
