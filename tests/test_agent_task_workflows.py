from __future__ import annotations

import asyncio
from contextlib import contextmanager
import inspect
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock, patch

import pytest

from src.agent.task_executor import (
    ConfirmationRequired,
    WorkflowExecutor,
    WorkflowPolicyValidator,
    action_fingerprint,
)
from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    Capability,
    ClaimRequirementV2,
    GoalContractV2,
    OrchestratorV2Error,
    QuestionType,
    UncertaintyMode,
)
from src.agent.orchestrator_v2.registry import capability_catalog, capability_for
from src.agent.orchestrator_v2.runtime import (
    CompiledIntentGraphV2,
    CompiledTaskV2,
)
from src.agent.task_planner import (
    resolve_plan_entities,
    validate_candidate_plan,
)
import src.agent.task_planner as task_planner_module
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResultSelectionMode,
    ResultSelectionSpec,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
    WORKFLOW_REGISTRY,
    compile_task,
    registered_workflow_tools,
)
from src.tools.registry import ToolRegistry


class _Controller:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.tool_calls: list[str] = []
        self._stream_tasks: list = []
        self.assistant_text_snapshot = ""

    def append_text(self, text: str) -> None:
        self.texts.append(text)

    def append_reasoning(self, _text: str) -> None:
        return None

    async def add_tool_call(self, name: str, tool_call_id: str | None = None):
        self.tool_calls.append(name)
        tool = MagicMock()
        tool.append_args_text = MagicMock()
        tool.set_response = MagicMock()
        return tool


@contextmanager
def _unified_pipeline(
    chat_mod,
    plan: TaskPlan,
    resolved: list[ResolvedTask],
):
    dimensions = tuple(sorted({
        dimension
        for task in resolved
        for dimension in capability_for(
            Capability(task.kind.value)
        ).evidence_dimensions
    }, key=lambda item: item.value))
    goal = GoalContractV2(
        objective="完成测试计划",
        question_type=QuestionType.RESEARCH,
        uncertainty_mode=UncertaintyMode.BOUNDED,
        deliverables=("返回测试计划结果",),
        claims=(ClaimRequirementV2(
            claim_id="result",
            question="测试计划是否形成结果",
            required_dimensions=dimensions,
        ),),
    )
    compiled = CompiledIntentGraphV2(
        run_id="test-run",
        plan=plan,
        tasks=tuple(
            CompiledTaskV2(
                task=task,
                capability=Capability(task.kind.value),
                capability_version=capability_for(
                    Capability(task.kind.value)
                ).version,
                intent_schema_version=capability_for(
                    Capability(task.kind.value)
                ).schema_version,
                execution_policy=capability_for(
                    Capability(task.kind.value)
                ).execution_policy,
                resource_fingerprint=f"test-{task.task_id}",
            )
            for task in resolved
        ),
        assumptions=(),
    )
    graph = MagicMock()
    graph.run_id = "test-run"
    graph.outline.goal = goal
    graph.trace.schema_version = "orchestrator-4.0"
    graph.trace.stage_durations_ms = {}
    with patch.object(
        chat_mod,
        "plan_intent_graph_v2",
        new=AsyncMock(return_value=graph),
    ), patch.object(
        chat_mod,
        "compile_intent_graph_v2",
        new=AsyncMock(return_value=compiled),
    ):
        yield


def _task(
    kind: StandardTaskKind,
    *,
    task_id: str = "task_a",
    entities: list[str] | None = None,
    parameters: dict | None = None,
    depends_on: list[str] | None = None,
    confirmation: ConfirmationState = ConfirmationState.NOT_REQUIRED,
    result_selection: ResultSelectionSpec | None = None,
) -> StandardTask:
    normalized_parameters = dict(parameters or {})
    if kind == StandardTaskKind.THEME_BUSINESS_EVIDENCE:
        normalized_parameters.setdefault("evidence_context", {
            "target_topics": ["人形机器人"],
            "domain_theses": [
                {
                    "label": str(domain.get("label") or ""),
                    "rationale": "该板块是人形机器人产业链的直接受益环节",
                }
                for domain in normalized_parameters.get("domains") or []
                if isinstance(domain, dict) and domain.get("label")
            ],
        })
    return StandardTask(
        task_id=task_id,
        kind=kind,
        objective=kind.value,
        entity_scope=EntityScope.NONE,
        entities=entities or [],
        parameters=normalized_parameters,
        depends_on=depends_on or [],
        result_selection=(
            result_selection
            or ResultSelectionSpec(
                mode=ResultSelectionMode.ALL_RELEVANT,
                max_items=None,
            )
            if kind == StandardTaskKind.INDUSTRY_RESEARCH
            else None
        ),
        output_requirements=[],
        confirmation=confirmation,
        confidence=0.95,
    )


def _domain(
    label: str,
    *boards: str,
    mapping_type: str | None = None,
    rationale: str = "测试中的板块目录解析结果",
    unresolved_parts: list[str] | None = None,
) -> dict:
    unresolved = mapping_type == "unresolved"
    selected = list(boards) or ([] if unresolved else [label])
    return {
        "label": label,
        "board_queries": selected,
        "mapping_type": mapping_type or "catalog_binding",
        "rationale": rationale,
        "unresolved_parts": unresolved_parts or ([label] if unresolved else []),
    }


def _financial_conditions(*conditions: dict) -> dict:
    return {"conditions": list(conditions)}


def _model_response(function_name: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        tool_calls=[SimpleNamespace(function=SimpleNamespace(
            name=function_name,
            arguments=json.dumps(payload, ensure_ascii=False),
        ))],
        content=None,
    ))])


def test_every_registered_tool_belongs_to_at_least_one_fixed_workflow() -> None:
    assert set(ToolRegistry().get_tool_names()) == set(registered_workflow_tools())
    assert all(
        spec.max_tool_calls <= 104
        for kind, spec in WORKFLOW_REGISTRY.items()
        if kind not in {
            StandardTaskKind.THEME_BUSINESS_EVIDENCE,
            StandardTaskKind.INVESTMENT_DECISION,
        }
    )
    assert WORKFLOW_REGISTRY[
        StandardTaskKind.THEME_BUSINESS_EVIDENCE
    ].max_tool_calls == 6000
    assert WORKFLOW_REGISTRY[
        StandardTaskKind.INVESTMENT_DECISION
    ].max_tool_calls == 302


def test_planner_catalog_does_not_expose_tool_names() -> None:
    catalog_text = json.dumps(capability_catalog(), ensure_ascii=False)
    assert "tool_whitelist" not in catalog_text
    for tool_name in ToolRegistry().get_tool_names():
        assert tool_name not in catalog_text


def test_task_planner_contains_no_request_phrase_router() -> None:
    source = inspect.getsource(task_planner_module)
    source_lines = {line.strip() for line in source.splitlines()}
    assert "import re" not in source_lines
    assert not any(line.startswith("from re import") for line in source_lines)
    assert "re.search" not in source
    assert "_explicit_" not in source
    assert "deterministic_" not in source


