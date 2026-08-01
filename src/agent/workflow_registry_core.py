"""Core immutable workflow specifications."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

from src.agent.task_workflows import (
    CollectionBehavior,
    ConfirmationPolicy,
    ConfirmationState,
    EffectClass,
    ParameterRequirement,
    ResolvedTask,
    StandardTaskKind,
    TaskResource,
    WorkflowCall,
    WorkflowSpec,
    Compiler,
)
from src.agent.workflow_compilers_primary import (
    compile_announcements as _compile_announcements,
    compile_catalyst_analysis as _compile_catalyst_analysis,
    compile_capital_flow as _compile_capital_flow,
    compile_comparison as _compile_comparison,
    compile_decision_packet as _compile_decision_packet,
    compile_fundamental as _compile_fundamental,
    compile_industry as _compile_industry,
    compile_industry_index_research as _compile_industry_index_research,
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


__all__ = ["build_core_registry", "make_spec", "requirement"]


def requirement(
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


def make_spec(
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
    required_input_resources: Iterable[TaskResource] | None = None,
    alternative_input_resource_groups: Sequence[Iterable[TaskResource]] = (),
    input_resource_parameters: Mapping[str, TaskResource] | None = None,
    parameter_output_resources: Mapping[str, TaskResource] | None = None,
    output_resource_paths: Mapping[
        TaskResource,
        tuple[str, ...],
    ] | None = None,
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
    accepted_input_resources = frozenset(
        {
            *explicit_input_resources,
            *({TaskResource.SECURITY_COLLECTION} if entities else set()),
            *(input_resource_parameters or {}).values(),
        }
    )
    required_resources = (
        accepted_input_resources
        if required_input_resources is None
        else frozenset(required_input_resources)
    )
    alternative_groups = tuple(
        frozenset(group) for group in alternative_input_resource_groups
    )
    if not required_resources <= accepted_input_resources:
        raise ValueError("required_input_resources must be accepted inputs")
    if any(not group or not group <= accepted_input_resources for group in alternative_groups):
        raise ValueError("alternative input groups must be non-empty accepted inputs")
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
                    if effect in {EffectClass.DESTRUCTIVE, EffectClass.EXTERNAL, EffectClass.TRADE}
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
        input_resources=accepted_input_resources,
        required_input_resources=required_resources,
        alternative_input_resource_groups=alternative_groups,
        input_resource_parameters=MappingProxyType(dict(input_resource_parameters or {})),
        parameter_output_resources=MappingProxyType(dict(parameter_output_resources or {})),
        output_resource_paths=MappingProxyType(
            dict(output_resource_paths or {})
        ),
        output_resources=frozenset(
            {
                *({TaskResource.SECURITY_COLLECTION} if collection_behavior != CollectionBehavior.NONE else set()),
                *(parameter_output_resources or {}).values(),
                *(output_resource_paths or {}).keys(),
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

def build_core_registry(spec_factory: Callable[..., WorkflowSpec]) -> dict[StandardTaskKind, WorkflowSpec]:
    return {
            StandardTaskKind.GENERAL_RESPONSE: spec_factory(
                StandardTaskKind.GENERAL_RESPONSE,
                "通用回答",
                "无需外部或实时数据的日常知识、解释、写作或计算。",
                (),
                _compile_no_tools,
            ),
            StandardTaskKind.SECURITY_LOOKUP: spec_factory(
                StandardTaskKind.SECURITY_LOOKUP,
                "证券识别",
                "按名称、代码、市场或行业查询证券身份。",
                {"search_stocks"},
                _compile_security_lookup,
                collection=CollectionBehavior.SOURCE,
            ),
            StandardTaskKind.REALTIME_QUOTE: spec_factory(
                StandardTaskKind.REALTIME_QUOTE,
                "实时行情",
                "查询一只或多只证券的当前行情。",
                {"get_realtime_quotes"},
                _compile_realtime_quote,
                entities=True,
            ),
            StandardTaskKind.PRICE_HISTORY: spec_factory(
                StandardTaskKind.PRICE_HISTORY,
                "历史行情",
                "查询近期或指定日期区间的历史行情。",
                {"get_kline", "get_history_data"},
                _compile_price_history,
                entities=True,
            ),
            StandardTaskKind.TECHNICAL_ANALYSIS: spec_factory(
                StandardTaskKind.TECHNICAL_ANALYSIS,
                "技术分析",
                "计算趋势、动量、波动和量价技术指标。",
                {"get_technical_indicators"},
                _compile_technical,
                entities=True,
            ),
            StandardTaskKind.FUNDAMENTAL_ANALYSIS: spec_factory(
                StandardTaskKind.FUNDAMENTAL_ANALYSIS,
                "基本面分析",
                "分析公司资料、核心财务、主营构成和股东结构。",
                {
                    "get_stock_info",
                    "get_financials",
                    "get_business_segments",
                    "get_shareholder_structure",
                    "get_company_structured_evidence",
                    "get_multi_stock_snapshot",
                },
                _compile_fundamental,
                entities=True,
                max_tool_calls=10,
            ),
            StandardTaskKind.VALUATION_ANALYSIS: spec_factory(
                StandardTaskKind.VALUATION_ANALYSIS,
                "估值分析",
                "分析当前、历史、预期和同行相对估值。",
                {"get_valuation_ratios", "get_consensus_estimates", "get_peer_comparison", "get_multi_stock_snapshot"},
                _compile_valuation,
                entities=True,
            ),
            StandardTaskKind.FINANCIAL_STATEMENT_ANALYSIS: spec_factory(
                StandardTaskKind.FINANCIAL_STATEMENT_ANALYSIS,
                "财报分析",
                "分析资产负债表、利润表和现金流量表。",
                {"get_balance_sheet", "get_income_statement", "get_cashflow"},
                _compile_statements,
                entities=True,
            ),
            StandardTaskKind.NEWS_ANALYSIS: spec_factory(
                StandardTaskKind.NEWS_ANALYSIS,
                "新闻分析",
                "查询公司或主题新闻并分析影响。",
                {"search_news", "get_announcements", "search_financial_news"},
                _compile_news,
            ),
            StandardTaskKind.ANNOUNCEMENT_ANALYSIS: spec_factory(
                StandardTaskKind.ANNOUNCEMENT_ANALYSIS,
                "公告分析",
                "查询并分析正式公司公告。",
                {"get_announcements"},
                _compile_announcements,
                entities=True,
            ),
            StandardTaskKind.RISK_ANALYSIS: spec_factory(
                StandardTaskKind.RISK_ANALYSIS,
                "风险分析",
                "获取公告与新闻证据并由模型研判风险。",
                {"get_announcements", "get_risk_events"},
                _compile_risk,
                entities=True,
            ),
            StandardTaskKind.REGULATORY_ANALYSIS: spec_factory(
                StandardTaskKind.REGULATORY_ANALYSIS,
                "监管信息",
                "查询交易所披露、问询、项目和上市监管动态。",
                {"get_regulatory_updates"},
                _compile_regulatory,
            ),
            StandardTaskKind.RESEARCH_REPORT_ANALYSIS: spec_factory(
                StandardTaskKind.RESEARCH_REPORT_ANALYSIS,
                "个股研报",
                "查询单只证券的券商研报和一致预期证据。",
                {"get_research_report"},
                _compile_reports,
                entities=True,
            ),
            StandardTaskKind.CATALYST_ANALYSIS: spec_factory(
                StandardTaskKind.CATALYST_ANALYSIS,
                "未来催化事件",
                "核验公司未来6—12个月具有明确时间窗和可回查来源的催化事件。",
                {"analyze_stock_catalysts"},
                _compile_catalyst_analysis,
                entities=True,
                max_tool_calls=8,
                max_parallel_steps=2,
            ),
            StandardTaskKind.SOCIAL_SENTIMENT_ANALYSIS: spec_factory(
                StandardTaskKind.SOCIAL_SENTIMENT_ANALYSIS,
                "舆情分析",
                "采样并分析个股公开讨论情绪。",
                {"get_social_sentiment"},
                _compile_sentiment,
                entities=True,
            ),
            StandardTaskKind.STOCK_COMPARISON: spec_factory(
                StandardTaskKind.STOCK_COMPARISON,
                "股票对比",
                "横向比较多只证券的行情、估值、技术与财务。",
                {"get_multi_stock_snapshot", "get_peer_comparison"},
                _compile_comparison,
                entities=True,
            ),
            StandardTaskKind.STOCK_DEEP_RESEARCH: spec_factory(
                StandardTaskKind.STOCK_DEEP_RESEARCH,
                "个股深度研究",
                "收集完整业务、财务、估值、交易状态和风险证据。",
                {"get_multi_stock_decision_evidence"},
                _compile_decision_packet,
                entities=True,
            ),
            StandardTaskKind.INVESTMENT_DECISION: spec_factory(
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
            StandardTaskKind.MARKET_OVERVIEW: spec_factory(
                StandardTaskKind.MARKET_OVERVIEW,
                "市场概览",
                "分析指数、市场宽度与整体交易状态。",
                {"get_market_status", "get_market_breadth", "get_market_regime", "get_index_data"},
                _compile_market,
            ),
            StandardTaskKind.MARKET_MAINLINE_RESEARCH: spec_factory(
                StandardTaskKind.MARKET_MAINLINE_RESEARCH,
                "市场主线研究",
                "基于政策、产业供需、技术路线、资本开支和机构策略证据，研判未来一至六个月的当前主线与候选主线；指数涨跌和单日热度不能单独建立主线。",
                {"prepare_market_mainline_snapshot"},
                _compile_market_mainline_research,
                max_tool_calls=1,
                max_parallel_steps=1,
            ),
            StandardTaskKind.SECTOR_ANALYSIS: spec_factory(
                StandardTaskKind.SECTOR_ANALYSIS,
                "板块分析",
                "比较行业或概念板块强弱、资金和近期信息。",
                {"get_sector_list", "get_sector_flow", "search_financial_news"},
                _compile_sector,
            ),
            StandardTaskKind.CAPITAL_FLOW_ANALYSIS: spec_factory(
                StandardTaskKind.CAPITAL_FLOW_ANALYSIS,
                "资金流分析",
                "分析个股多周期资金流持续性。",
                {"get_stock_capital_flow"},
                _compile_capital_flow,
                entities=True,
            ),
            StandardTaskKind.MACRO_ANALYSIS: spec_factory(
                StandardTaskKind.MACRO_ANALYSIS,
                "宏观分析",
                "分析宏观指标、利率或货币政策操作。",
                {"get_macro_indicator", "get_bond_yield", "get_monetary_policy_operations", "search_research_library"},
                _compile_macro,
            ),
            StandardTaskKind.INDUSTRY_RESEARCH: spec_factory(
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
            StandardTaskKind.INDUSTRY_INDEX_RESEARCH: spec_factory(
                StandardTaskKind.INDUSTRY_INDEX_RESEARCH,
                "申万行业指数研究",
                "查询申万行业指数目录、历史表现和成分股，保持与项目主题板块候选发现相互独立。",
                {"get_industry_index_context"},
                _compile_industry_index_research,
                max_tool_calls=1,
                max_parallel_steps=1,
                result_contract="industry_index_context",
            ),
    }
