"""End-to-end contracts for collection-level eight-dimensional decisions."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from api.v1.endpoints.agent.chat import (
    _build_professional_buy_decision_answer,
)
from src.agent.task_executor import WorkflowExecutor
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    compile_task,
    workflow_for,
)
from src.services.buy_criteria.professional_analysis import (
    DIMENSION_DEFINITIONS,
    DimensionAssessment,
)
from src.tools.evaluate_market_mainline_gate import (
    evaluate_market_mainline_gate,
)
from src.tools.evaluate_multi_stock_buy_criteria import (
    build_professional_buy_failure_result,
    evaluate_multi_stock_buy_criteria,
)
from src.tools.prepare_market_mainline_snapshot import (
    prepare_market_mainline_snapshot,
)
from src.tools.base import ToolProgressUpdate, tool_progress_observer
from src.tools.registry import ToolRegistry


EXPECTED_DIMENSIONS = (
    "market_mainline",
    "industrial_competitiveness",
    "industry_cycle",
    "competition_quality",
    "growth_drivers",
    "forward_catalysts",
    "valuation_odds",
    "major_risks",
)


def _task() -> StandardTask:
    return StandardTask(
        task_id="buy_now",
        kind=StandardTaskKind.INVESTMENT_DECISION,
        objective="这些股票中哪些现在能买入",
        entity_scope=EntityScope.PREVIOUS_ANSWER,
        entities=[],
        parameters={"thesis": "用户给出的结构化投资逻辑"},
        depends_on=[],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.99,
    )


def _criterion(index: int, status: str = "pass") -> dict:
    dimension_id, title = DIMENSION_DEFINITIONS[index]
    return {
        "criterion_id": dimension_id,
        "criterion_name": title,
        "index": index,
        "passed": status == "pass",
        "status": status,
        "verdict": f"{title}的证据与反证已经核验。",
        "details": {
            "key_evidence": [f"{title}支持证据"],
            "counter_evidence": [f"{title}主要反证"],
        },
    }


def _item(symbol: str, statuses: list[str]) -> dict:
    criteria = [
        _criterion(index, status)
        for index, status in enumerate(statuses)
    ]
    all_pass = len(criteria) == 8 and all(
        status == "pass" for status in statuses
    )
    stopped = next(
        (item for item in criteria if item["status"] != "pass"),
        None,
    )
    return {
        "symbol": symbol,
        "name": f"公司{symbol}",
        "criteria": criteria,
        "analysis_status": (
            "source_unavailable"
            if "insufficient" in statuses
            else "completed"
        ),
        "final_decision": (
            "可买入"
            if all_pass
            else "分析未完成"
            if "insufficient" in statuses
            else "不可买入"
        ),
        "gate_pass_complete": all_pass,
        "stopped_at": stopped["criterion_id"] if stopped else None,
        "stopped_at_name": stopped["criterion_name"] if stopped else None,
    }


def test_professional_dimension_order_is_exactly_the_requested_eight_axes() -> None:
    assert tuple(item[0] for item in DIMENSION_DEFINITIONS) == EXPECTED_DIMENSIONS


def test_shared_market_mainline_tool_returns_one_typed_gate() -> None:
    assessment = DimensionAssessment(
        dimension_id="market_mainline",
        status="pass",
        evaluated_subjects=["减速器"],
        headline="减速器属于当前市场主线",
        analysis="机构策略、产业供需和技术路线共同支持当前主线判断。",
    )
    progress_updates: list[ToolProgressUpdate] = []
    with (
        patch(
            "src.tools.evaluate_market_mainline_gate.evaluate_shared_market_mainline",
            return_value=(assessment, ""),
        ) as shared,
        tool_progress_observer(progress_updates.append),
    ):
        result = evaluate_market_mainline_gate(
            thesis="减速器",
            thesis_context={
                "summary": "减速器",
                "domains": [{
                    "label": "减速器",
                    "board_queries": ["减速器"],
                    "mapping_type": "catalog_binding",
                }],
            },
            market_mainline_snapshot={
                "snapshot_id": "shared-snapshot",
                "as_of_date": "2026-07-29",
                "current_mainlines": [{"name": "机器人"}],
            },
        )

    shared.assert_called_once()
    assert result["market_mainline_assessment"]["status"] == "pass"
    assert result["market_mainline_snapshot_id"] == "shared-snapshot"
    assert [update.progress for update in progress_updates] == [10, 35, 100]
    assert "共享第一关已完成" in progress_updates[-1].message


def test_investment_workflow_compiles_each_stock_as_an_independent_call() -> None:
    symbols = tuple(f"{index:06d}" for index in range(72))
    calls = compile_task(ResolvedTask(candidate=_task(), symbols=symbols))

    assert len(calls) == len(symbols) + 2
    assert calls[0].tool_name == "prepare_market_mainline_snapshot"
    assert calls[1].tool_name == "evaluate_market_mainline_gate"
    assert all(
        call.tool_name == "evaluate_multi_stock_buy_criteria"
        for call in calls[2:]
    )
    assert [call.arguments["symbols"] for call in calls[2:]] == list(symbols)
    assert calls[1].arguments["mainline_strategy"] == "confirmed_mainline"
    assert all(
        call.arguments["mainline_strategy"] == "confirmed_mainline"
        for call in calls[2:]
    )
    assert calls[1].depends_on_steps == ("market_mainline_snapshot",)
    assert calls[1].result_bindings == (
        ("market_mainline_snapshot", "market_mainline_snapshot"),
    )
    assert all(
        call.depends_on_steps == (
            "market_mainline_snapshot",
            "market_mainline_gate",
        )
        and call.after_steps == ()
        and call.result_bindings == (
            ("market_mainline_snapshot", "market_mainline_snapshot"),
            ("market_mainline_assessment", "market_mainline_gate"),
            ("market_mainline_model_error", "market_mainline_gate"),
        )
        and call.execution_guard is not None
        and call.execution_guard.source_step == "market_mainline_gate"
        and call.execution_guard.result_path == (
            "market_mainline_assessment",
            "status",
        )
        and call.execution_guard.allowed_values == frozenset({"pass"})
        for call in calls[2:]
    )
    spec = workflow_for(StandardTaskKind.INVESTMENT_DECISION)
    assert spec.max_tool_calls == 302
    assert spec.max_parallel_steps == 4


def test_investment_executor_keeps_bounded_sliding_progress_and_source_order() -> None:
    symbols = tuple(f"{index:06d}" for index in range(1, 71))
    active = 0
    max_active = 0
    observed: list[tuple[str, int, int]] = []
    frozen_snapshot = {
        "success": True,
        "partial": False,
        "available": True,
        "snapshot_id": "snapshot-2026-07-27",
        "as_of_date": "2026-07-27",
        "current_mainlines": [{"name": "测试主线"}],
        "report_pending": False,
        "errors": [],
        "generation_task": None,
        "_tool_payload_meta": {
            "tool_name": "prepare_market_mainline_snapshot",
        },
    }
    received_snapshots: list[dict] = []

    async def runner(_call, arguments):
        nonlocal active, max_active
        if _call.tool_name == "prepare_market_mainline_snapshot":
            return frozen_snapshot
        if _call.tool_name == "evaluate_market_mainline_gate":
            return {
                "success": True,
                "partial": False,
                "market_mainline_assessment": DimensionAssessment(
                    dimension_id="market_mainline",
                    status="pass",
                    evaluated_subjects=["测试主线"],
                    headline="测试主线属于当前主线",
                    analysis="多源中期证据确认测试主线属于当前市场主导叙事。",
                ).model_dump(),
                "market_mainline_model_error": None,
                "errors": [],
                "warnings": [],
            }
        symbol = arguments["symbols"]
        received_snapshots.append(arguments["market_mainline_snapshot"])
        assert arguments["market_mainline_assessment"]["status"] == "pass"
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.04 if symbol == symbols[0] else 0.001)
        active -= 1
        return {
            "success": True,
            "partial": False,
            "items": [_item(symbol, ["fail"])],
            "errors": [],
        }

    async def observe(_task, outcome, completed, total):
        observed.append((
            str(outcome.arguments.get("symbols") or ""),
            completed,
            total,
        ))

    execution = asyncio.run(WorkflowExecutor(
        ToolRegistry(),
        runner,
        outcome_observer=observe,
    ).execute([
        ResolvedTask(candidate=_task(), symbols=symbols),
    ]))

    assert execution.success is True
    assert max_active == 4
    assert len(received_snapshots) == len(symbols)
    assert all(
        snapshot == {
            key: frozen_snapshot[key]
            for key in (
                "success",
                "partial",
                "available",
                "snapshot_id",
                "as_of_date",
                "current_mainlines",
                "report_pending",
                "errors",
            )
        }
        for snapshot in received_snapshots
    )
    stock_observations = [
        item for item in observed if item[0]
    ]
    assert stock_observations[0][0] != symbols[0]
    assert [call.arguments["symbols"] for call in execution.tasks[0].calls[2:]] == list(
        symbols
    )


def test_failed_shared_market_gate_projects_every_stock_without_running_tools() -> None:
    symbols = ("000001", "000002", "000003")
    invoked_tools: list[str] = []
    shared_failure = DimensionAssessment(
        dimension_id="market_mainline",
        status="fail",
        evaluated_subjects=["减速器"],
        headline="减速器不属于本轮确认的市场主线",
        analysis="共享市场主线快照未把减速器列入当前确认主线，因此第一关失败。",
    ).model_dump()

    async def runner(call, _arguments):
        invoked_tools.append(call.tool_name)
        if call.tool_name == "prepare_market_mainline_snapshot":
            return {
                "success": True,
                "partial": False,
                "available": True,
                "snapshot_id": "snapshot-shared-fail",
                "as_of_date": "2026-07-29",
                "current_mainlines": [{"name": "国产算力"}],
                "report_pending": False,
                "errors": [],
                "warnings": [],
            }
        if call.tool_name == "evaluate_market_mainline_gate":
            return {
                "success": True,
                "partial": False,
                "market_mainline_assessment": shared_failure,
                "market_mainline_model_error": None,
                "errors": [],
                "warnings": [],
            }
        raise AssertionError("共享首关失败后不得调用逐股工具")

    def resolve_one(value: str):
        return ([{"symbol": value, "name": f"测试公司{value}"}], [])

    with patch(
        "src.tools.evaluate_multi_stock_buy_criteria.resolve_securities_csv",
        side_effect=resolve_one,
    ):
        execution = asyncio.run(WorkflowExecutor(
            ToolRegistry(),
            runner,
        ).execute([
            ResolvedTask(candidate=_task(), symbols=symbols),
        ]))

    task_result = execution.tasks[0]
    assert execution.success is True
    assert invoked_tools == [
        "prepare_market_mainline_snapshot",
        "evaluate_market_mainline_gate",
    ]
    assert len(task_result.calls) == len(symbols) + 2
    projected = task_result.calls[2:]
    assert all(outcome.executed is False for outcome in projected)
    assert all(outcome.success is True for outcome in projected)
    assert all(
        outcome.result["items"][0]["stopped_at"] == "market_mainline"
        and outcome.result["items"][0]["final_decision"] == "不可买入"
        for outcome in projected
    )


def test_failed_market_snapshot_blocks_every_per_stock_analysis() -> None:
    symbols = ("000001", "000002")
    executed: list[str] = []

    async def runner(call, arguments):
        if call.tool_name == "prepare_market_mainline_snapshot":
            return {
                "success": False,
                "partial": False,
                "errors": ["daily report unavailable"],
            }
        executed.append(arguments["symbols"])
        return {
            "success": True,
            "partial": False,
            "items": [_item(arguments["symbols"], ["fail"])],
            "errors": [],
        }

    execution = asyncio.run(WorkflowExecutor(
        ToolRegistry(),
        runner,
    ).execute([
        ResolvedTask(candidate=_task(), symbols=symbols),
    ]))

    assert executed == []
    assert execution.tasks[0].status == "failed"
    assert len(execution.tasks[0].calls) == 1
    assert execution.tasks[0].calls[0].success is False
    assert execution.tasks[0].errors == [
        "3 个后续步骤因前置步骤失败未执行。"
    ]


def test_market_snapshot_inline_failure_is_not_marked_successful() -> None:
    with patch(
        "src.tools.prepare_market_mainline_snapshot.MarketThemeService.ensure_model_report_inline",
        side_effect=RuntimeError("provider disconnected"),
    ) as ensure_report:
        result = prepare_market_mainline_snapshot()

    assert result["success"] is False
    assert result["partial"] is False
    assert result["generation_pending"] is False
    assert result["data_time"] is None
    assert result["is_stale"] is None
    assert "provider disconnected" in result["errors"][0]
    ensure_report.assert_called_once_with(
        force=False,
        on_progress=ensure_report.call_args.kwargs["on_progress"],
    )


def test_market_snapshot_returns_only_a_real_frozen_report() -> None:
    with patch(
        "src.tools.prepare_market_mainline_snapshot.MarketThemeService.ensure_model_report_inline",
        return_value={
            "contract_version": "market_mainline_lifecycle_strategy_v3",
            "generated_at": "2026-07-27T22:00:00+08:00",
            "as_of_date": "2026-07-27",
            "overview": "测试总览",
            "market_stage": {"label": "测试阶段"},
            "current_mainlines": [{"name": "国产算力"}],
            "future_mainlines": [],
            "llm_used": True,
            "model_used": "test-model",
            "report_pending": False,
            "raw_stream_output": "不应进入批次参数",
            "debug_input": {"large": "不应进入批次参数"},
        },
    ):
        result = prepare_market_mainline_snapshot()

    assert result["success"] is True
    assert result["partial"] is False
    assert result["available"] is True
    assert result["report_pending"] is False
    assert result["is_stale"] is False
    assert result["snapshot_id"]
    assert "raw_stream_output" not in result
    assert "debug_input" not in result


def test_market_snapshot_accepts_candidate_only_lifecycle_report() -> None:
    with patch(
        "src.tools.prepare_market_mainline_snapshot.MarketThemeService.ensure_model_report_inline",
        return_value={
            "contract_version": "market_mainline_lifecycle_strategy_v3",
            "generated_at": "2026-07-29T10:00:00+08:00",
            "as_of_date": "2026-07-29",
            "overview": "当前无确认主线，具身智能进入验证期",
            "current_mainlines": [],
            "candidate_mainlines": [{
                "name": "具身智能",
                "lifecycle": "validating",
                "branches": ["减速器"],
            }],
            "future_mainlines": [{
                "name": "具身智能",
                "lifecycle": "validating",
                "branches": ["减速器"],
            }],
            "report_pending": False,
        },
    ):
        result = prepare_market_mainline_snapshot()

    assert result["success"] is True
    assert result["available"] is True
    assert result["current_mainlines"] == []
    assert result["candidate_mainlines"][0]["branches"] == ["减速器"]


def test_market_snapshot_forwards_inline_progress_to_workflow() -> None:
    updates: list[ToolProgressUpdate] = []

    def ensure_inline(*, force, on_progress):
        assert force is False
        on_progress(24, "正在分析市场主线证据", "先核对市场宽度。")
        return {
            "contract_version": "market_mainline_lifecycle_strategy_v3",
            "generated_at": "2026-07-28T10:00:00+08:00",
            "as_of_date": "2026-07-28",
            "overview": "测试总览",
            "current_mainlines": [{"name": "机器人"}],
            "report_pending": False,
            "llm_used": True,
        }

    with patch(
        "src.tools.prepare_market_mainline_snapshot.MarketThemeService.ensure_model_report_inline",
        side_effect=ensure_inline,
    ), tool_progress_observer(updates.append):
        result = prepare_market_mainline_snapshot()

    assert result["success"] is True
    assert updates == [
        ToolProgressUpdate(
            message="正在分析市场主线证据",
            progress=24,
            reasoning_delta="先核对市场宽度。",
        ),
    ]


def test_buy_tool_forwards_the_frozen_snapshot_to_every_company() -> None:
    resolved = [
        {"symbol": "000001", "name": "公司一"},
        {"symbol": "000002", "name": "公司二"},
    ]
    snapshot = {
        "snapshot_id": "shared-snapshot",
        "report_pending": False,
        "as_of_date": "2026-07-27",
        "current_mainlines": [{"name": "国产算力"}],
    }
    captured: list[dict] = []

    def analyze(_self, symbol: str, **kwargs) -> dict:
        captured.append(kwargs["pre_fetched_data"])
        return _item(symbol, ["fail"])

    with patch(
        "src.tools.evaluate_multi_stock_buy_criteria.resolve_securities_csv",
        return_value=(resolved, []),
    ), patch(
        "src.tools.evaluate_multi_stock_buy_criteria.CriterionOrchestrator.analyze_for_agent",
        autospec=True,
        side_effect=analyze,
    ):
        result = evaluate_multi_stock_buy_criteria(
            "000001,000002",
            market_mainline_snapshot=snapshot,
        )

    assert result["market_mainline_snapshot_id"] == "shared-snapshot"
    assert captured == [
        {"market_mainline_snapshot": snapshot},
        {"market_mainline_snapshot": snapshot},
    ]


def test_failed_company_run_becomes_a_renderable_terminal_result() -> None:
    result = build_professional_buy_failure_result(
        "000026",
        "000026工具执行超时",
        market_mainline_snapshot_id="shared-snapshot",
    )
    answer = _build_professional_buy_decision_answer([{
        "tool": "evaluate_multi_stock_buy_criteria",
        "arguments": {"symbols": "000026"},
        "result": result,
    }])

    assert answer is not None
    assert "000026" in answer
    assert "分析失败" in answer
    assert "数据未返回" not in answer
    assert "请求 1 只，返回 1 只，缺失 0 只" in answer
    assert result["market_mainline_snapshot_id"] == "shared-snapshot"
    assert result["success"] is False


def test_failed_company_run_sanitizes_gateway_and_schema_details() -> None:
    result = build_professional_buy_failure_result(
        "000026",
        (
            "litellm.Timeout: <html><h1>504 Gateway Time-out</h1></html> "
            "ValidationError headline string_too_long"
        ),
    )
    answer = _build_professional_buy_decision_answer([{
        "tool": "evaluate_multi_stock_buy_criteria",
        "arguments": {"symbols": "000026"},
        "result": result,
    }])

    assert result["success"] is False
    assert result["errors"] == [
        "analysis_timeout：分析服务响应超时，本轮未形成公司结论"
    ]
    assert answer is not None
    assert "<html>" not in answer
    assert "ValidationError" not in answer
    assert "analysis_timeout" in answer


def test_renderer_does_not_turn_an_unexecuted_call_into_zero_of_eight() -> None:
    answer = _build_professional_buy_decision_answer([{
        "tool": "evaluate_multi_stock_buy_criteria",
        "arguments": {"symbols": "000026"},
        "executed": False,
        "result": {
            "success": False,
            "errors": ["前置流程阻止了逐股调用"],
            "partial": False,
        },
    }])

    assert answer is not None
    assert "**未执行**" in answer
    assert "前置流程阻止了逐股调用" in answer
    assert "数据未返回" not in answer
    assert "0/8" not in answer


def test_collection_executor_covers_every_resolved_stock_without_silent_cap() -> None:
    resolved = [
        {"symbol": f"{index:06d}", "name": f"公司{index}"}
        for index in range(1, 24)
    ]

    def analyze(_self, symbol: str, **_kwargs) -> dict:
        return _item(symbol, ["pass"] * 8)

    with patch(
        "src.tools.evaluate_multi_stock_buy_criteria.resolve_securities_csv",
        return_value=(resolved, []),
    ), patch(
        "src.tools.evaluate_multi_stock_buy_criteria.CriterionOrchestrator.analyze_for_agent",
        autospec=True,
        side_effect=analyze,
    ):
        result = evaluate_multi_stock_buy_criteria(
            ",".join(item["symbol"] for item in resolved),
        )

    assert result["requested_count"] == 23
    assert result["covered_count"] == 23
    assert result["coverage_complete"] is True
    assert [item["symbol"] for item in result["items"]] == [
        item["symbol"] for item in resolved
    ]


def test_company_failure_is_reported_as_execution_failure_not_a_fake_gate() -> None:
    resolved = [{"symbol": "600519", "name": "贵州茅台"}]
    with patch(
        "src.tools.evaluate_multi_stock_buy_criteria.resolve_securities_csv",
        return_value=(resolved, []),
    ), patch(
        "src.tools.evaluate_multi_stock_buy_criteria.CriterionOrchestrator.analyze_for_agent",
        side_effect=RuntimeError("analysis unavailable"),
    ):
        result = evaluate_multi_stock_buy_criteria("600519")

    item = result["items"][0]
    assert item["analysis_status"] == "execution_failed"
    assert item["final_decision"] == "分析失败"
    assert item["insufficient_count"] == 0
    assert item["not_evaluated_count"] == 8
    assert item["criteria"] == []
    assert item["stopped_at"] is None
    assert item["gate_pass_complete"] is False
    assert result["execution_failed_count"] == 1
    assert result["success"] is False
    assert result["errors"]


def test_critical_source_outage_is_not_reported_as_company_rejection() -> None:
    resolved = [{"symbol": "600519", "name": "贵州茅台"}]
    unavailable = _item("600519", ["pass", "insufficient"])
    unavailable["evidence_gaps"] = [
        "industrial_competitiveness/formal_business_evidence：取证超时"
    ]
    with patch(
        "src.tools.evaluate_multi_stock_buy_criteria.resolve_securities_csv",
        return_value=(resolved, []),
    ), patch(
        "src.tools.evaluate_multi_stock_buy_criteria.CriterionOrchestrator.analyze_for_agent",
        return_value=unavailable,
    ):
        result = evaluate_multi_stock_buy_criteria("600519")

    assert result["success"] is False
    assert result["source_unavailable_count"] == 1
    assert result["evidence_insufficient_count"] == 0
    assert result["items"][0]["final_decision"] == "分析未完成"
    assert result["errors"] == [
        "analysis_timeout：关键来源取证超时，本轮未形成公司结论"
    ]


def test_renderer_distinguishes_screening_rejection_from_system_unavailable() -> None:
    rejected = _item("000001", ["pass", "fail"])
    insufficient = _item("000002", ["pass", "insufficient"])
    answer = _build_professional_buy_decision_answer([{
        "tool": "evaluate_multi_stock_buy_criteria",
        "arguments": {"symbols": "000001,000002"},
        "result": {
            "items": [rejected, insufficient],
            "errors": [],
        },
    }])

    assert answer is not None
    assert (
        "| 公司000001 (000001) | **不符合本次买入条件**"
        in answer
    )
    assert "| 公司000002 (000002) | **分析未完成**" in answer
    assert "不符合本次买入条件 1 只" in answer
    assert "分析未完成 1 只" in answer
    assert "证据不足 1 只" not in answer


def test_renderer_accepts_only_complete_ordered_eight_passes() -> None:
    passed = _item("600519", ["pass"] * 8)
    stopped = _item("000858", ["pass", "fail"])
    answer = _build_professional_buy_decision_answer([{
        "tool": "evaluate_multi_stock_buy_criteria",
        "arguments": {"symbols": "600519,000858"},
        "result": {
            "items": [passed, stopped],
            "errors": [],
        },
    }])

    assert answer is not None
    assert "公司600519 (600519)" in answer
    assert "可买入" in answer
    assert "公司000858 (000858)" in answer
    assert "首个阻断后停止" in answer
    assert "2/8，首个阻断后停止" in answer
    assert "后续 6 维未执行" in answer


def test_renderer_rejects_out_of_order_dimension_payload() -> None:
    malformed = _item("600519", ["pass"] * 8)
    malformed["criteria"][0], malformed["criteria"][1] = (
        malformed["criteria"][1],
        malformed["criteria"][0],
    )
    answer = _build_professional_buy_decision_answer([{
        "tool": "evaluate_multi_stock_buy_criteria",
        "arguments": {"symbols": "600519"},
        "result": {"items": [malformed], "errors": []},
    }])

    assert answer is not None
    assert "执行结构异常" in answer
    assert "| 公司600519 (600519) | **分析失败**" in answer
