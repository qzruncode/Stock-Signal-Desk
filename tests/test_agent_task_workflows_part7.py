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
    PlanExecutionResult,
    TaskExecutionResult,
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



"""Focused test slice 7; shared fixtures remain local to this slice."""

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
                "metric": "debt_ratio",
                "period_basis": "latest_report",
                "operator": "gt",
                "threshold": 70,
                "threshold_unit": "percent",
                "action": "exclude_matching",
            },
            {
                "metric": "net_profit",
                "period_basis": "previous_fiscal_year",
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
                "threshold": 5,
                "threshold_unit": "yi_cny",
                "action": "exclude_matching",
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
        "domain_results": [
            {
                "domain": "灵巧手",
                "success": True,
                "items": [
                    {"symbol": "000001", "name": "甲公司"},
                    {"symbol": "000002", "name": "乙公司"},
                ],
            }
        ],
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
        with (
            _unified_pipeline(chat_mod, plan, resolved),
            patch.object(chat_mod._registry, "execute", side_effect=execute),
            patch.object(chat_mod, "execute_tool_isolated", return_value=domain_result),
            patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value),
            patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value),
            patch.object(chat_mod, "_flush_substreams", new=AsyncMock()),
        ):
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
        "debt_ratio",
        "net_profit",
        "revenue",
    }
    assert all(call["symbols"] == "000001,000002,000003" for call in financial_calls)
    assert "本轮同时执行 **3 项**财务条件" in answer
    assert "合并后筛除 **3 只**，最终保留 **0 只**" in answer

def test_standard_task_answer_validator_rejects_unsupported_codes_and_ratios() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    evidence = [
        {
            "tool": "search_research_library",
            "result": {
                "success": True,
                "items": [{"title": "人形机器人产业研究", "summary": "核心零部件仍需跟踪"}],
            },
        }
    ]
    issues = chat_mod._standard_task_answer_issues(
        "上游价值量约60-70%，代表公司绿的谐波（688017）。",
        evidence,
    )
    assert any("60-70%" in issue for issue in issues)
    assert any("688017" in issue for issue in issues)

def test_standard_task_answer_validator_accepts_evidence_rounding_and_amount_units() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    evidence = [
        {
            "tool": "get_balance_sheet",
            "result": {
                "success": True,
                "latest": {
                    "debt_ratio": 38.6518773168,
                    "total_assets": 4_588_000_000,
                    "total_liabilities": 4_628_869_011.10,
                },
                "amount_unit": "元",
                "ratio_unit": "%",
            },
        },
        {
            "tool": "get_income_statement",
            "result": {
                "success": True,
                "latest": {
                    "revenue": 886_200_000,
                },
                "amount_unit": "元",
            },
        },
    ]
    issues = chat_mod._standard_task_answer_issues(
        (
            "资产负债率38.65%，总资产45.88亿元，"
            "总负债4,628,869,011.10元，营业收入8.86亿元。"
        ),
        evidence,
    )
    assert issues == []


def test_standard_task_answer_validator_accepts_amount_from_document_text() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    evidence = [
        {
            "tool": "read_text_document",
            "result": {
                "chunks": [
                    {
                        "page": 1,
                        "text": "调整后委托理财总额不超过人民币50亿元。",
                    }
                ]
            },
        }
    ]

    assert chat_mod._standard_task_answer_issues(
        "委托理财额度不超过50亿元（PDF第1页）。",
        evidence,
    ) == []

def test_terminal_block_is_not_hidden_by_completed_upstream_lookup() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    lookup = ResolvedTask(
        candidate=_task(
            StandardTaskKind.SECURITY_LOOKUP,
            task_id="lookup",
        )
    )
    statements = ResolvedTask(
        candidate=_task(
            StandardTaskKind.FINANCIAL_STATEMENT_ANALYSIS,
            task_id="statements",
            depends_on=["lookup"],
        ),
        symbols=("300850",),
    )
    answer = chat_mod._blocked_task_answer(
        PlanExecutionResult(
            tasks=[
                TaskExecutionResult(
                    task=lookup,
                    status="completed",
                ),
                TaskExecutionResult(
                    task=statements,
                    status="blocked",
                    errors=["periods 必须大于等于 2"],
                ),
            ]
        )
    )
    assert answer is not None
    assert "财报分析" in answer
    assert "periods 必须大于等于 2" in answer
    assert "证据缺失" not in answer
