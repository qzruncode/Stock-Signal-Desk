from __future__ import annotations

import asyncio
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
from src.agent.task_planner import (
    previous_assistant_outline,
    resolve_task_plan,
    validate_candidate_plan,
)
import src.agent.task_planner as task_planner_module
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
    WORKFLOW_REGISTRY,
    compile_task,
    planner_task_catalog,
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


def _task(
    kind: StandardTaskKind,
    *,
    task_id: str = "task_a",
    entities: list[str] | None = None,
    parameters: dict | None = None,
    depends_on: list[str] | None = None,
    confirmation: ConfirmationState = ConfirmationState.NOT_REQUIRED,
) -> StandardTask:
    return StandardTask(
        task_id=task_id,
        kind=kind,
        objective=kind.value,
        entity_scope=EntityScope.NONE,
        entities=entities or [],
        parameters=parameters or {},
        depends_on=depends_on or [],
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
    selected = list(boards) or [label]
    return {
        "label": label,
        "board_queries": selected,
        "mapping_type": mapping_type or "catalog_binding",
        "rationale": rationale,
        "unresolved_parts": unresolved_parts or [],
    }


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
        spec.max_tool_calls <= 8
        for kind, spec in WORKFLOW_REGISTRY.items()
        if kind != StandardTaskKind.INVESTMENT_DECISION
    )
    assert WORKFLOW_REGISTRY[
        StandardTaskKind.INVESTMENT_DECISION
    ].max_tool_calls == 1


def test_planner_catalog_does_not_expose_tool_names() -> None:
    catalog_text = json.dumps(planner_task_catalog(), ensure_ascii=False)
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
        StandardTaskKind.TECHNICAL_ANALYSIS: {"count": 60},
        StandardTaskKind.NEWS_ANALYSIS: {
            "query": "白酒行业", "topic": "industry", "subjects": ["白酒"], "days": 7,
        },
        StandardTaskKind.REGULATORY_ANALYSIS: {"keyword": "再融资"},
        StandardTaskKind.MARKET_OVERVIEW: {"include_index": True},
        StandardTaskKind.SECTOR_ANALYSIS: {"type": "industry", "period": "today"},
        StandardTaskKind.MACRO_ANALYSIS: {"indicators": ["PMI"]},
        StandardTaskKind.INDUSTRY_RESEARCH: {
            "query": "人形机器人产业链", "subjects": ["人形机器人"],
        },
        StandardTaskKind.THEME_STOCK_DISCOVERY: {"domains": [_domain("减速器")]},
        StandardTaskKind.THEME_BUSINESS_EVIDENCE: {
            "domains": [_domain("人形机器人")],
        },
        StandardTaskKind.STOCK_SCREENING: {"screen_spec": screen_spec},
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER: {
            "metric": "debt_ratio",
            "period_basis": "latest_report",
            "operator": "gt",
            "threshold": 70,
            "threshold_unit": "percent",
            "action": "exclude_matching",
        },
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
        assert len(calls) <= 8
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


def test_previous_answer_outline_keeps_middle_markdown_scope() -> None:
    long_prefix = "背景信息。" * 1200
    long_suffix = "风险提示。" * 800
    previous = (
        long_prefix
        + "\n## 第一梯队上游核心零部件\n"
        + "| 领域 | 受益逻辑 |\n|---|---|\n"
        + "| 行星滚柱丝杠 | 高价值量 |\n"
        + "| 减速器 | 核心传动 |\n"
        + "| 无框力矩电机 | 关节驱动 |\n"
        + long_suffix
    )
    outline = previous_assistant_outline([
        {"role": "user", "content": "分析产业链"},
        {"role": "assistant", "content": previous},
        {"role": "user", "content": "按上面第一梯队找股票"},
    ])
    assert "行星滚柱丝杠" in outline
    assert "减速器" in outline
    assert "无框力矩电机" in outline