def test_every_enabled_standard_task_has_a_schema_valid_fixed_workflow() -> None:
    screen_spec = {
        "version": "1.0",
        "universe": {
            "status": "active",
            "markets": ["sh", "sz", "bj"],
            "include_st": False,
            "min_listing_trading_days": 120,
            "price_adjustment": "qfq",
        },
        "technical_rule": {
            "strategy": "atr_relative_frequency",
            "atr_period": 14,
            "atr_average": "wilder",
            "baseline_period": 60,
            "baseline_average": "sma",
            "threshold_operator": "multiply",
            "threshold_value": 1.5,
            "daily_comparison": "gte",
            "lookback_days": 20,
            "min_qualified_days": 5,
            "min_qualified_ratio_pct": None,
        },
        "financial_filters": [],
        "sort": {"field": "qualified_days", "order": "desc"},
        "output_fields": ["qualified_days", "current_atr_pct"],
        "preview_limit": 10,
    }
    parameters = {
        StandardTaskKind.SECURITY_LOOKUP: {"query": "贵州茅台"},
        StandardTaskKind.PRICE_HISTORY: {"count": 60},
        StandardTaskKind.TECHNICAL_ANALYSIS: {},
        StandardTaskKind.NEWS_ANALYSIS: {
            "query": "白酒行业", "topic": "industry", "subjects": ["白酒"], "days": 7,
        },
        StandardTaskKind.REGULATORY_ANALYSIS: {"keyword": "再融资"},
        StandardTaskKind.MARKET_OVERVIEW: {"include_index": True},
        StandardTaskKind.SECTOR_ANALYSIS: {"type": "industry", "period": "today"},
        StandardTaskKind.MACRO_ANALYSIS: {"indicators": ["PMI"]},
        StandardTaskKind.INDUSTRY_RESEARCH: {
            "query": "人形机器人产业链",
            "domains": [_domain("人形机器人")],
        },
        StandardTaskKind.THEME_STOCK_DISCOVERY: {"domains": [_domain("减速器")]},
        StandardTaskKind.THEME_BUSINESS_EVIDENCE: {
            "domains": [_domain("人形机器人")],
            "candidate_scope": "public_fallback",
        },
        StandardTaskKind.STOCK_SCREENING: {"screen_spec": screen_spec},
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER: _financial_conditions({
            "metric": "debt_ratio",
            "period_basis": "latest_report",
            "operator": "gt",
            "threshold": 70,
            "threshold_unit": "percent",
            "action": "exclude_matching",
        }),
        StandardTaskKind.WATCHLIST_MUTATION: {"action": "add"},
        StandardTaskKind.WATCHLIST_GROUP_MANAGEMENT: {"action": "list"},
        StandardTaskKind.FORMAL_ANALYSIS: {"action": "status"},
        StandardTaskKind.ANALYSIS_HISTORY: {"action": "search"},
        StandardTaskKind.ANALYSIS_TEMPLATE_MANAGEMENT: {"action": "list"},
        StandardTaskKind.BATCH_ANALYSIS: {
            "scope": "symbols",
            "analysis_mode": "buy_criteria",
        },
        StandardTaskKind.BATCH_RUN_MANAGEMENT: {"action": "list"},
        StandardTaskKind.ANALYSIS_SCHEDULE_MANAGEMENT: {"action": "get"},
        StandardTaskKind.NOTIFICATION: {"action": "status"},
        StandardTaskKind.FINANCIAL_FEED_READ: {"route_path": "/cls/telegraph"},
        StandardTaskKind.FINANCIAL_ARTICLE_READ: {
            "route_path": "/cls/telegraph",
            "title": "测试文章",
        },
        StandardTaskKind.WEBPAGE_FEED_TRANSFORM: {"url": "https://example.com/news"},
        StandardTaskKind.FINANCIAL_FEED_EXPORT: {"route_path": "/cls/telegraph"},
        StandardTaskKind.PUBLIC_WEB_RESEARCH: {"query": "公开资料"},
    }
    entity_tasks = {
        StandardTaskKind.REALTIME_QUOTE,
        StandardTaskKind.PRICE_HISTORY,
        StandardTaskKind.TECHNICAL_ANALYSIS,
        StandardTaskKind.FUNDAMENTAL_ANALYSIS,
        StandardTaskKind.VALUATION_ANALYSIS,
        StandardTaskKind.FINANCIAL_STATEMENT_ANALYSIS,
        StandardTaskKind.ANNOUNCEMENT_ANALYSIS,
        StandardTaskKind.RISK_ANALYSIS,
        StandardTaskKind.RESEARCH_REPORT_ANALYSIS,
        StandardTaskKind.CATALYST_ANALYSIS,
        StandardTaskKind.SOCIAL_SENTIMENT_ANALYSIS,
        StandardTaskKind.STOCK_COMPARISON,
        StandardTaskKind.STOCK_DEEP_RESEARCH,
        StandardTaskKind.INVESTMENT_DECISION,
        StandardTaskKind.CAPITAL_FLOW_ANALYSIS,
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        StandardTaskKind.WATCHLIST_MUTATION,
        StandardTaskKind.BATCH_ANALYSIS,
    }
    exercised: set[StandardTaskKind] = set()
    for kind, spec in WORKFLOW_REGISTRY.items():
        if not spec.enabled:
            continue
        symbols = ("600519", "000858") if kind == StandardTaskKind.STOCK_COMPARISON else (
            ("600519",) if kind in entity_tasks else ()
        )
        candidate = _task(
            kind,
            task_id=f"task_{len(exercised)}",
            parameters=parameters.get(kind, {}),
            confirmation=ConfirmationState.EXPLICIT,
        )
        resolved = ResolvedTask(candidate=candidate, symbols=symbols)
        validator = WorkflowPolicyValidator(
            ToolRegistry(),
            approved_actions={action_fingerprint(resolved)},
        )
        calls = validator.preflight_task(resolved)
        assert len(calls) <= spec.max_tool_calls
        assert {call.call.tool_name for call in calls} <= spec.tool_whitelist
        exercised.add(kind)
    assert exercised == {
        kind for kind, spec in WORKFLOW_REGISTRY.items() if spec.enabled
    }


def test_domain_discovery_compiles_only_internal_candidate_tool() -> None:
    candidate = _task(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        parameters={
            "domains": [
                _domain("行星滚柱丝杠", "机器人执行器"),
                _domain("减速器"),
                _domain("无框力矩电机", "机器人执行器"),
            ],
        },
    )
    calls = compile_task(ResolvedTask(candidate=candidate))
    assert [call.tool_name for call in calls] == ["get_domain_stock_candidates"]
    assert calls[0].arguments == {
        "domains": [
            _domain("行星滚柱丝杠", "机器人执行器"),
            _domain("减速器"),
            _domain("无框力矩电机", "机器人执行器"),
        ],
    }


def test_industry_research_always_uses_one_project_catalog_snapshot() -> None:
    project_task = _task(
        StandardTaskKind.INDUSTRY_RESEARCH,
        parameters={
            "query": "某新兴产业哪些方向最受益",
            "domains": [_domain("某新兴产业", "实际主题板块")],
        },
    )
    project_calls = compile_task(ResolvedTask(candidate=project_task))
    assert [call.tool_name for call in project_calls] == [
        "get_domain_board_catalog"
    ]

    fallback_task = _task(
        StandardTaskKind.INDUSTRY_RESEARCH,
        parameters={
            "query": "目录外产业哪些方向最受益",
            "domains": [
                _domain(
                    "目录外产业",
                    mapping_type="unresolved",
                    rationale="当前实时板块目录没有合理相关板块。",
                ),
            ],
        },
    )
    fallback_calls = compile_task(ResolvedTask(candidate=fallback_task))
    assert [call.tool_name for call in fallback_calls] == [
        "get_domain_board_catalog",
    ]


