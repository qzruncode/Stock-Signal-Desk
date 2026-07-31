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



"""Focused test slice 5; shared fixtures remain local to this slice."""

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
                action_fingerprint(task) for task in tasks if task.candidate.confirmation == ConfirmationState.EXPLICIT
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

    plan = TaskPlan(
        tasks=[
            _task(
                StandardTaskKind.THEME_STOCK_DISCOVERY,
                parameters={
                    "domains": [
                        _domain("行星滚柱丝杠", "机器人执行器"),
                        _domain("减速器"),
                        _domain("无框力矩电机", "机器人执行器"),
                    ],
                },
            )
        ]
    )
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
        with (
            _unified_pipeline(chat_mod, plan, resolved),
            patch.object(chat_mod, "execute_tool_isolated", return_value=result),
            patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value),
            patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value),
            patch.object(chat_mod, "_flush_substreams", new=AsyncMock()),
        ):
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
            new=AsyncMock(
                side_effect=(
                    OrchestratorV2Error(
                        AgentErrorCode.RESOURCE_UNAVAILABLE,
                        "实时板块语义绑定暂不可用",
                    )
                )
            ),
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
        parameters=_financial_conditions(
            {
                "metric": "debt_ratio",
                "period_basis": "latest_report",
                "operator": "gt",
                "threshold": 70,
                "threshold_unit": "percent",
                "action": "exclude_matching",
            }
        ),
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
        with (
            _unified_pipeline(chat_mod, plan, resolved),
            patch.object(chat_mod._registry, "execute", side_effect=execute),
            patch.object(
                chat_mod, "execute_tool_isolated", side_effect=AssertionError("local finance must not be isolated")
            ),
            patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value),
            patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value),
            patch.object(
                chat_mod.asyncio,
                "wait_for",
                side_effect=AssertionError("Agent execution must not install a deadline"),
            ),
            patch.object(chat_mod, "_flush_substreams", new=AsyncMock()),
        ):
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