def test_collection_financial_filter_compiles_all_batches_without_truncation() -> None:
    symbols = tuple(f"{index:06d}" for index in range(47))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters={
            "metric": "debt_ratio",
            "period_basis": "latest_report",
            "operator": "gt",
            "threshold": 70,
            "threshold_unit": "percent",
            "action": "exclude_matching",
        },
    )
    calls = compile_task(ResolvedTask(candidate=candidate, symbols=symbols))
    assert len(calls) == 4
    assert [len(call.arguments["symbols"].split(",")) for call in calls] == [12, 12, 12, 11]
    assert all(call.arguments["metric"] == "debt_ratio" for call in calls)
    assert all(call.arguments["period_basis"] == "latest_report" for call in calls)


def test_previous_fiscal_year_revenue_filter_has_one_typed_contract_for_every_batch() -> None:
    symbols = tuple(f"{index:06d}" for index in range(47))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters={
            "metric": "revenue",
            "period_basis": "previous_fiscal_year",
            "operator": "lt",
            "threshold": 5,
            "threshold_unit": "yi_cny",
            "action": "exclude_matching",
        },
    ).model_copy(update={"entity_scope": EntityScope.PREVIOUS_ANSWER})

    validate_candidate_plan(TaskPlan(tasks=[candidate]))
    calls = compile_task(ResolvedTask(candidate=candidate, symbols=symbols))

    assert len(calls) == 4
    assert all(call.arguments["metric"] == "revenue" for call in calls)
    assert all(call.arguments["period_basis"] == "previous_fiscal_year" for call in calls)
    assert all("fiscal_year" not in call.arguments for call in calls)


def test_collection_financial_filter_rejects_metric_unit_mismatch() -> None:
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters={
            "metric": "revenue",
            "period_basis": "previous_fiscal_year",
            "operator": "lt",
            "threshold": 5,
            "threshold_unit": "percent",
            "action": "exclude_matching",
        },
    ).model_copy(update={"entity_scope": EntityScope.PREVIOUS_ANSWER})

    with pytest.raises(ValueError, match="currency metrics require a CNY threshold unit"):
        validate_candidate_plan(TaskPlan(tasks=[candidate]))


def test_collection_financial_filter_accepts_negative_profit_threshold() -> None:
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters={
            "metric": "deducted_net_profit",
            "period_basis": "previous_fiscal_year",
            "operator": "lt",
            "threshold": -1,
            "threshold_unit": "yi_cny",
            "action": "exclude_matching",
        },
    ).model_copy(update={"entity_scope": EntityScope.PREVIOUS_ANSWER})

    validate_candidate_plan(TaskPlan(tasks=[candidate]))


def test_collection_financial_filter_keeps_net_profit_distinct_from_deducted_profit() -> None:
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters={
            "metric": "net_profit",
            "period_basis": "previous_fiscal_year",
            "operator": "lt",
            "threshold": 0,
            "threshold_unit": "cny",
            "action": "exclude_matching",
        },
    ).model_copy(update={"entity_scope": EntityScope.PREVIOUS_ANSWER})

    validate_candidate_plan(TaskPlan(tasks=[candidate]))
    calls = compile_task(ResolvedTask(candidate=candidate, symbols=("000001",)))

    assert calls[0].arguments["metric"] == "net_profit"


def test_collection_filter_executor_runs_every_batch_even_when_one_fails() -> None:
    symbols = tuple(f"{index:06d}" for index in range(47))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters={
            "metric": "debt_ratio",
            "period_basis": "latest_report",
            "operator": "gt",
            "threshold": 70,
            "threshold_unit": "percent",
            "action": "exclude_matching",
        },
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
    assert len(seen) == 4
    assert sorted(len(batch.split(",")) for batch in seen) == [11, 12, 12, 12]
    assert result.tasks[0].status == "failed"
    assert len(result.tasks[0].calls) == 4
    assert max_active == 1