def test_theme_business_evidence_compiles_only_after_candidates_are_bound() -> None:
    candidate = _task(
        StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        parameters={
            "domains": [
                {"label": "灵巧手"},
                {"label": "六维力传感器"},
                {"label": "谐波减速器"},
            ],
            "candidate_scope": "candidate_collection",
            "query": "有哪些公司正在大力发展",
        },
    )

    calls = compile_task(ResolvedTask(
        candidate=candidate,
        symbols=("002979", "300007", "301368"),
    ))

    assert "get_domain_stock_candidates" not in [
        call.tool_name for call in calls
    ]
    assert [call.tool_name for call in calls] == [
        "get_company_theme_evidence",
        "get_company_theme_evidence",
        "get_company_theme_evidence",
    ]
    assert [call.arguments["symbol"] for call in calls] == [
        "002979",
        "300007",
        "301368",
    ]
    assert all(call.arguments["target_topics"] == ["人形机器人"] for call in calls)
    assert all(call.arguments["domains"] == [
        "灵巧手",
        "六维力传感器",
        "谐波减速器",
    ] for call in calls)


def test_theme_business_evidence_compiles_all_488_candidates_as_single_stock_calls() -> None:
    candidate = _task(
        StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        parameters={
            "domains": [{"label": "目标领域"}],
            "candidate_scope": "candidate_collection",
            "query": "逐股核验业务进展",
        },
    )
    symbols = tuple(f"{100000 + index:06d}" for index in range(488))

    calls = WorkflowPolicyValidator(ToolRegistry()).preflight_task(
        ResolvedTask(candidate=candidate, symbols=symbols)
    )

    assert len(calls) == 488
    assert {
        call.call.tool_name
        for call in calls
    } == {"get_company_theme_evidence"}
    assert [
        call.arguments["symbol"]
        for call in calls
    ] == list(symbols)
    assert all("," not in call.arguments["symbol"] for call in calls)


def test_theme_business_evidence_plan_rejects_missing_candidate_dependency() -> None:
    candidate = _task(
        StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        parameters={
            "domains": [{"label": "减速器"}],
            "candidate_scope": "candidate_collection",
            "query": "找正在大力发展的股票",
        },
    )

    with pytest.raises(
        ValueError,
        match="dependency that produces security_collection",
    ):
        validate_candidate_plan(TaskPlan(tasks=[candidate]))


def test_free_form_context_is_not_forwarded_as_a_board_identifier() -> None:
    candidate = _task(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        parameters={
            "domains": [
                _domain("行星滚柱丝杠", "机器人执行器"),
                _domain("减速器"),
                _domain("无框力矩电机", "机器人执行器"),
            ],
        },
    )
    plan = TaskPlan(tasks=[candidate])
    validate_candidate_plan(plan)
    calls = compile_task(ResolvedTask(candidate=candidate))
    assert calls[0].tool_name == "get_domain_stock_candidates"
    assert set(calls[0].arguments) == {"domains"}


def test_collection_financial_filter_compiles_all_batches_without_truncation() -> None:
    symbols = tuple(f"{index:06d}" for index in range(47))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions({
            "metric": "debt_ratio",
            "period_basis": "latest_report",
            "operator": "gt",
            "threshold": 70,
            "threshold_unit": "percent",
            "action": "exclude_matching",
        }),
    )
    calls = compile_task(ResolvedTask(candidate=candidate, symbols=symbols))
    assert len(calls) == 2
    assert [len(call.arguments["symbols"].split(",")) for call in calls] == [24, 23]
    assert all(call.arguments["metric"] == "debt_ratio" for call in calls)
    assert all(call.arguments["period_basis"] == "latest_report" for call in calls)


def test_domain_discovery_and_multi_condition_filter_form_one_resource_dag() -> None:
    discovery = _task(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        task_id="discover",
        parameters={"domains": [_domain("灵巧手"), _domain("丝杠"), _domain("减速器")]},
    )
    financial_filter = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        task_id="filter",
        parameters=_financial_conditions(
            {
                "metric": "debt_ratio", "period_basis": "latest_report",
                "operator": "gt", "threshold": 70,
                "threshold_unit": "percent", "action": "exclude_matching",
            },
            {
                "metric": "net_profit", "period_basis": "previous_fiscal_year",
                "operator": "lt", "threshold": 0,
                "threshold_unit": "cny", "action": "exclude_matching",
            },
            {
                "metric": "revenue", "period_basis": "fiscal_year",
                "fiscal_year": 2025, "operator": "lt", "threshold": 5,
                "threshold_unit": "yi_cny", "action": "exclude_matching",
            },
        ),
        depends_on=["discover"],
    )
    plan = TaskPlan(tasks=[discovery, financial_filter])

    validate_candidate_plan(plan)
    resolved = resolve_plan_entities(
        plan,
        current_entities=[],
        previous_answer_entities=[],
    )

    assert [task.task_id for task in resolved] == ["discover", "filter"]
    assert resolved[1].symbols == ()
    contracts = {
        item["capability"]: item
        for item in capability_catalog()
    }
    assert contracts["theme_stock_discovery"]["output_resources"] == [
        "domain_collection",
        "security_collection"
    ]
    assert contracts["collection_financial_filter"]["input_resources"] == [
        "security_collection"
    ]


def test_previous_fiscal_year_revenue_filter_has_one_typed_contract_for_every_batch() -> None:
    symbols = tuple(f"{index:06d}" for index in range(47))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions({
            "metric": "revenue",
            "period_basis": "previous_fiscal_year",
            "operator": "lt",
            "threshold": 5,
            "threshold_unit": "yi_cny",
            "action": "exclude_matching",
        }),
    ).model_copy(update={"entity_scope": EntityScope.PREVIOUS_ANSWER})

    validate_candidate_plan(TaskPlan(tasks=[candidate]))
    calls = compile_task(ResolvedTask(candidate=candidate, symbols=symbols))

    assert len(calls) == 2
    assert all(call.arguments["metric"] == "revenue" for call in calls)
    assert all(call.arguments["period_basis"] == "previous_fiscal_year" for call in calls)
    assert all("fiscal_year" not in call.arguments for call in calls)


def test_collection_financial_filter_rejects_metric_unit_mismatch() -> None:
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions({
            "metric": "revenue",
            "period_basis": "previous_fiscal_year",
            "operator": "lt",
            "threshold": 5,
            "threshold_unit": "percent",
            "action": "exclude_matching",
        }),
    ).model_copy(update={"entity_scope": EntityScope.PREVIOUS_ANSWER})

    with pytest.raises(ValueError, match="currency metrics require a CNY threshold unit"):
        validate_candidate_plan(TaskPlan(tasks=[candidate]))


def test_collection_financial_filter_accepts_negative_profit_threshold() -> None:
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions({
            "metric": "deducted_net_profit",
            "period_basis": "previous_fiscal_year",
            "operator": "lt",
            "threshold": -1,
            "threshold_unit": "yi_cny",
            "action": "exclude_matching",
        }),
    ).model_copy(update={"entity_scope": EntityScope.PREVIOUS_ANSWER})

    validate_candidate_plan(TaskPlan(tasks=[candidate]))


def test_collection_financial_filter_keeps_net_profit_distinct_from_deducted_profit() -> None:
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions({
            "metric": "net_profit",
            "period_basis": "previous_fiscal_year",
            "operator": "lt",
            "threshold": 0,
            "threshold_unit": "cny",
            "action": "exclude_matching",
        }),
    ).model_copy(update={"entity_scope": EntityScope.PREVIOUS_ANSWER})

    validate_candidate_plan(TaskPlan(tasks=[candidate]))
    calls = compile_task(ResolvedTask(candidate=candidate, symbols=("000001",)))

    assert calls[0].arguments["metric"] == "net_profit"


