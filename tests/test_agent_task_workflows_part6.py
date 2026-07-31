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



"""Focused test slice 6; shared fixtures remain local to this slice."""

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
            "revenue": lambda number: (400_000_000.0 if number % 7 == 0 else 600_000_000.0),
            "debt_ratio": lambda number: 80.0 if number % 11 == 0 else 50.0,
        }
        return {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "requested_count": len(batch_symbols),
            "covered_count": len(batch_symbols),
            "items": [
                {
                    "symbol": symbol,
                    "name": f"公司{symbol}",
                    "metric": metric,
                    "period_basis": arguments["period_basis"],
                    "fiscal_year": arguments.get("fiscal_year"),
                    "financial_value": values[metric](int(symbol)),
                    "value_unit": "percent" if metric == "debt_ratio" else "cny",
                    "report_date": ("2026-03-31" if metric == "debt_ratio" else "2025-12-31"),
                }
                for symbol in batch_symbols
            ],
            "source": "typed-test-source",
            "data_time": "2026-07-28T10:00:00",
        }

    async def run() -> str:
        with (
            _unified_pipeline(chat_mod, plan, resolved),
            patch.object(chat_mod._registry, "execute", side_effect=execute),
            patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value),
            patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value),
            patch.object(chat_mod, "_flush_substreams", new=AsyncMock()),
        ):
            return await chat_mod._run_standard_task_pipeline(
                controller,
                [
                    {
                        "role": "user",
                        "content": ("剔除其中归母净利润为负，去年营收低于5亿，" "负债率高于70%的股票"),
                    }
                ],
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
        covered = [symbol for call in seen if call["metric"] == metric for symbol in call["symbols"].split(",")]
        assert len(covered) == len(symbols)
        assert set(covered) == set(symbols)
    assert "全部条件均完整覆盖 **35 只**" in answer
    assert "本轮筛选未完成" not in answer

def test_production_previous_year_revenue_follow_up_runs_every_batch() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    symbols = tuple(f"{index:06d}" for index in range(47))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions(
            {
                "metric": "revenue",
                "period_basis": "previous_fiscal_year",
                "operator": "lt",
                "threshold": 5,
                "threshold_unit": "yi_cny",
                "action": "exclude_matching",
            }
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
        with (
            _unified_pipeline(chat_mod, plan, resolved),
            patch.object(chat_mod._registry, "execute", side_effect=execute),
            patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value),
            patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value),
            patch.object(chat_mod, "_flush_substreams", new=AsyncMock()),
        ):
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
                "metric": "debt_ratio",
                "period_basis": "latest_report",
                "operator": "gt",
                "threshold": 70,
                "threshold_unit": "percent",
                "action": "exclude_matching",
            },
            {
                "metric": "revenue",
                "period_basis": "previous_fiscal_year",
                "operator": "lt",
                "threshold": 5,
                "threshold_unit": "yi_cny",
                "action": "exclude_matching",
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
        values = [80.0, 50.0, 80.0, 50.0] if is_debt else [400_000_000.0, 400_000_000.0, 800_000_000.0, 800_000_000.0]
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
        with (
            _unified_pipeline(chat_mod, plan, resolved),
            patch.object(chat_mod._registry, "execute", side_effect=execute),
            patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value),
            patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value),
            patch.object(chat_mod, "_flush_substreams", new=AsyncMock()),
        ):
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