def test_candidate_plan_rejects_parameters_outside_task_contract() -> None:
    plan = TaskPlan(tasks=[_task(
        StandardTaskKind.REALTIME_QUOTE,
        parameters={"query": "联网找股票"},
    )])
    with pytest.raises(ValueError, match="unsupported parameters"):
        validate_candidate_plan(plan)


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
    candidate = _task(kind, parameters=parameters, confirmation=ConfirmationState.MISSING)
    with pytest.raises(ValueError, match=missing_name):
        validate_candidate_plan(TaskPlan(tasks=[candidate]))


def test_template_update_requires_a_real_change() -> None:
    candidate = _task(
        StandardTaskKind.ANALYSIS_TEMPLATE_MANAGEMENT,
        parameters={"action": "update", "template_id": "template-1"},
    )
    with pytest.raises(ValueError, match="at least one"):
        validate_candidate_plan(TaskPlan(tasks=[candidate]))


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

        result = await WorkflowExecutor(ToolRegistry(), runner).execute(tasks)
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


def test_semantic_planner_uses_one_bounded_planning_stage_and_no_data_tools() -> None:
    payload = {
        "tasks": [{
            "task_id": "valuation",
            "kind": "valuation_analysis",
            "objective": "分析贵州茅台估值",
            "entity_scope": "current_message",
            "entities": ["贵州茅台"],
            "parameters": {},
            "depends_on": [],
            "output_requirements": [],
            "confirmation": "not_required",
            "confidence": 0.96,
        }],
        "needs_clarification": False,
        "clarification_question": None,
    }
    completion = AsyncMock(
        return_value=_model_response("submit_standard_task_plan", payload)
    )
    plan = asyncio.run(resolve_task_plan(
        [
            {"role": "user", "content": "旧轮任务：分析机器人产业链"},
            {"role": "assistant", "content": "上一轮已经完成产业链研究。"},
            {"role": "user", "content": "分析贵州茅台估值"},
        ],
        {"model": "test", "api_base": ""},
        completion=completion,
    ))
    assert plan.tasks[0].kind == StandardTaskKind.VALUATION_ANALYSIS
    assert completion.await_count == 1
    plan_kwargs = completion.await_args.kwargs
    assert [tool["function"]["name"] for tool in plan_kwargs["tools"]] == [
        "submit_standard_task_plan"
    ]
    serialized = json.dumps(plan_kwargs["tools"], ensure_ascii=False)
    assert all(name not in serialized for name in ToolRegistry().get_tool_names())
    planner_context = json.loads(plan_kwargs["messages"][1]["content"])
    assert planner_context["current_request"] == "分析贵州茅台估值"
    assert "旧轮任务" not in json.dumps(planner_context, ensure_ascii=False)
    assert "standard_task_contracts" in planner_context
    assert "valuation_analysis" in {
        contract["kind"] for contract in planner_context["standard_task_contracts"]
    }


def test_plan_cache_uses_structured_context_instead_of_rendered_assistant_text() -> None:
    context = task_planner_module.ConversationContext.from_value({
        "version": "1",
        "turns": [{
            "request": "找候选公司",
            "tasks": [],
            "entities": [{"symbol": "000001", "name": "甲公司"}],
        }],
    })
    base = [{"role": "user", "content": "筛掉不符合条件的公司"}]
    first = task_planner_module._cache_key(
        [{"role": "assistant", "content": "渲染版本甲"}, *base],
        {"model": "test", "api_base": "https://model.example"},
        [],
        [{"symbol": "000001", "name": "甲公司"}],
        context,
    )
    second = task_planner_module._cache_key(
        [{"role": "assistant", "content": "完全不同的渲染版本乙"}, *base],
        {"model": "test", "api_base": "https://model.example"},
        [],
        [{"symbol": "000001", "name": "甲公司"}],
        context,
    )

    assert first == second