def test_collection_filter_executor_runs_every_batch_even_when_one_fails() -> None:
    symbols = tuple(f"{index:06d}" for index in range(47))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions({
            "metric": "debt_ratio",
            "period_basis": "latest_report",
            "operator": "gt",
            "threshold": 70,
            "threshold_unit": "percent",
            "action": "exclude_matching",
        }),
    )
    seen: list[str] = []
    active = 0
    max_active = 0

    async def runner(_call, arguments):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        seen.append(arguments["symbols"])
        await asyncio.sleep(0)
        failed = arguments["symbols"].startswith("000024")
        result = {
            "success": not failed,
            "errors": ["data source unavailable"] if failed else [],
            "partial": False,
            "items": [],
        }
        active -= 1
        return result

    result = asyncio.run(WorkflowExecutor(ToolRegistry(), runner).execute([
        ResolvedTask(candidate=candidate, symbols=symbols),
    ]))
    assert len(seen) == 2
    assert sorted(len(batch.split(",")) for batch in seen) == [23, 24]
    assert result.tasks[0].status == "failed"
    assert len(result.tasks[0].calls) == 2
    assert max_active == 2


def test_capability_intent_rejects_fields_outside_its_exact_schema() -> None:
    intent_model = capability_for(Capability.REALTIME_QUOTE).intent_model
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        intent_model.model_validate({"query": "联网找股票"})


def test_policy_requires_explicit_confirmation_before_delete() -> None:
    candidate = _task(
        StandardTaskKind.ANALYSIS_HISTORY,
        parameters={"action": "delete", "record_ids": "record-1"},
        confirmation=ConfirmationState.MISSING,
    )
    validator = WorkflowPolicyValidator(ToolRegistry())
    with pytest.raises(ConfirmationRequired, match="requires explicit confirmation"):
        validator.preflight_task(ResolvedTask(candidate=candidate))


def test_explicit_confirmation_requires_a_matching_prior_review() -> None:
    candidate = _task(
        StandardTaskKind.ANALYSIS_HISTORY,
        parameters={"action": "delete", "record_ids": "record-1"},
        confirmation=ConfirmationState.EXPLICIT,
    )
    resolved = ResolvedTask(candidate=candidate)
    with pytest.raises(ConfirmationRequired):
        WorkflowPolicyValidator(ToolRegistry()).preflight_task(resolved)

    calls = WorkflowPolicyValidator(
        ToolRegistry(),
        approved_actions={action_fingerprint(resolved)},
    ).preflight_task(resolved)
    assert [item.call.tool_name for item in calls] == ["delete_analysis_history"]


@pytest.mark.parametrize(
    ("kind", "parameters", "missing_name"),
    [
        (StandardTaskKind.ANALYSIS_HISTORY, {"action": "read"}, "record_id"),
        (StandardTaskKind.ANALYSIS_HISTORY, {"action": "delete"}, "record_ids"),
        (StandardTaskKind.WATCHLIST_GROUP_MANAGEMENT, {"action": "rename", "group": "核心"}, "new_name"),
        (StandardTaskKind.ANALYSIS_TEMPLATE_MANAGEMENT, {"action": "create", "name": "模板"}, "content"),
        (StandardTaskKind.BATCH_RUN_MANAGEMENT, {"action": "detail"}, "run_id"),
        (StandardTaskKind.NOTIFICATION, {"action": "send", "content_type": "custom"}, "message"),
    ],
)
def test_action_specific_parameters_are_rejected_before_tool_execution(
    kind: StandardTaskKind,
    parameters: dict,
    missing_name: str,
) -> None:
    intent_model = capability_for(Capability(kind.value)).intent_model
    with pytest.raises(ValueError, match=missing_name):
        intent_model.model_validate(parameters)


def test_template_update_requires_a_real_change() -> None:
    intent_model = capability_for(
        Capability.ANALYSIS_TEMPLATE_MANAGEMENT
    ).intent_model
    with pytest.raises(ValueError, match="update requires"):
        intent_model.model_validate({"action": "update", "template_id": 1})


def test_saved_batch_scope_requires_declared_and_explicit_confirmation() -> None:
    unmarked = _task(
        StandardTaskKind.BATCH_ANALYSIS,
        parameters={
            "scope": "group",
            "group_name": "机器人",
            "analysis_mode": "buy_criteria",
        },
    )
    with pytest.raises(ValueError, match="confirmation state"):
        validate_candidate_plan(TaskPlan(tasks=[unmarked]))

    pending_confirmation = unmarked.model_copy(update={
        "confirmation": ConfirmationState.MISSING,
    })
    validate_candidate_plan(TaskPlan(tasks=[pending_confirmation]))
    with pytest.raises(ConfirmationRequired):
        WorkflowPolicyValidator(ToolRegistry()).preflight_task(
            ResolvedTask(candidate=pending_confirmation)
        )


def test_batch_symbol_scope_is_bounded_before_runner_is_called() -> None:
    candidate = _task(
        StandardTaskKind.BATCH_ANALYSIS,
        parameters={"scope": "symbols", "analysis_mode": "buy_criteria"},
        confirmation=ConfirmationState.EXPLICIT,
    )
    symbols = tuple(f"{index:06d}" for index in range(51))
    calls = 0

    async def runner(_call, _arguments):
        nonlocal calls
        calls += 1
        return {"success": True, "errors": [], "partial": False}

    resolved = ResolvedTask(candidate=candidate, symbols=symbols)
    result = asyncio.run(WorkflowExecutor(
        ToolRegistry(),
        runner,
        approved_actions={action_fingerprint(resolved)},
    ).execute([resolved]))
    assert calls == 0
    assert result.tasks[0].status == "blocked"
    assert "at most 50" in result.tasks[0].errors[0]


def test_trade_requests_are_stopped_at_the_fixed_state_machine() -> None:
    candidate = _task(StandardTaskKind.TRADE_EXECUTION)
    calls = 0

    async def runner(_call, _arguments):
        nonlocal calls
        calls += 1
        return {"success": True, "errors": [], "partial": False}

    result = asyncio.run(WorkflowExecutor(ToolRegistry(), runner).execute([
        ResolvedTask(candidate=candidate),
    ]))
    assert calls == 0
    assert result.tasks[0].status == "blocked"
    assert "参数校验 → 账户检查 → 风控检查 → 用户确认 → 下单 → 订单状态" in result.tasks[0].errors[0]


def test_workflow_registry_cannot_be_mutated_at_runtime() -> None:
    with pytest.raises(TypeError):
        WORKFLOW_REGISTRY[StandardTaskKind.GENERAL_RESPONSE] = WORKFLOW_REGISTRY[
            StandardTaskKind.SECURITY_LOOKUP
        ]


def test_executor_reuses_identical_calls_across_independent_tasks() -> None:
    calls: list[tuple[str, dict]] = []

    async def runner(call, arguments):
        calls.append((call.tool_name, arguments))
        await asyncio.sleep(0)
        return {"success": True, "errors": [], "partial": False, "items": []}

    first = ResolvedTask(
        candidate=_task(StandardTaskKind.REALTIME_QUOTE, task_id="quote_a"),
        symbols=("600519",),
    )
    second = ResolvedTask(
        candidate=_task(StandardTaskKind.REALTIME_QUOTE, task_id="quote_b"),
        symbols=("600519",),
    )
    result = asyncio.run(WorkflowExecutor(ToolRegistry(), runner).execute([first, second]))
    assert result.success is True
    assert calls == [("get_realtime_quotes", {"symbols": "600519"})]
    assert sum(call.reused for task in result.tasks for call in task.calls) == 1


