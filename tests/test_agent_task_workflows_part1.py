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



"""Focused test slice 1; shared fixtures remain local to this slice."""

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
    dimensions = tuple(
        sorted(
            {
                dimension
                for task in resolved
                for dimension in capability_for(Capability(task.kind.value)).evidence_dimensions
            },
            key=lambda item: item.value,
        )
    )
    goal = GoalContractV2(
        objective="完成测试计划",
        question_type=QuestionType.RESEARCH,
        uncertainty_mode=UncertaintyMode.BOUNDED,
        deliverables=("返回测试计划结果",),
        claims=(
            ClaimRequirementV2(
                claim_id="result",
                question="测试计划是否形成结果",
                required_dimensions=dimensions,
            ),
        ),
    )
    compiled = CompiledIntentGraphV2(
        run_id="test-run",
        plan=plan,
        tasks=tuple(
            CompiledTaskV2(
                task=task,
                capability=Capability(task.kind.value),
                capability_version=capability_for(Capability(task.kind.value)).version,
                intent_schema_version=capability_for(Capability(task.kind.value)).schema_version,
                execution_policy=capability_for(Capability(task.kind.value)).execution_policy,
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
    with (
        patch.object(
            chat_mod,
            "plan_intent_graph_v2",
            new=AsyncMock(return_value=graph),
        ),
        patch.object(
            chat_mod,
            "compile_intent_graph_v2",
            new=AsyncMock(return_value=compiled),
        ),
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
        normalized_parameters.setdefault(
            "evidence_context",
            {
                "target_topics": ["人形机器人"],
                "domain_theses": [
                    {
                        "label": str(domain.get("label") or ""),
                        "rationale": "该板块是人形机器人产业链的直接受益环节",
                    }
                    for domain in normalized_parameters.get("domains") or []
                    if isinstance(domain, dict) and domain.get("label")
                ],
            },
        )
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
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name=function_name,
                                arguments=json.dumps(payload, ensure_ascii=False),
                            )
                        )
                    ],
                    content=None,
                )
            )
        ]
    )
def test_every_registered_tool_belongs_to_at_least_one_fixed_workflow() -> None:
    assert set(ToolRegistry().get_tool_names()) == set(registered_workflow_tools())
    assert all(
        spec.max_tool_calls <= 104
        for kind, spec in WORKFLOW_REGISTRY.items()
        if kind
        not in {
            StandardTaskKind.THEME_BUSINESS_EVIDENCE,
            StandardTaskKind.INVESTMENT_DECISION,
        }
    )
    assert WORKFLOW_REGISTRY[StandardTaskKind.THEME_BUSINESS_EVIDENCE].max_tool_calls == 6000
    assert WORKFLOW_REGISTRY[StandardTaskKind.INVESTMENT_DECISION].max_tool_calls == 302

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
            "query": "白酒行业",
            "topic": "industry",
            "subjects": ["白酒"],
            "days": 7,
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
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER: _financial_conditions(
            {
                "metric": "debt_ratio",
                "period_basis": "latest_report",
                "operator": "gt",
                "threshold": 70,
                "threshold_unit": "percent",
                "action": "exclude_matching",
            }
        ),
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
        symbols = (
            ("600519", "000858")
            if kind == StandardTaskKind.STOCK_COMPARISON
            else (("600519",) if kind in entity_tasks else ())
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
    assert exercised == {kind for kind, spec in WORKFLOW_REGISTRY.items() if spec.enabled}

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

def test_financial_statement_workflow_passes_one_valid_period_count_to_all_tables() -> None:
    candidate = _task(
        StandardTaskKind.FINANCIAL_STATEMENT_ANALYSIS,
        parameters={"periods": 4},
    )
    calls = compile_task(
        ResolvedTask(
            candidate=candidate,
            symbols=("300850",),
        )
    )
    assert [call.tool_name for call in calls] == [
        "get_balance_sheet",
        "get_income_statement",
        "get_cashflow",
    ]
    assert all(call.arguments == {"symbol": "300850", "periods": 4} for call in calls)

def test_industry_research_always_uses_one_project_catalog_snapshot() -> None:
    project_task = _task(
        StandardTaskKind.INDUSTRY_RESEARCH,
        parameters={
            "query": "某新兴产业哪些方向最受益",
            "domains": [_domain("某新兴产业", "实际主题板块")],
        },
    )
    project_calls = compile_task(ResolvedTask(candidate=project_task))
    assert [call.tool_name for call in project_calls] == ["get_domain_board_catalog"]

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

    calls = compile_task(
        ResolvedTask(
            candidate=candidate,
            symbols=("002979", "300007", "301368"),
        )
    )

    assert "get_domain_stock_candidates" not in [call.tool_name for call in calls]
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
    assert all(
        call.arguments["domains"]
        == [
            "灵巧手",
            "六维力传感器",
            "谐波减速器",
        ]
        for call in calls
    )

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

    calls = WorkflowPolicyValidator(ToolRegistry()).preflight_task(ResolvedTask(candidate=candidate, symbols=symbols))

    assert len(calls) == 488
    assert {call.call.tool_name for call in calls} == {"get_company_theme_evidence"}
    assert [call.arguments["symbol"] for call in calls] == list(symbols)
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
