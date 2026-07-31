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



"""Focused test slice 2; shared fixtures remain local to this slice."""

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
    contracts = {item["capability"]: item for item in capability_catalog()}
    assert contracts["theme_stock_discovery"]["output_resources"] == ["domain_collection", "security_collection"]
    assert contracts["collection_financial_filter"]["input_resources"] == ["security_collection"]

def test_previous_fiscal_year_revenue_filter_has_one_typed_contract_for_every_batch() -> None:
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
        parameters=_financial_conditions(
            {
                "metric": "revenue",
                "period_basis": "previous_fiscal_year",
                "operator": "lt",
                "threshold": 5,
                "threshold_unit": "percent",
                "action": "exclude_matching",
            }
        ),
    ).model_copy(update={"entity_scope": EntityScope.PREVIOUS_ANSWER})

    with pytest.raises(ValueError, match="currency metrics require a CNY threshold unit"):
        validate_candidate_plan(TaskPlan(tasks=[candidate]))

def test_collection_financial_filter_accepts_negative_profit_threshold() -> None:
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions(
            {
                "metric": "deducted_net_profit",
                "period_basis": "previous_fiscal_year",
                "operator": "lt",
                "threshold": -1,
                "threshold_unit": "yi_cny",
                "action": "exclude_matching",
            }
        ),
    ).model_copy(update={"entity_scope": EntityScope.PREVIOUS_ANSWER})

    validate_candidate_plan(TaskPlan(tasks=[candidate]))

def test_collection_financial_filter_keeps_net_profit_distinct_from_deducted_profit() -> None:
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters=_financial_conditions(
            {
                "metric": "net_profit",
                "period_basis": "previous_fiscal_year",
                "operator": "lt",
                "threshold": 0,
                "threshold_unit": "cny",
                "action": "exclude_matching",
            }
        ),
    ).model_copy(update={"entity_scope": EntityScope.PREVIOUS_ANSWER})

    validate_candidate_plan(TaskPlan(tasks=[candidate]))
    calls = compile_task(ResolvedTask(candidate=candidate, symbols=("000001",)))

    assert calls[0].arguments["metric"] == "net_profit"

def test_collection_filter_executor_runs_every_batch_even_when_one_fails() -> None:
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

    result = asyncio.run(
        WorkflowExecutor(ToolRegistry(), runner).execute(
            [
                ResolvedTask(candidate=candidate, symbols=symbols),
            ]
        )
    )
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
    intent_model = capability_for(Capability.ANALYSIS_TEMPLATE_MANAGEMENT).intent_model
    with pytest.raises(ValueError, match="update requires"):
        intent_model.model_validate({"action": "update", "template_id": 1})