def test_executor_enforces_the_plan_wide_call_budget() -> None:
    calls = 0

    async def runner(_call, _arguments):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"success": True, "errors": [], "partial": False, "items": []}

    tasks = [
        ResolvedTask(
            candidate=_task(
                StandardTaskKind.ANNOUNCEMENT_ANALYSIS,
                task_id=f"task_{index}",
            ),
            symbols=tuple(f"{index * 8 + offset:06d}" for offset in range(8)),
        )
        for index in range(9)
    ]
    result = asyncio.run(WorkflowExecutor(
        ToolRegistry(),
        runner,
        max_plan_tool_calls=64,
    ).execute(tasks))
    assert calls == 64
    blocked_by_budget = [
        call
        for task in result.tasks
        for call in task.calls
        if "调用预算已用完" in " ".join(call.result.get("errors") or [])
    ]
    assert len(blocked_by_budget) == 8
    assert result.success is False


def test_executor_stops_dependent_task_after_failure() -> None:
    async def runner(call, arguments):
        return {"success": False, "errors": ["upstream failed"], "partial": False}

    first = ResolvedTask(
        candidate=_task(StandardTaskKind.REALTIME_QUOTE, task_id="quote"),
        symbols=("600519",),
    )
    second = ResolvedTask(
        candidate=_task(
            StandardTaskKind.TECHNICAL_ANALYSIS,
            task_id="technical",
            depends_on=["quote"],
        ),
        symbols=("600519",),
    )
    result = asyncio.run(WorkflowExecutor(ToolRegistry(), runner).execute([first, second]))
    assert result.tasks[0].status == "failed"
    assert result.tasks[1].status == "skipped"
    assert result.tasks[1].calls == []


def test_executor_binds_declared_collection_resources_without_task_type_rules() -> None:
    source = _task(
        StandardTaskKind.SECURITY_LOOKUP,
        task_id="lookup",
        parameters={"query": "白酒"},
    )
    consumer = _task(
        StandardTaskKind.REALTIME_QUOTE,
        task_id="quotes",
        depends_on=["lookup"],
    )
    plan = TaskPlan(tasks=[source, consumer])
    validate_candidate_plan(plan)
    seen: list[tuple[str, dict]] = []

    async def runner(call, arguments):
        seen.append((call.tool_name, arguments))
        if call.tool_name == "search_stocks":
            return {
                "success": True,
                "items": [
                    {"symbol": "600519", "name": "贵州茅台"},
                    {"symbol": "000858", "name": "五粮液"},
                ],
            }
        return {"success": True, "items": []}

    result = asyncio.run(WorkflowExecutor(ToolRegistry(), runner).execute([
        ResolvedTask(candidate=source),
        ResolvedTask(candidate=consumer),
    ]))

    assert result.success is True
    assert seen == [
        ("search_stocks", {"query": "白酒"}),
        ("get_realtime_quotes", {"symbols": "600519,000858"}),
    ]
    assert [entity.symbol for entity in result.tasks[1].output_entities] == [
        "600519",
        "000858",
    ]


def test_executor_discovers_board_candidates_before_business_evidence() -> None:
    industry = _task(
        StandardTaskKind.INDUSTRY_RESEARCH,
        task_id="industry",
        parameters={
            "query": "人形机器人哪些领域最受益",
            "domains": [_domain("人形机器人")],
        },
    )
    domain_candidates = _task(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        task_id="domain_candidates",
        parameters={},
        depends_on=["industry"],
    )
    companies = _task(
        StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        task_id="companies",
        parameters={
            "candidate_scope": "candidate_collection",
            "query": "哪些公司正在大力发展",
        },
        depends_on=["domain_candidates"],
    )
    plan = TaskPlan(tasks=[industry, domain_candidates, companies])
    validate_candidate_plan(plan)
    tool_names: list[str] = []
    processor_tasks: list[tuple[str, list[dict], tuple[str, ...]]] = []

    async def runner(call, _arguments):
        tool_names.append(call.tool_name)
        if call.tool_name == "get_domain_stock_candidates":
            return {
                "success": True,
                "errors": [],
                "partial": False,
                "items": [
                    {"symbol": "002979", "name": "雷赛智能"},
                    {"symbol": "300007", "name": "汉威科技"},
                ],
            }
        return {"success": True, "errors": [], "partial": False, "items": []}

    async def processor(name, task, _evidence):
        processor_tasks.append((
            name,
            list(task.parameters.get("domains") or []),
            task.symbols,
        ))
        if name == "ranked_domain_selection":
            return {
                "success": True,
                "errors": [],
                "semantic_artifacts": [{
                    "type": "ranked_domains",
                    "groups": [{
                        "tier": 1,
                        "domains": [
                            {"label": "灵巧手", "tier": 1},
                            {"label": "六维力传感器", "tier": 1},
                        ],
                    }],
                }],
                "resource_outputs": {
                    "domain_collection": [
                        _domain("灵巧手", "机器人执行器"),
                        _domain("六维力传感器", "传感器"),
                    ],
                },
            }
        return {
            "success": True,
            "errors": [],
            "semantic_artifacts": [],
            "resource_outputs": {
                "security_collection": [
                    {"symbol": "002979", "name": "雷赛智能"},
                    {"symbol": "300007", "name": "汉威科技"},
                ],
            },
        }

    result = asyncio.run(WorkflowExecutor(
        ToolRegistry(),
        runner,
        processor_runner=processor,
    ).execute([
        ResolvedTask(candidate=industry),
        ResolvedTask(candidate=domain_candidates),
        ResolvedTask(
            candidate=companies,
            # Simulates the incidental name resolution that turns the phrase
            # “人形机器人” into the listed company 300024. The explicit
            # dependency collection must remain authoritative.
            symbols=("300024",),
            entity_names=(("300024", "机器人"),),
        ),
    ]))

    assert result.success is True
    assert tool_names.index("get_domain_stock_candidates") < tool_names.index(
        "get_company_theme_evidence"
    )
    assert tool_names.count("get_domain_board_catalog") == 1
    assert tool_names.count("get_domain_stock_candidates") == 1
    assert tool_names.count("get_company_theme_evidence") == 2
    assert tool_names.count("search_financial_news") == 0
    assert tool_names.count("search_research_library") == 0
    assert processor_tasks[1] == (
        "company_evidence_binding",
        [
            _domain("灵巧手", "机器人执行器"),
            _domain("六维力传感器", "传感器"),
        ],
        ("002979", "300007"),
    )
    assert result.tasks[1].resource_outputs["domain_collection"] == [
        _domain("灵巧手", "机器人执行器"),
        _domain("六维力传感器", "传感器"),
    ]
    assert [entity.symbol for entity in result.tasks[2].output_entities] == [
        "002979",
        "300007",
    ]


def test_failed_domain_collection_v2_blocks_every_downstream_data_tool() -> None:
    industry = _task(
        StandardTaskKind.INDUSTRY_RESEARCH,
        task_id="industry",
        parameters={
            "query": "人形机器人哪些领域最受益",
            "domains": [_domain("人形机器人")],
        },
    )
    domain_candidates = _task(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        task_id="domain_candidates",
        parameters={},
        depends_on=["industry"],
    )
    companies = _task(
        StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        task_id="companies",
        parameters={"candidate_scope": "candidate_collection"},
        depends_on=["domain_candidates"],
    )
    tool_names: list[str] = []

    async def runner(call, _arguments):
        tool_names.append(call.tool_name)
        return {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "boards": [{"sector_code": "BK0566", "name": "减速器"}],
        }

    async def processor(name, _task, _evidence):
        assert name == "ranked_domain_selection"
        return {
            "success": False,
            "partial": False,
            "error_code": "planner_schema_invalid",
            "errors": ["板块 ID 绑定未通过精确 Schema"],
            "items": [],
            "semantic_artifacts": [],
            "resource_outputs": {},
            "coverage": {
                "catalog_total": 1,
                "catalog_supplied": 1,
                "selected_count": 0,
                "binding_complete": False,
            },
        }

    result = asyncio.run(WorkflowExecutor(
        ToolRegistry(),
        runner,
        processor_runner=processor,
    ).execute([
        ResolvedTask(candidate=industry),
        ResolvedTask(candidate=domain_candidates),
        ResolvedTask(candidate=companies),
    ]))

    assert tool_names == ["get_domain_board_catalog"]
    assert [task.status for task in result.tasks] == [
        "failed",
        "skipped",
        "blocked",
    ]
    assert result.tasks[0].resource_outputs == {}


