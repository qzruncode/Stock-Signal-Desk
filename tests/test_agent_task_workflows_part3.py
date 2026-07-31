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



"""Focused test slice 3; shared fixtures remain local to this slice."""

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

    pending_confirmation = unmarked.model_copy(
        update={
            "confirmation": ConfirmationState.MISSING,
        }
    )
    validate_candidate_plan(TaskPlan(tasks=[pending_confirmation]))
    with pytest.raises(ConfirmationRequired):
        WorkflowPolicyValidator(ToolRegistry()).preflight_task(ResolvedTask(candidate=pending_confirmation))

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
    result = asyncio.run(
        WorkflowExecutor(
            ToolRegistry(),
            runner,
            approved_actions={action_fingerprint(resolved)},
        ).execute([resolved])
    )
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

    result = asyncio.run(
        WorkflowExecutor(ToolRegistry(), runner).execute(
            [
                ResolvedTask(candidate=candidate),
            ]
        )
    )
    assert calls == 0
    assert result.tasks[0].status == "blocked"
    assert "参数校验 → 账户检查 → 风控检查 → 用户确认 → 下单 → 订单状态" in result.tasks[0].errors[0]

def test_workflow_registry_cannot_be_mutated_at_runtime() -> None:
    with pytest.raises(TypeError):
        WORKFLOW_REGISTRY[StandardTaskKind.GENERAL_RESPONSE] = WORKFLOW_REGISTRY[StandardTaskKind.SECURITY_LOOKUP]

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
    result = asyncio.run(
        WorkflowExecutor(
            ToolRegistry(),
            runner,
            max_plan_tool_calls=64,
        ).execute(tasks)
    )
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

    result = asyncio.run(
        WorkflowExecutor(ToolRegistry(), runner).execute(
            [
                ResolvedTask(candidate=source),
                ResolvedTask(candidate=consumer),
            ]
        )
    )

    assert result.success is True
    assert seen == [
        ("search_stocks", {"query": "白酒"}),
        ("get_realtime_quotes", {"symbols": "600519,000858"}),
    ]
    assert [entity.symbol for entity in result.tasks[1].output_entities] == [
        "600519",
        "000858",
    ]