def test_semantic_planner_resolves_compound_domains_only_from_current_board_catalog() -> None:
    domain_specs = [
        _domain(
            "灵巧手及力控部件",
            "机器人执行器",
            rationale="当前目录没有同名板块，按执行机构语义使用最窄代理板块。",
        ),
        _domain(
            "电机（伺服电机/步进电机）",
            "机器人执行器",
            rationale="关节驱动电机按执行器板块召回。",
        ),
    ]
    plan_payload = {
        "tasks": [{
            "task_id": "domain_candidates",
            "kind": "theme_stock_discovery",
            "objective": "按上面领域找A股公司",
            "entity_scope": "none",
            "entities": [],
            "parameters": {
                "domains": [
                    {"label": "灵巧手及力控部件"},
                    {"label": "电机（伺服电机/步进电机）"},
                ],
            },
            "depends_on": [],
            "output_requirements": [],
            "confirmation": "not_required",
            "confidence": 0.96,
        }],
        "needs_clarification": False,
        "clarification_question": None,
    }
    binding_payload = {
        "bindings": [{"task_id": "domain_candidates", "domains": domain_specs}],
    }
    completion = AsyncMock(side_effect=[
        _model_response("submit_standard_task_plan", plan_payload),
        _model_response("submit_semantic_resource_bindings", binding_payload),
    ])
    with patch(
        "src.services.domain_board_catalog.get_domain_board_catalog",
        return_value={
            "success": True,
            "board_names": ["人形机器人", "机器人执行器", "减速器", "机器人概念"],
            "source": "测试板块目录",
            "data_time": "2026-07-21",
            "errors": [],
        },
    ):
        plan = asyncio.run(resolve_task_plan(
            [
                {
                    "role": "assistant",
                    "content": "人形机器人第一梯队包括灵巧手及力控部件、电机（伺服电机/步进电机）。",
                },
                {"role": "user", "content": "按上面第一梯队找A股公司"},
            ],
            {"model": "test", "api_base": ""},
            completion=completion,
        ))

    assert plan.tasks[0].parameters["domains"] == domain_specs
    planner_context = json.loads(
        completion.await_args_list[0].kwargs["messages"][1]["content"]
    )
    binder_context = json.loads(
        completion.await_args_list[1].kwargs["messages"][1]["content"]
    )
    assert "catalog" not in planner_context
    assert binder_context["catalog"]["board_names"] == [
        "人形机器人", "机器人执行器", "减速器", "机器人概念",
    ]
    assert "get_domain_stock_candidates" not in json.dumps(
        [planner_context, binder_context], ensure_ascii=False
    )


def test_domain_plan_rejects_a_board_name_absent_from_current_catalog() -> None:
    candidate = _task(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        parameters={
            "domains": [_domain("灵巧手", "模型虚构板块")],
        },
    )
    with pytest.raises(ValueError, match="absent from the live concept-board catalog"):
        validate_candidate_plan(
            TaskPlan(tasks=[candidate]),
            concept_board_names={"人形机器人", "机器人执行器"},
        )


def test_catalog_membership_validation_does_not_guess_semantic_affinity() -> None:
    candidate = _task(
        StandardTaskKind.THEME_STOCK_DISCOVERY,
        parameters={
            "domains": [_domain("伺服电机/步进电机", "轮毂电机")],
        },
    )
    validate_candidate_plan(
        TaskPlan(tasks=[candidate]),
        concept_board_names={"人形机器人", "机器人执行器", "轮毂电机"},
    )


def test_semantic_planner_retries_a_transient_provider_failure() -> None:
    payload = {
        "tasks": [{
            "task_id": "answer",
            "kind": "general_response",
            "objective": "解释概念",
            "entity_scope": "none",
            "entities": [],
            "parameters": {},
            "depends_on": [],
            "output_requirements": [],
            "confirmation": "not_required",
            "confidence": 0.9,
        }],
        "needs_clarification": False,
        "clarification_question": None,
    }
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        tool_calls=[SimpleNamespace(function=SimpleNamespace(
            name="submit_standard_task_plan",
            arguments=json.dumps(payload, ensure_ascii=False),
        ))],
        content=None,
    ))])
    completion = AsyncMock(side_effect=[
        RuntimeError("temporary gateway error"),
        response,
    ])
    plan = asyncio.run(resolve_task_plan(
        [{"role": "user", "content": "解释这个概念"}],
        {"model": "test", "api_base": ""},
        completion=completion,
    ))
    assert plan.tasks[0].kind == StandardTaskKind.GENERAL_RESPONSE
    assert completion.await_count == 2