def test_business_evidence_preserves_one_terminal_result_per_company() -> None:
    companies = _task(
        StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        task_id="companies",
        parameters={
            "domains": [{"label": "减速器"}],
            "candidate_scope": "candidate_collection",
            "query": "核验业务进展",
        },
    )

    async def runner(_call, arguments):
        return {
            "success": True,
            "errors": ["个股研报源超时"],
            "partial": True,
            "symbol": arguments["symbol"],
            "evidence_documents": [],
        }

    async def processor(_name, _task, _evidence):
        return {
            "success": True,
            "partial": True,
            "errors": [],
            "resource_outputs": {
                "security_collection": [
                    {"symbol": "301368", "name": "丰立智能"},
                ],
            },
        }

    result = asyncio.run(WorkflowExecutor(
        ToolRegistry(),
        runner,
        processor_runner=processor,
    ).execute([
        ResolvedTask(candidate=companies, symbols=("301368",)),
    ]))

    assert result.success is True
    assert result.tasks[0].status == "completed"
    assert len(result.tasks[0].calls) == 1
    assert result.tasks[0].calls[0].arguments["symbol"] == "301368"
    assert result.tasks[0].calls[0].success is True
    assert [entity.symbol for entity in result.final_entities] == ["301368"]


def test_task_plan_rejects_dependency_cycles_before_execution() -> None:
    first = _task(
        StandardTaskKind.REALTIME_QUOTE,
        task_id="first",
        depends_on=["second"],
    )
    second = _task(
        StandardTaskKind.TECHNICAL_ANALYSIS,
        task_id="second",
        depends_on=["first"],
    )
    with pytest.raises(ValueError, match="cycle"):
        TaskPlan(tasks=[first, second])


def test_executor_parallelizes_independent_reads_and_serializes_mutations() -> None:
    async def run_pair(tasks: list[ResolvedTask]) -> int:
        active = 0
        max_active = 0

        async def runner(_call, _arguments):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"success": True, "errors": [], "partial": False}

        result = await WorkflowExecutor(
            ToolRegistry(),
            runner,
            approved_actions={
                action_fingerprint(task)
                for task in tasks
                if task.candidate.confirmation == ConfirmationState.EXPLICIT
            },
        ).execute(tasks)
        assert result.success is True
        return max_active

    reads = [
        ResolvedTask(
            candidate=_task(StandardTaskKind.REALTIME_QUOTE, task_id="quote_a"),
            symbols=("600519",),
        ),
        ResolvedTask(
            candidate=_task(StandardTaskKind.REALTIME_QUOTE, task_id="quote_b"),
            symbols=("000858",),
        ),
    ]
    mutations = [
        ResolvedTask(
            candidate=_task(
                StandardTaskKind.WATCHLIST_MUTATION,
                task_id="watch_a",
                parameters={"action": "add"},
                confirmation=ConfirmationState.EXPLICIT,
            ),
            symbols=("600519",),
        ),
        ResolvedTask(
            candidate=_task(
                StandardTaskKind.WATCHLIST_MUTATION,
                task_id="watch_b",
                parameters={"action": "add"},
                confirmation=ConfirmationState.EXPLICIT,
            ),
            symbols=("000858",),
        ),
    ]
    assert asyncio.run(run_pair(reads)) == 2
    assert asyncio.run(run_pair(mutations)) == 1


def test_production_pipeline_uses_only_fixed_domain_workflow_for_tier_followup() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    plan = TaskPlan(tasks=[_task(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        parameters={
            "domains": [
                _domain("行星滚柱丝杠", "机器人执行器"),
                _domain("减速器"),
                _domain("无框力矩电机", "机器人执行器"),
            ],
        },
    )])
    resolved = [ResolvedTask(candidate=plan.tasks[0])]
    result = {
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [],
        "requested_domains": ["行星滚柱丝杠", "减速器", "无框力矩电机"],
        "local_universe_count": 5879,
        "candidate_count": 2,
        "source_scope": "structured_concept_constituents_intersected_with_local_stock_meta",
        "domain_results": [
            {
                "domain": "行星滚柱丝杠",
                "lookup_themes": ["机器人执行器"],
                "mapping_type": "catalog_binding",
                "mapping_basis": "live_catalog_binding",
                "success": True,
                "coverage_complete": True,
                "candidate_count": 1,
                "matched_boards": [{"name": "机器人执行器"}],
                "items": [{"symbol": "300580", "name": "贝斯特", "boards": ["机器人执行器"]}],
            },
            {
                "domain": "减速器",
                "lookup_themes": ["减速器"],
                "mapping_type": "catalog_binding",
                "mapping_basis": "live_catalog_binding",
                "success": True,
                "coverage_complete": True,
                "candidate_count": 1,
                "matched_boards": [{"name": "减速器"}],
                "items": [{"symbol": "688017", "name": "绿的谐波", "boards": ["减速器"]}],
            },
            {
                "domain": "无框力矩电机",
                "lookup_themes": ["机器人执行器"],
                "mapping_type": "catalog_binding",
                "mapping_basis": "live_catalog_binding",
                "success": True,
                "coverage_complete": True,
                "candidate_count": 1,
                "matched_boards": [{"name": "机器人执行器"}],
                "items": [{"symbol": "300580", "name": "贝斯特", "boards": ["机器人执行器"]}],
            },
        ],
    }
    controller = _Controller()

    async def run() -> str:
        with _unified_pipeline(chat_mod, plan, resolved), \
             patch.object(chat_mod, "execute_tool_isolated", return_value=result), \
             patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value), \
             patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value), \
             patch.object(chat_mod, "_flush_substreams", new=AsyncMock()):
            return await chat_mod._run_standard_task_pipeline(
                controller,
                [{"role": "user", "content": "按上面第一梯队找A股公司"}],
                {"model": "test", "api_base": "", "api_key": None, "extra_headers": None},
            )

    answer = asyncio.run(run())
    assert controller.tool_calls == ["get_domain_stock_candidates"]
    assert "贝斯特 (300580)" in answer
    assert "绿的谐波 (688017)" in answer
    assert "网络" not in " ".join(controller.tool_calls)


def test_production_pipeline_reports_binding_unavailable_without_public_fallback() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    controller = _Controller()

    async def run() -> str:
        with patch.object(
            chat_mod,
            "plan_intent_graph_v2",
            new=AsyncMock(side_effect=(
                OrchestratorV2Error(
                    AgentErrorCode.RESOURCE_UNAVAILABLE,
                    "实时板块语义绑定暂不可用",
                )
            )),
        ):
            return await chat_mod._run_standard_task_pipeline(
                controller,
                [{"role": "user", "content": "找这个方向的股票"}],
                {
                    "model": "test",
                    "api_base": "",
                    "api_key": None,
                    "extra_headers": None,
                },
            )

    answer = asyncio.run(run())

    assert controller.tool_calls == []
    assert "实时板块语义绑定暂不可用" in answer
    assert "没有改用新闻或公网来源兜底" in answer


