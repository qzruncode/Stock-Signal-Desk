# -*- coding: utf-8 -*-
"""Single-source capability contracts for the unified Agent control plane."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, cast

from pydantic import BaseModel

from src.agent.orchestrator_v2 import intents as intent_models
from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    AssumptionRecord,
    CacheReuseScope,
    Capability,
    CapabilitySpec,
    CoverageV2,
    EffectLevel,
    ExecutionPolicy,
    EvidenceDimension,
    FreshnessPolicy,
    InputReferenceV2,
    NormalizedIntent,
    OrchestratorV2Error,
    ProjectedResourceV2,
    QuestionType,
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
    if resource_type == ResourceType.MARKET_MAINLINE_SNAPSHOT:
        for call in result.get("calls") or ():
            if (
                isinstance(call, Mapping)
                and call.get("tool") == "prepare_market_mainline_snapshot"
                and isinstance(call.get("result"), Mapping)
                and call["result"].get("available") is True
            ):
                return projected(dict(call["result"]))
        return None
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
        raw = {
            key: value
            for key, value in raw.items()
            if value not in ([], {}, ())
        }
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
    Capability.MARKET_MAINLINE_RESEARCH: intent_models.MarketMainlineResearchIntent,
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


_RUN_ONLY_FRESHNESS = FreshnessPolicy(
    reuse_scope=CacheReuseScope.RUN_ONLY,
)


def _cross_run_freshness(
    seconds: int,
    *,
    market_session_sensitive: bool = False,
    require_observed_at: bool = False,
) -> FreshnessPolicy:
    return FreshnessPolicy(
        reuse_scope=CacheReuseScope.CROSS_RUN,
        max_age_seconds=seconds,
        market_session_sensitive=market_session_sensitive,
        require_observed_at=require_observed_at,
    )


# Freshness is a capability contract, not an executor/tool-name special case.
# Highly volatile snapshots stay run-local; slow-changing or versioned reads
# may cross runs for a bounded interval.
_FRESHNESS_BY_CAPABILITY: Mapping[
    Capability,
    FreshnessPolicy,
] = MappingProxyType({
    Capability.REALTIME_QUOTE: _RUN_ONLY_FRESHNESS,
    Capability.MARKET_OVERVIEW: _cross_run_freshness(
        15,
        market_session_sensitive=True,
        require_observed_at=True,
    ),
    Capability.MARKET_MAINLINE_RESEARCH: _cross_run_freshness(
        300,
        market_session_sensitive=True,
        require_observed_at=True,
    ),
    Capability.CAPITAL_FLOW_ANALYSIS: _cross_run_freshness(
        30,
        market_session_sensitive=True,
        require_observed_at=True,
    ),
    Capability.PRICE_HISTORY: _cross_run_freshness(
        60,
        market_session_sensitive=True,
        require_observed_at=True,
    ),
    Capability.TECHNICAL_ANALYSIS: _cross_run_freshness(
        60,
        market_session_sensitive=True,
        require_observed_at=True,
    ),
    Capability.SECTOR_ANALYSIS: _cross_run_freshness(
        60,
        market_session_sensitive=True,
        require_observed_at=True,
    ),
    Capability.STOCK_COMPARISON: _cross_run_freshness(
        60,
        market_session_sensitive=True,
        require_observed_at=True,
    ),
    Capability.STOCK_DEEP_RESEARCH: _cross_run_freshness(
        120,
        market_session_sensitive=True,
        require_observed_at=True,
    ),
    Capability.INVESTMENT_DECISION: _cross_run_freshness(
        120,
        market_session_sensitive=True,
        require_observed_at=True,
    ),
    Capability.NEWS_ANALYSIS: _cross_run_freshness(300),
    Capability.ANNOUNCEMENT_ANALYSIS: _cross_run_freshness(300),
    Capability.RISK_ANALYSIS: _cross_run_freshness(300),
    Capability.REGULATORY_ANALYSIS: _cross_run_freshness(300),
    Capability.RESEARCH_REPORT_ANALYSIS: _cross_run_freshness(300),
    Capability.CATALYST_ANALYSIS: _cross_run_freshness(300),
    Capability.SOCIAL_SENTIMENT_ANALYSIS: _cross_run_freshness(300),
    Capability.MACRO_ANALYSIS: _cross_run_freshness(300),
    Capability.PUBLIC_WEB_RESEARCH: _cross_run_freshness(300),
    Capability.FUNDAMENTAL_ANALYSIS: _cross_run_freshness(1_800),
    Capability.VALUATION_ANALYSIS: _cross_run_freshness(300),
    Capability.FINANCIAL_STATEMENT_ANALYSIS: _cross_run_freshness(1_800),
    Capability.THEME_BUSINESS_EVIDENCE: _cross_run_freshness(1_800),
    Capability.COLLECTION_FINANCIAL_FILTER: _cross_run_freshness(1_800),
    Capability.SECURITY_LOOKUP: _cross_run_freshness(3_600),
    Capability.INDUSTRY_RESEARCH: _cross_run_freshness(3_600),
    Capability.THEME_STOCK_DISCOVERY: _cross_run_freshness(600),
    Capability.STOCK_SCREENING: _cross_run_freshness(300),
    Capability.WATCHLIST_QUERY: _RUN_ONLY_FRESHNESS,
    Capability.DATA_HEALTH: _RUN_ONLY_FRESHNESS,
    Capability.FINANCIAL_SOURCE_DISCOVERY: _cross_run_freshness(3_600),
    Capability.FINANCIAL_FEED_READ: _cross_run_freshness(300),
    Capability.FINANCIAL_ARTICLE_READ: _cross_run_freshness(3_600),
    Capability.WEBPAGE_FEED_TRANSFORM: _RUN_ONLY_FRESHNESS,
    Capability.GENERAL_RESPONSE: _RUN_ONLY_FRESHNESS,
})
_READ_CAPABILITIES = {
    capability
    for capability in Capability
    if workflow_for(
        StandardTaskKind(capability.value)
    ).effect == EffectClass.READ
}
if set(_FRESHNESS_BY_CAPABILITY) != _READ_CAPABILITIES:
    missing = sorted(
        capability.value
        for capability in _READ_CAPABILITIES - set(_FRESHNESS_BY_CAPABILITY)
    )
    unexpected = sorted(
        capability.value
        for capability in set(_FRESHNESS_BY_CAPABILITY) - _READ_CAPABILITIES
    )
    raise RuntimeError(
        "freshness registry must cover every read capability exactly; "
        f"missing={missing}, unexpected={unexpected}"
    )


@dataclass(frozen=True)
class _CapabilitySemantics:
    question_types: frozenset[QuestionType]
    dimensions: frozenset[EvidenceDimension]
    claims: tuple[str, ...]
    limitations: tuple[str, ...]
    fallbacks: tuple[Capability, ...] = ()
    auto_expandable: bool = False


_FACT_RESEARCH = frozenset({
    QuestionType.FACTUAL,
    QuestionType.EXPLANATION,
    QuestionType.DIAGNOSIS,
    QuestionType.COMPARISON,
    QuestionType.RESEARCH,
})
_RESEARCH_FORECAST = frozenset({
    *_FACT_RESEARCH,
    QuestionType.FORECAST,
})
_OPERATIONAL = frozenset({
    QuestionType.FACTUAL,
    QuestionType.OPERATION,
})


def _sem(
    dimensions: tuple[EvidenceDimension, ...],
    claims: tuple[str, ...],
    limitations: tuple[str, ...],
    *,
    question_types: frozenset[QuestionType] = _FACT_RESEARCH,
    fallbacks: tuple[Capability, ...] = (),
    auto_expandable: bool = False,
) -> _CapabilitySemantics:
    return _CapabilitySemantics(
        question_types=question_types,
        dimensions=frozenset(dimensions),
        claims=claims,
        limitations=limitations,
        fallbacks=fallbacks,
        auto_expandable=auto_expandable,
    )


# This registry is the program-owned semantic boundary between a user's Goal
# Contract and executable workflows.  It is deliberately exhaustive: adding a
# capability without declaring what it can and cannot prove fails at import.
_SEMANTICS_BY_CAPABILITY: Mapping[
    Capability,
    _CapabilitySemantics,
] = MappingProxyType({
    Capability.GENERAL_RESPONSE: _sem(
        (EvidenceDimension.GENERAL_KNOWLEDGE,),
        ("无需实时外部数据的知识、解释、写作或计算",),
        ("不能提供本轮未检索的实时市场或公司事实",),
        question_types=frozenset({
            QuestionType.DIRECT,
            QuestionType.FACTUAL,
            QuestionType.EXPLANATION,
        }),
    ),
    Capability.SECURITY_LOOKUP: _sem(
        (EvidenceDimension.SECURITY_IDENTITY,),
        ("证券名称、代码、市场和行业身份",),
        ("不能证明公司业务质量、价格或投资价值",),
        auto_expandable=True,
    ),
    Capability.REALTIME_QUOTE: _sem(
        (EvidenceDimension.REALTIME_MARKET,),
        ("证券当前行情快照",),
        ("不能单独解释涨跌原因或预测后续走势",),
        auto_expandable=True,
    ),
    Capability.PRICE_HISTORY: _sem(
        (EvidenceDimension.PRICE_HISTORY,),
        ("证券指定时间范围内的历史行情",),
        ("不能单独建立基本面因果或未来主线",),
        auto_expandable=True,
    ),
    Capability.TECHNICAL_ANALYSIS: _sem(
        (EvidenceDimension.TECHNICAL_SIGNALS,),
        ("趋势、动量、波动和量价技术状态",),
        ("技术信号不是公司基本面或产业主线证据",),
        auto_expandable=True,
    ),
    Capability.FUNDAMENTAL_ANALYSIS: _sem(
        (
            EvidenceDimension.COMPANY_BUSINESS,
            EvidenceDimension.COMPANY_FINANCIALS,
        ),
        ("公司业务结构、财务概况和股东结构",),
        ("不能替代完整估值、催化或市场交易状态",),
        auto_expandable=True,
    ),
    Capability.VALUATION_ANALYSIS: _sem(
        (
            EvidenceDimension.VALUATION,
            EvidenceDimension.RESEARCH_CONSENSUS,
        ),
        ("当前、历史、预期和同行相对估值",),
        ("估值便宜不能单独推出可以买入",),
        auto_expandable=True,
    ),
    Capability.FINANCIAL_STATEMENT_ANALYSIS: _sem(
        (EvidenceDimension.FINANCIAL_STATEMENTS,),
        ("资产负债表、利润表和现金流量表",),
        ("不能单独证明业务竞争力或未来催化",),
        auto_expandable=True,
    ),
    Capability.NEWS_ANALYSIS: _sem(
        (EvidenceDimension.NEWS,),
        ("近期公司、行业、市场或宏观新闻及其影响",),
        ("媒体报道不能冒充公司正式披露",),
        fallbacks=(Capability.PUBLIC_WEB_RESEARCH,),
        auto_expandable=True,
    ),
    Capability.ANNOUNCEMENT_ANALYSIS: _sem(
        (EvidenceDimension.ANNOUNCEMENTS,),
        ("公司正式公告事实",),
        ("没有公告不能证明事件不存在",),
        auto_expandable=True,
    ),
    Capability.RISK_ANALYSIS: _sem(
        (
            EvidenceDimension.RISK_EVENTS,
            EvidenceDimension.ANNOUNCEMENTS,
        ),
        ("公司已披露或公开可核验的风险事件",),
        ("不能把未检索到风险解释为没有风险",),
        fallbacks=(Capability.NEWS_ANALYSIS,),
        auto_expandable=True,
    ),
    Capability.REGULATORY_ANALYSIS: _sem(
        (EvidenceDimension.REGULATORY,),
        ("交易所、监管和上市项目动态",),
        ("不能替代公司财务或市场行情",),
        fallbacks=(Capability.PUBLIC_WEB_RESEARCH,),
        auto_expandable=True,
    ),
    Capability.RESEARCH_REPORT_ANALYSIS: _sem(
        (EvidenceDimension.RESEARCH_CONSENSUS,),
        ("券商研报与一致预期证据",),
        ("机构观点属于预测而不是已实现事实",),
        fallbacks=(Capability.PUBLIC_WEB_RESEARCH,),
        auto_expandable=True,
    ),
    Capability.CATALYST_ANALYSIS: _sem(
        (
            EvidenceDimension.CATALYSTS,
            EvidenceDimension.ANNOUNCEMENTS,
            EvidenceDimension.NEWS,
        ),
        ("未来六至十二个月可回查的公司催化与反向事件",),
        ("没有明确时间窗和来源的叙事不能升级为催化",),
        auto_expandable=True,
    ),
    Capability.SOCIAL_SENTIMENT_ANALYSIS: _sem(
        (EvidenceDimension.SOCIAL_SENTIMENT,),
        ("公开讨论样本中的情绪分布",),
        ("舆情样本不能替代基本面或全市场共识",),
        auto_expandable=True,
    ),
    Capability.STOCK_COMPARISON: _sem(
        (EvidenceDimension.COMPARATIVE_SNAPSHOT,),
        ("多只证券在行情、估值、技术与财务上的横向差异",),
        ("快照对比不是完整买入判断",),
        question_types=frozenset({
            QuestionType.COMPARISON,
            QuestionType.RESEARCH,
            QuestionType.DECISION,
        }),
        auto_expandable=True,
    ),
    Capability.STOCK_DEEP_RESEARCH: _sem(
        (
            EvidenceDimension.COMPANY_BUSINESS,
            EvidenceDimension.COMPANY_FINANCIALS,
            EvidenceDimension.VALUATION,
            EvidenceDimension.REALTIME_MARKET,
            EvidenceDimension.RISK_EVENTS,
            EvidenceDimension.CATALYSTS,
        ),
        ("完整个股业务、财务、估值、交易状态、风险与催化证据",),
        ("深度研究不等于确定性买卖指令",),
        question_types=frozenset({
            QuestionType.RESEARCH,
            QuestionType.DIAGNOSIS,
            QuestionType.COMPARISON,
            QuestionType.FORECAST,
            QuestionType.DECISION,
        }),
        auto_expandable=True,
    ),
    Capability.INVESTMENT_DECISION: _sem(
        (
            EvidenceDimension.MARKET_MAINLINE,
            EvidenceDimension.COMPANY_BUSINESS,
            EvidenceDimension.COMPANY_FINANCIALS,
            EvidenceDimension.VALUATION,
            EvidenceDimension.RISK_EVENTS,
            EvidenceDimension.CATALYSTS,
        ),
        ("八维顺序闸门下的逐股条件买入判断",),
        ("只有八维全部通过才允许输出可买入",),
        question_types=frozenset({QuestionType.DECISION}),
    ),
    Capability.MARKET_OVERVIEW: _sem(
        (
            EvidenceDimension.MARKET_REGIME,
            EvidenceDimension.REALTIME_MARKET,
        ),
        ("指数、市场宽度、成交和整体交易状态",),
        ("不能单独识别未来产业主线或建立板块因果",),
        fallbacks=(Capability.SECTOR_ANALYSIS,),
        auto_expandable=True,
    ),
    Capability.MARKET_MAINLINE_RESEARCH: _sem(
        (
            EvidenceDimension.MARKET_MAINLINE,
            EvidenceDimension.RESEARCH_CONSENSUS,
            EvidenceDimension.MACRO_POLICY,
            EvidenceDimension.INDUSTRY_STRUCTURE,
        ),
        ("未来一至六个月当前主线、候选主线及验证条件",),
        ("候选主线不是对未来赢家的确定性承诺",),
        question_types=frozenset({
            QuestionType.RESEARCH,
            QuestionType.FORECAST,
            QuestionType.DECISION,
        }),
        fallbacks=(
            Capability.MACRO_ANALYSIS,
            Capability.INDUSTRY_RESEARCH,
            Capability.SECTOR_ANALYSIS,
            Capability.PUBLIC_WEB_RESEARCH,
        ),
        auto_expandable=True,
    ),
    Capability.SECTOR_ANALYSIS: _sem(
        (
            EvidenceDimension.SECTOR_STRUCTURE,
            EvidenceDimension.NEWS,
        ),
        ("行业或概念板块强弱、资金和近期信息",),
        ("单日板块热度不能单独升级为中期市场主线",),
        fallbacks=(Capability.NEWS_ANALYSIS,),
        auto_expandable=True,
    ),
    Capability.CAPITAL_FLOW_ANALYSIS: _sem(
        (EvidenceDimension.CAPITAL_FLOW,),
        ("个股多周期资金流持续性",),
        ("资金流不能替代公司基本面或主线证据",),
        auto_expandable=True,
    ),
    Capability.MACRO_ANALYSIS: _sem(
        (EvidenceDimension.MACRO_POLICY,),
        ("宏观指标、利率和货币政策证据",),
        ("宏观证据不能单独推出具体公司结论",),
        question_types=_RESEARCH_FORECAST,
        fallbacks=(Capability.PUBLIC_WEB_RESEARCH,),
        auto_expandable=True,
    ),
    Capability.INDUSTRY_RESEARCH: _sem(
        (
            EvidenceDimension.INDUSTRY_STRUCTURE,
            EvidenceDimension.DOMAIN_CANDIDATES,
        ),
        ("产业受益链与项目实时板块目录中的候选方向",),
        ("产业受益方向不等于公司业务、订单或收入证明",),
        question_types=_RESEARCH_FORECAST,
        auto_expandable=True,
    ),
    Capability.THEME_STOCK_DISCOVERY: _sem(
        (
            EvidenceDimension.DOMAIN_CANDIDATES,
            EvidenceDimension.SECURITY_IDENTITY,
        ),
        ("结构化领域对应的完整板块成分股候选集合",),
        ("候选成员关系不等于公司业务匹配",),
        question_types=frozenset({
            QuestionType.FACTUAL,
            QuestionType.RESEARCH,
        }),
    ),
    Capability.THEME_BUSINESS_EVIDENCE: _sem(
        (
            EvidenceDimension.THEME_BUSINESS,
            EvidenceDimension.COMPANY_BUSINESS,
        ),
        ("候选集合逐股主题业务、投入和发展强度",),
        ("行业新闻不能代替逐家公司证据",),
        question_types=frozenset({
            QuestionType.RESEARCH,
            QuestionType.COMPARISON,
        }),
    ),
    Capability.STOCK_SCREENING: _sem(
        (EvidenceDimension.SCREENING,),
        ("完整强类型规则下的全市场量化筛选结果",),
        ("未声明的筛选条件不会被自动补入",),
        question_types=frozenset({
            QuestionType.FACTUAL,
            QuestionType.RESEARCH,
        }),
    ),
    Capability.COLLECTION_FINANCIAL_FILTER: _sem(
        (
            EvidenceDimension.SCREENING,
            EvidenceDimension.COMPANY_FINANCIALS,
        ),
        ("结构化公司集合上的联合财务条件筛选",),
        ("只能消费明确集合，不能扩大股票范围",),
        question_types=frozenset({
            QuestionType.FACTUAL,
            QuestionType.RESEARCH,
        }),
    ),
    Capability.WATCHLIST_QUERY: _sem(
        (EvidenceDimension.WATCHLIST_STATE,),
        ("当前自选和自选分组状态",),
        ("自选集合不代表推荐或持仓",),
        question_types=_OPERATIONAL,
    ),
    Capability.WATCHLIST_MUTATION: _sem(
        (EvidenceDimension.WATCHLIST_STATE,),
        ("按确认动作修改自选成员",),
        ("不能在未确认时执行修改",),
        question_types=frozenset({QuestionType.OPERATION}),
    ),
    Capability.WATCHLIST_GROUP_MANAGEMENT: _sem(
        (EvidenceDimension.WATCHLIST_STATE,),
        ("查看或管理自选分组",),
        ("高影响分组修改必须确认",),
        question_types=_OPERATIONAL,
    ),
    Capability.DATA_HEALTH: _sem(
        (EvidenceDimension.DATA_QUALITY,),
        ("股票池、行情和财务数据覆盖与维护状态",),
        ("数据健康不能替代具体投资分析",),
        question_types=frozenset({
            QuestionType.FACTUAL,
            QuestionType.DIAGNOSIS,
        }),
        auto_expandable=True,
    ),
    Capability.FORMAL_ANALYSIS: _sem(
        (EvidenceDimension.ANALYSIS_OPERATIONS,),
        ("启动或查询正式分析任务",),
        ("启动持久任务必须按策略确认",),
        question_types=_OPERATIONAL,
    ),
    Capability.ANALYSIS_HISTORY: _sem(
        (EvidenceDimension.ANALYSIS_OPERATIONS,),
        ("搜索、读取或删除正式分析历史",),
        ("删除操作必须确认",),
        question_types=_OPERATIONAL,
    ),
    Capability.ANALYSIS_TEMPLATE_MANAGEMENT: _sem(
        (EvidenceDimension.ANALYSIS_OPERATIONS,),
        ("查看或管理分析模板",),
        ("模板修改不会自动改变已完成报告",),
        question_types=_OPERATIONAL,
    ),
    Capability.BATCH_ANALYSIS: _sem(
        (EvidenceDimension.ANALYSIS_OPERATIONS,),
        ("启动指定范围的批量分析",),
        ("批量执行必须确认且保持完整范围",),
        question_types=frozenset({QuestionType.OPERATION}),
    ),
    Capability.BATCH_RUN_MANAGEMENT: _sem(
        (EvidenceDimension.ANALYSIS_OPERATIONS,),
        ("查看或控制批量任务",),
        ("控制和删除动作必须确认",),
        question_types=_OPERATIONAL,
    ),
    Capability.ANALYSIS_SCHEDULE_MANAGEMENT: _sem(
        (EvidenceDimension.ANALYSIS_OPERATIONS,),
        ("查看或修改自动分析计划",),
        ("修改计划必须确认",),
        question_types=_OPERATIONAL,
    ),
    Capability.NOTIFICATION: _sem(
        (EvidenceDimension.ANALYSIS_OPERATIONS,),
        ("检查通知配置或发送确认内容",),
        ("发送属于外部副作用，不能自动扩展",),
        question_types=_OPERATIONAL,
    ),
    Capability.FINANCIAL_SOURCE_DISCOVERY: _sem(
        (EvidenceDimension.FEED_CONTENT,),
        ("可用财经资讯源及其用途",),
        ("来源目录不是资讯内容本身",),
        auto_expandable=True,
    ),
    Capability.FINANCIAL_FEED_READ: _sem(
        (EvidenceDimension.FEED_CONTENT,),
        ("指定财经 Feed 的结构化内容",),
        ("只读取明确路由，不替代语义搜索",),
        auto_expandable=True,
    ),
    Capability.FINANCIAL_ARTICLE_READ: _sem(
        (EvidenceDimension.FEED_CONTENT,),
        ("用户指定财经文章的正文",),
        ("单篇文章不能代表多源共识",),
        auto_expandable=True,
    ),
    Capability.WEBPAGE_FEED_TRANSFORM: _sem(
        (EvidenceDimension.FEED_CONTENT,),
        ("用户指定网页的 Feed 预览",),
        ("转换预览不写入外部系统",),
        question_types=frozenset({
            QuestionType.FACTUAL,
            QuestionType.OPERATION,
        }),
    ),
    Capability.FINANCIAL_FEED_EXPORT: _sem(
        (EvidenceDimension.FEED_CONTENT,),
        ("导出用户指定的财经 Feed",),
        ("导出属于外部副作用，必须确认",),
        question_types=frozenset({QuestionType.OPERATION}),
    ),
    Capability.PUBLIC_WEB_RESEARCH: _sem(
        (EvidenceDimension.PUBLIC_WEB,),
        ("内部结构化来源无法覆盖时的公开网页证据",),
        ("公开网页不能冒充交易所公告或内部权威数据",),
        question_types=_RESEARCH_FORECAST,
        auto_expandable=True,
    ),
    Capability.TRADE_EXECUTION: _sem(
        (EvidenceDimension.TRADE_STATE,),
        ("独立交易状态机中的账户、风控和订单状态",),
        ("当前未接入账户与下单工具，不能执行交易",),
        question_types=frozenset({
            QuestionType.DECISION,
            QuestionType.OPERATION,
        }),
    ),
})

if set(_SEMANTICS_BY_CAPABILITY) != set(Capability):
    missing = sorted(
        item.value
        for item in set(Capability) - set(_SEMANTICS_BY_CAPABILITY)
    )
    unexpected = sorted(
        item.value
        for item in set(_SEMANTICS_BY_CAPABILITY) - set(Capability)
    )
    raise RuntimeError(
        "semantic registry must cover every capability exactly; "
        f"missing={missing}, unexpected={unexpected}"
    )


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
    if capability == Capability.MARKET_MAINLINE_RESEARCH:
        output_resources = frozenset({
            ResourceType.MARKET_MAINLINE_SNAPSHOT,
            ResourceType.EVIDENCE_COLLECTION,
        })
    policy = ExecutionPolicy(
        effect=_effect(workflow.effect),
        confirmation_required=False,
        max_calls=workflow.max_tool_calls,
        max_parallelism=workflow.max_parallel_steps,
        timeout_seconds=180.0,
        max_attempts=(2 if workflow.effect == EffectClass.READ else 1),
        retry_backoff_seconds=0.5,
        retry_backoff_multiplier=2.0,
        retryable_error_codes=(
            "timeout",
            "connection_error",
            "provider_rate_limited",
            "provider_unavailable",
            "tool_process_crashed",
        ),
    )
    compiler = _COMPILERS.get(capability, _simple_compiler())
    capability_version = (
        "4.2.0"
        if capability == Capability.INVESTMENT_DECISION
        else "4.0.0"
    )
    semantics = _SEMANTICS_BY_CAPABILITY[capability]
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
        freshness_policy=(
            _FRESHNESS_BY_CAPABILITY[capability]
            if workflow.effect == EffectClass.READ
            else _RUN_ONLY_FRESHNESS
        ),
        projector=_project_outcome_resource,
        renderer=(
            RendererMode.DETERMINISTIC
            if workflow.result_contract in _DETERMINISTIC_CONTRACTS
            else RendererMode.EVIDENCE_SYNTHESIS
        ),
        supported_question_types=semantics.question_types,
        evidence_dimensions=semantics.dimensions,
        supported_claims=semantics.claims,
        limitations=semantics.limitations,
        fallback_capabilities=semantics.fallbacks,
        auto_expandable=semantics.auto_expandable,
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
            "supported_question_types": sorted(
                item.value for item in spec.supported_question_types
            ),
            "evidence_dimensions": sorted(
                item.value for item in spec.evidence_dimensions
            ),
            "supported_claims": list(spec.supported_claims),
            "limitations": list(spec.limitations),
            "fallback_capabilities": [
                item.value for item in spec.fallback_capabilities
            ],
            "auto_expandable": spec.auto_expandable,
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