def test_industry_structure_question_never_opens_company_discovery() -> None:
    payload = {
        "tasks": [{
            "task_id": "industry_structure",
            "kind": "industry_research",
            "objective": "分析人形机器人核心受益领域",
            "entity_scope": "none",
            "entities": [],
            "parameters": {
                "query": "人形机器人核心受益领域",
                "subjects": ["人形机器人"],
            },
            "depends_on": [],
            "output_requirements": ["不生成股票名单"],
            "confirmation": "not_required",
            "confidence": 0.96,
        }],
        "needs_clarification": False,
        "clarification_question": None,
    }
    completion = AsyncMock(
        return_value=_model_response("submit_standard_task_plan", payload)
    )
    plan = asyncio.run(resolve_task_plan(
        [{"role": "user", "content": "人形机器人方向哪些领域最核心最受益？"}],
        {"model": "test", "api_base": ""},
        completion=completion,
    ))

    assert completion.await_count == 1
    assert [task.kind for task in plan.tasks] == [StandardTaskKind.INDUSTRY_RESEARCH]
    assert "不生成股票名单" in plan.tasks[0].output_requirements


@pytest.mark.parametrize(
    "user_text",
    [
        "把上述公司中资产负债率超过70%的剔除",
        "从刚才那批标的排除杠杆率高于七成的公司",
        "对前述集合做财务过滤，只留下负债率不超过70%的公司",
    ],
)
def test_financial_filter_phrasings_all_use_the_semantic_planner(user_text: str) -> None:
    payload = {
        "tasks": [{
            "task_id": "filter",
            "kind": "collection_financial_filter",
            "objective": "按资产负债率过滤证券集合",
            "entity_scope": "previous_answer",
            "entities": [],
            "parameters": {
                "metric": "debt_ratio",
                "period_basis": "latest_report",
                "operator": "gt",
                "threshold": 70,
                "threshold_unit": "percent",
                "action": "exclude_matching",
            },
            "depends_on": [],
            "output_requirements": ["覆盖完整集合"],
            "confirmation": "not_required",
            "confidence": 0.95,
        }],
        "needs_clarification": False,
        "clarification_question": None,
    }
    completion = AsyncMock(
        return_value=_model_response("submit_standard_task_plan", payload)
    )
    plan = asyncio.run(resolve_task_plan(
        [{"role": "user", "content": user_text}],
        {"model": "test", "api_base": ""},
        completion=completion,
        previous_answer_entities=[{"symbol": "000001", "name": "甲公司"}],
    ))

    assert completion.await_count == 1
    assert plan.tasks[0].kind == StandardTaskKind.COLLECTION_FINANCIAL_FILTER
    planner_context = json.loads(
        completion.await_args.kwargs["messages"][1]["content"]
    )
    assert planner_context["current_request"] == user_text
    assert "collection_financial_filter" in planner_context["parameter_schemas"]
    assert planner_context["runtime_context"]["previous_fiscal_year"] == date.today().year - 1


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
        with patch.object(chat_mod, "resolve_task_plan", new=AsyncMock(return_value=plan)), \
             patch.object(chat_mod, "resolve_plan_entities", return_value=resolved), \
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