def test_production_collection_filter_retries_a_transient_failed_batch() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    symbols = tuple(f"{index:06d}" for index in range(47))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions({
            "metric": "debt_ratio",
            "period_basis": "latest_report",
            "operator": "gt",
            "threshold": 70,
            "threshold_unit": "percent",
            "action": "exclude_matching",
        }),
    )
    plan = TaskPlan(tasks=[candidate])
    resolved = [ResolvedTask(candidate=candidate, symbols=symbols)]
    controller = _Controller()
    attempts: dict[str, int] = {}

    def execute(name: str, arguments: dict) -> dict:
        assert name == "get_multi_stock_financials"
        batch = arguments["symbols"]
        attempts[batch] = attempts.get(batch, 0) + 1
        if batch.startswith("000024") and attempts[batch] == 1:
            raise ConnectionError("temporary local connection error")
        batch_symbols = batch.split(",")
        return {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "items": [
                {
                    "symbol": symbol,
                    "name": f"公司{symbol}",
                    "metric": "debt_ratio",
                    "period_basis": "latest_report",
                    "financial_value": 80.0 if int(symbol) % 2 else 50.0,
                    "value_unit": "percent",
                    "debt_ratio_pct": 80.0 if int(symbol) % 2 else 50.0,
                    "report_date": "2026-03-31",
                }
                for symbol in batch_symbols
            ],
            "source": "stock_meta 本地已同步最新报告期财务快照",
            "data_time": "2026-07-21T10:00:00",
        }

    async def run() -> str:
        with _unified_pipeline(chat_mod, plan, resolved), \
             patch.object(chat_mod._registry, "execute", side_effect=execute), \
             patch.object(chat_mod, "execute_tool_isolated", side_effect=AssertionError("local finance must not be isolated")), \
             patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value), \
             patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value), \
             patch.object(
                 chat_mod.asyncio,
                 "wait_for",
                 side_effect=AssertionError("Agent execution must not install a deadline"),
             ), \
             patch.object(chat_mod, "_flush_substreams", new=AsyncMock()):
            return await chat_mod._run_standard_task_pipeline(
                controller,
                [{"role": "user", "content": "把上面负债率高于70%的筛掉"}],
                {"model": "test", "api_base": "", "api_key": None, "extra_headers": None},
            )

    answer = asyncio.run(run())
    assert len(controller.tool_calls) == 2
    assert sum(attempts.values()) == 3
    assert max(attempts.values()) == 2
    assert "本轮筛选未完成" not in answer


def test_original_three_condition_request_executes_all_35_candidates() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    symbols = tuple(f"{index:06d}" for index in range(1, 36))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions(
            {
                "metric": "net_profit",
                "period_basis": "fiscal_year",
                "fiscal_year": 2025,
                "operator": "lt",
                "threshold": 0,
                "threshold_unit": "cny",
                "action": "exclude_matching",
            },
            {
                "metric": "revenue",
                "period_basis": "fiscal_year",
                "fiscal_year": 2025,
                "operator": "lt",
                "threshold": 500_000_000,
                "threshold_unit": "cny",
                "action": "exclude_matching",
            },
            {
                "metric": "debt_ratio",
                "period_basis": "latest_report",
                "operator": "gt",
                "threshold": 70,
                "threshold_unit": "percent",
                "action": "exclude_matching",
            },
        ),
    )
    plan = TaskPlan(tasks=[candidate])
    resolved = [ResolvedTask(candidate=candidate, symbols=symbols)]
    controller = _Controller()
    seen: list[dict] = []

    def execute(name: str, arguments: dict) -> dict:
        assert name == "get_multi_stock_financials"
        seen.append(arguments)
        batch_symbols = arguments["symbols"].split(",")
        metric = arguments["metric"]
        values = {
            "net_profit": lambda number: -1.0 if number % 5 == 0 else 1.0,
            "revenue": lambda number: (
                400_000_000.0 if number % 7 == 0 else 600_000_000.0
            ),
            "debt_ratio": lambda number: 80.0 if number % 11 == 0 else 50.0,
        }
        return {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "requested_count": len(batch_symbols),
            "covered_count": len(batch_symbols),
            "items": [{
                "symbol": symbol,
                "name": f"公司{symbol}",
                "metric": metric,
                "period_basis": arguments["period_basis"],
                "fiscal_year": arguments.get("fiscal_year"),
                "financial_value": values[metric](int(symbol)),
                "value_unit": "percent" if metric == "debt_ratio" else "cny",
                "report_date": (
                    "2026-03-31"
                    if metric == "debt_ratio"
                    else "2025-12-31"
                ),
            } for symbol in batch_symbols],
            "source": "typed-test-source",
            "data_time": "2026-07-28T10:00:00",
        }

    async def run() -> str:
        with _unified_pipeline(chat_mod, plan, resolved), \
             patch.object(chat_mod._registry, "execute", side_effect=execute), \
             patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value), \
             patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value), \
             patch.object(chat_mod, "_flush_substreams", new=AsyncMock()):
            return await chat_mod._run_standard_task_pipeline(
                controller,
                [{
                    "role": "user",
                    "content": (
                        "剔除其中归母净利润为负，去年营收低于5亿，"
                        "负债率高于70%的股票"
                    ),
                }],
                {
                    "model": "test",
                    "api_base": "",
                    "api_key": None,
                    "extra_headers": None,
                },
            )

    answer = asyncio.run(run())
    assert len(seen) == 6
    for metric in ("net_profit", "revenue", "debt_ratio"):
        covered = [
            symbol
            for call in seen
            if call["metric"] == metric
            for symbol in call["symbols"].split(",")
        ]
        assert len(covered) == len(symbols)
        assert set(covered) == set(symbols)
    assert "全部条件均完整覆盖 **35 只**" in answer
    assert "本轮筛选未完成" not in answer


def test_production_previous_year_revenue_follow_up_runs_every_batch() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    symbols = tuple(f"{index:06d}" for index in range(47))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions({
            "metric": "revenue",
            "period_basis": "previous_fiscal_year",
            "operator": "lt",
            "threshold": 5,
            "threshold_unit": "yi_cny",
            "action": "exclude_matching",
        }),
    )
    plan = TaskPlan(tasks=[candidate])
    resolved = [ResolvedTask(candidate=candidate, symbols=symbols)]
    controller = _Controller()
    seen: list[dict] = []

    def execute(name: str, arguments: dict) -> dict:
        assert name == "get_multi_stock_financials"
        seen.append(arguments)
        batch_symbols = arguments["symbols"].split(",")
        return {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "items": [
                {
                    "symbol": symbol,
                    "name": f"公司{symbol}",
                    "metric": "revenue",
                    "period_basis": "previous_fiscal_year",
                    "financial_value": 400_000_000.0 if int(symbol) % 2 else 800_000_000.0,
                    "value_unit": "cny",
                    "report_date": "2025-12-31",
                }
                for symbol in batch_symbols
            ],
            "source": "内部财务数据源 2025-12-31 年度快照",
            "data_time": "2026-07-21T13:00:00",
        }

    async def run() -> str:
        with _unified_pipeline(chat_mod, plan, resolved), \
             patch.object(chat_mod._registry, "execute", side_effect=execute), \
             patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value), \
             patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value), \
             patch.object(chat_mod, "_flush_substreams", new=AsyncMock()):
            return await chat_mod._run_standard_task_pipeline(
                controller,
                [{"role": "user", "content": "继续筛掉股票中去年年营业收入低于5亿的股票"}],
                {"model": "test", "api_base": "", "api_key": None, "extra_headers": None},
            )

    answer = asyncio.run(run())
    assert len(controller.tool_calls) == 2
    assert sorted(len(call["symbols"].split(",")) for call in seen) == [23, 24]
    assert all(call["metric"] == "revenue" for call in seen)
    assert all(call["period_basis"] == "previous_fiscal_year" for call in seen)
    assert "2025 年报营业收入" in answer
    assert "低于 5 亿元" in answer
    assert "成功覆盖 **47 只**，缺失 **0 只**" in answer