def test_production_collection_filter_retries_one_failed_batch_without_duplicate_cards() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    symbols = tuple(f"{index:06d}" for index in range(47))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters={
            "metric": "debt_ratio",
            "period_basis": "latest_report",
            "operator": "gt",
            "threshold": 70,
            "threshold_unit": "percent",
            "action": "exclude_matching",
        },
    )
    plan = TaskPlan(tasks=[candidate])
    resolved = [ResolvedTask(candidate=candidate, symbols=symbols)]
    controller = _Controller()
    attempts: dict[str, int] = {}

    def execute(name: str, arguments: dict) -> dict:
        assert name == "get_multi_stock_financials"
        batch = arguments["symbols"]
        attempts[batch] = attempts.get(batch, 0) + 1
        if batch.startswith("000012") and attempts[batch] == 1:
            raise RuntimeError("temporary local connection error")
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
        with patch.object(chat_mod, "resolve_task_plan", new=AsyncMock(return_value=plan)), \
             patch.object(chat_mod, "resolve_plan_entities", return_value=resolved), \
             patch.object(chat_mod._registry, "execute", side_effect=execute), \
             patch.object(chat_mod, "execute_tool_isolated", side_effect=AssertionError("local finance must not be isolated")), \
             patch.object(chat_mod, "_compact_tool_result", side_effect=lambda _name, value: value), \
             patch.object(chat_mod, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value), \
             patch.object(chat_mod, "_flush_substreams", new=AsyncMock()):
            return await chat_mod._run_standard_task_pipeline(
                controller,
                [{"role": "user", "content": "把上面负债率高于70%的筛掉"}],
                {"model": "test", "api_base": "", "api_key": None, "extra_headers": None},
            )

    answer = asyncio.run(run())
    assert len(controller.tool_calls) == 4
    assert sum(attempts.values()) == 5
    assert max(attempts.values()) == 2
    assert "原集合 **47 只**，成功覆盖 **47 只**，缺失 **0 只**" in answer
    assert "本轮筛选未完成" not in answer


def test_production_previous_year_revenue_follow_up_runs_every_batch() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    symbols = tuple(f"{index:06d}" for index in range(47))
    candidate = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        parameters={
            "metric": "revenue",
            "period_basis": "previous_fiscal_year",
            "operator": "lt",
            "threshold": 5,
            "threshold_unit": "yi_cny",
            "action": "exclude_matching",
        },
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
        with patch.object(chat_mod, "resolve_task_plan", new=AsyncMock(return_value=plan)), \
             patch.object(chat_mod, "resolve_plan_entities", return_value=resolved), \
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
    assert len(controller.tool_calls) == 4
    assert [len(call["symbols"].split(",")) for call in seen] == [12, 12, 12, 11]
    assert all(call["metric"] == "revenue" for call in seen)
    assert all(call["period_basis"] == "previous_fiscal_year" for call in seen)
    assert "2025 年报营业收入" in answer
    assert "低于 5 亿元" in answer
    assert "成功覆盖 **47 只**，缺失 **0 只**" in answer


def test_production_compound_collection_filter_returns_exact_intersection() -> None:
    from api.v1.endpoints.agent import chat as chat_mod

    symbols = ("000001", "000002", "000003", "000004")
    debt_task = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        task_id="financial_filter_1",
        parameters={
            "metric": "debt_ratio", "period_basis": "latest_report",
            "operator": "gt", "threshold": 70,
            "threshold_unit": "percent", "action": "exclude_matching",
        },
    )
    revenue_task = _task(
        StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        task_id="financial_filter_2",
        parameters={
            "metric": "revenue", "period_basis": "previous_fiscal_year",
            "operator": "lt", "threshold": 5,
            "threshold_unit": "yi_cny", "action": "exclude_matching",
        },
    )
    plan = TaskPlan(tasks=[debt_task, revenue_task])
    resolved = [
        ResolvedTask(candidate=debt_task, symbols=symbols),
        ResolvedTask(candidate=revenue_task, symbols=symbols),
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
        with patch.object(chat_mod, "resolve_task_plan", new=AsyncMock(return_value=plan)), \
             patch.object(chat_mod, "resolve_plan_entities", return_value=resolved), \
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