def test_production_compound_collection_filter_returns_exact_intersection() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    symbols = ("000001", "000002", "000003", "000004")
    filter_task = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        task_id="financial_filter",
        parameters=_financial_conditions(
            {
                "metric": "debt_ratio", "period_basis": "latest_report",
                "operator": "gt", "threshold": 70,
                "threshold_unit": "percent", "action": "exclude_matching",
            },
            {
                "metric": "revenue", "period_basis": "previous_fiscal_year",
                "operator": "lt", "threshold": 5,
                "threshold_unit": "yi_cny", "action": "exclude_matching",
            },
        ),
    )
    plan = TaskPlan(tasks=[filter_task])
    resolved = [
        ResolvedTask(candidate=filter_task, symbols=symbols),
    ]
    controller = _Controller()

    def execute(name: str, arguments: dict) -> dict:
        assert name == "get_multi_stock_financials"
        is_debt = arguments["metric"] == "debt_ratio"
        values = (
            [80.0, 50.0, 80.0, 50.0]
            if is_debt
            else [400_000_000.0, 400_000_000.0, 800_000_000.0, 800_000_000.0]
        )
        return {
            "success": True,
            "items": [
                {
                    "symbol": symbol,
                    "name": f"公司{symbol}",
                    "metric": arguments["metric"],
                    "period_basis": arguments["period_basis"],
                    "financial_value": value,
                    "value_unit": "percent" if is_debt else "cny",
                    "report_date": "2026-03-31" if is_debt else "2025-12-31",
                }
                for symbol, value in zip(symbols, values)
            ],
            "source": "本地已同步财务库",
            "data_time": "2026-07-21T20:00:00",
        }

    async def run() -> str:
        with _unified_pipeline(chat_mod, plan, resolved), \
             patch.object(chat_mod._registry, "execute", side_effect=execute), \
             patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value), \
             patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value), \
             patch.object(chat_mod, "_flush_substreams", new=AsyncMock()):
            return await chat_mod._run_standard_task_pipeline(
                controller,
                [{"role": "user", "content": "上面股票去掉负债率大于70%，去年营收小于5亿的"}],
                {"model": "test", "api_base": "", "api_key": None, "extra_headers": None},
            )

    answer = asyncio.run(run())
    assert controller.tool_calls == ["get_multi_stock_financials"] * 2
    assert "本轮同时执行 **2 项**财务条件" in answer
    assert "合并后筛除 **3 只**，最终保留 **1 只**" in answer
    assert "公司000004 (000004) | 全部条件通过" in answer
    assert "多条件结果由程序按集合交集计算" in answer


def test_production_domain_discovery_feeds_same_turn_financial_filters() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    discovery_task = _task(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        task_id="domain_candidates",
        parameters={
            "domains": [
                _domain("灵巧手", "机器人执行器"),
                _domain("丝杠", "机器人执行器"),
                _domain("减速器"),
            ],
        },
    )
    filter_task = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        task_id="financial_filter",
        parameters=_financial_conditions(
            {
                "metric": "debt_ratio", "period_basis": "latest_report",
                "operator": "gt", "threshold": 70,
                "threshold_unit": "percent", "action": "exclude_matching",
            },
            {
                "metric": "net_profit", "period_basis": "previous_fiscal_year",
                "operator": "lt", "threshold": 0,
                "threshold_unit": "cny", "action": "exclude_matching",
            },
            {
                "metric": "revenue", "period_basis": "fiscal_year",
                "fiscal_year": 2025, "operator": "lt", "threshold": 5,
                "threshold_unit": "yi_cny", "action": "exclude_matching",
            },
        ),
        depends_on=["domain_candidates"],
    )
    plan = TaskPlan(tasks=[discovery_task, filter_task])
    resolved = [
        ResolvedTask(candidate=discovery_task),
        ResolvedTask(candidate=filter_task),
    ]
    controller = _Controller()
    financial_calls: list[dict] = []

    domain_result = {
        "success": True,
        "items": [
            {"symbol": "000001", "name": "甲公司"},
            {"symbol": "000002", "name": "乙公司"},
            {"symbol": "000003", "name": "丙公司"},
        ],
        "domain_results": [{
            "domain": "灵巧手",
            "success": True,
            "items": [
                {"symbol": "000001", "name": "甲公司"},
                {"symbol": "000002", "name": "乙公司"},
            ],
        }],
    }

    def execute(name: str, arguments: dict) -> dict:
        assert name == "get_multi_stock_financials"
        financial_calls.append(arguments)
        metric = arguments["metric"]
        values = {
            "debt_ratio": [80.0, 50.0, 50.0],
            "net_profit": [1_000_000.0, -1_000_000.0, 1_000_000.0],
            "revenue": [800_000_000.0, 800_000_000.0, 400_000_000.0],
        }[metric]
        return {
            "success": True,
            "items": [
                {
                    "symbol": symbol,
                    "name": name,
                    "metric": metric,
                    "period_basis": arguments["period_basis"],
                    "financial_value": value,
                    "value_unit": "percent" if metric == "debt_ratio" else "cny",
                    "report_date": "2026-03-31" if metric == "debt_ratio" else "2025-12-31",
                }
                for symbol, name, value in zip(
                    ["000001", "000002", "000003"],
                    ["甲公司", "乙公司", "丙公司"],
                    values,
                )
            ],
            "source": "本地已同步财务库",
            "data_time": "2026-07-25T10:00:00",
        }

    async def run() -> str:
        with _unified_pipeline(chat_mod, plan, resolved), \
             patch.object(chat_mod._registry, "execute", side_effect=execute), \
             patch.object(chat_mod, "execute_tool_isolated", return_value=domain_result), \
             patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value), \
             patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value), \
             patch.object(chat_mod, "_flush_substreams", new=AsyncMock()):
            return await chat_mod._run_standard_task_pipeline(
                controller,
                [{"role": "user", "content": "找第一梯队并剔除负债率大于70%、净利润为负、2025年度营收低于5亿的股票"}],
                {"model": "test", "api_base": "", "api_key": None, "extra_headers": None},
            )

    answer = asyncio.run(run())
    assert controller.tool_calls == [
        "get_domain_stock_candidates",
        "get_multi_stock_financials",
        "get_multi_stock_financials",
        "get_multi_stock_financials",
    ]
    assert {call["metric"] for call in financial_calls} == {
        "debt_ratio", "net_profit", "revenue",
    }
    assert all(call["symbols"] == "000001,000002,000003" for call in financial_calls)
    assert "本轮同时执行 **3 项**财务条件" in answer
    assert "合并后筛除 **3 只**，最终保留 **0 只**" in answer


def test_standard_task_answer_validator_rejects_unsupported_codes_and_ratios() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    evidence = [{
        "tool": "search_research_library",
        "result": {
            "success": True,
            "items": [{"title": "人形机器人产业研究", "summary": "核心零部件仍需跟踪"}],
        },
    }]
    issues = chat_mod._standard_task_answer_issues(
        "上游价值量约60-70%，代表公司绿的谐波（688017）。",
        evidence,
    )
    assert any("60-70%" in issue for issue in issues)
    assert any("688017" in issue for issue in issues)
