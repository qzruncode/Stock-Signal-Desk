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



"""Focused test slice 2; shared fixtures remain local to this slice."""

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
    criteria = [_criterion(index, status) for index, status in enumerate(statuses)]
    all_pass = len(criteria) == 8 and all(status == "pass" for status in statuses)
    stopped = next(
        (item for item in criteria if item["status"] != "pass"),
        None,
    )
    return {
        "symbol": symbol,
        "name": f"公司{symbol}",
        "criteria": criteria,
        "analysis_status": ("source_unavailable" if "insufficient" in statuses else "completed"),
        "final_decision": ("可买入" if all_pass else "分析未完成" if "insufficient" in statuses else "不可买入"),
        "gate_pass_complete": all_pass,
        "stopped_at": stopped["criterion_id"] if stopped else None,
        "stopped_at_name": stopped["criterion_name"] if stopped else None,
    }
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

    with (
        patch(
            "src.tools.evaluate_multi_stock_buy_criteria.resolve_securities_csv",
            return_value=(resolved, []),
        ),
        patch(
            "src.tools.evaluate_multi_stock_buy_criteria.CriterionOrchestrator.analyze_for_agent",
            autospec=True,
            side_effect=analyze,
        ),
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
    answer = _build_professional_buy_decision_answer(
        [
            {
                "tool": "evaluate_multi_stock_buy_criteria",
                "arguments": {"symbols": "000026"},
                "result": result,
            }
        ]
    )

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
        ("litellm.Timeout: <html><h1>504 Gateway Time-out</h1></html> " "ValidationError headline string_too_long"),
    )
    answer = _build_professional_buy_decision_answer(
        [
            {
                "tool": "evaluate_multi_stock_buy_criteria",
                "arguments": {"symbols": "000026"},
                "result": result,
            }
        ]
    )

    assert result["success"] is False
    assert result["errors"] == ["analysis_timeout：分析服务响应超时，本轮未形成公司结论"]
    assert answer is not None
    assert "<html>" not in answer
    assert "ValidationError" not in answer
    assert "analysis_timeout" in answer

def test_renderer_does_not_turn_an_unexecuted_call_into_zero_of_eight() -> None:
    answer = _build_professional_buy_decision_answer(
        [
            {
                "tool": "evaluate_multi_stock_buy_criteria",
                "arguments": {"symbols": "000026"},
                "executed": False,
                "result": {
                    "success": False,
                    "errors": ["前置流程阻止了逐股调用"],
                    "partial": False,
                },
            }
        ]
    )

    assert answer is not None
    assert "**未执行**" in answer
    assert "前置流程阻止了逐股调用" in answer
    assert "数据未返回" not in answer
    assert "0/8" not in answer

def test_collection_executor_covers_every_resolved_stock_without_silent_cap() -> None:
    resolved = [{"symbol": f"{index:06d}", "name": f"公司{index}"} for index in range(1, 24)]

    def analyze(_self, symbol: str, **_kwargs) -> dict:
        return _item(symbol, ["pass"] * 8)

    with (
        patch(
            "src.tools.evaluate_multi_stock_buy_criteria.resolve_securities_csv",
            return_value=(resolved, []),
        ),
        patch(
            "src.tools.evaluate_multi_stock_buy_criteria.CriterionOrchestrator.analyze_for_agent",
            autospec=True,
            side_effect=analyze,
        ),
    ):
        result = evaluate_multi_stock_buy_criteria(
            ",".join(item["symbol"] for item in resolved),
        )

    assert result["requested_count"] == 23
    assert result["covered_count"] == 23
    assert result["coverage_complete"] is True
    assert [item["symbol"] for item in result["items"]] == [item["symbol"] for item in resolved]

def test_company_failure_is_reported_as_execution_failure_not_a_fake_gate() -> None:
    resolved = [{"symbol": "600519", "name": "贵州茅台"}]
    with (
        patch(
            "src.tools.evaluate_multi_stock_buy_criteria.resolve_securities_csv",
            return_value=(resolved, []),
        ),
        patch(
            "src.tools.evaluate_multi_stock_buy_criteria.CriterionOrchestrator.analyze_for_agent",
            side_effect=RuntimeError("analysis unavailable"),
        ),
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
    unavailable["evidence_gaps"] = ["industrial_competitiveness/formal_business_evidence：取证超时"]
    with (
        patch(
            "src.tools.evaluate_multi_stock_buy_criteria.resolve_securities_csv",
            return_value=(resolved, []),
        ),
        patch(
            "src.tools.evaluate_multi_stock_buy_criteria.CriterionOrchestrator.analyze_for_agent",
            return_value=unavailable,
        ),
    ):
        result = evaluate_multi_stock_buy_criteria("600519")

    assert result["success"] is False
    assert result["source_unavailable_count"] == 1
    assert result["evidence_insufficient_count"] == 0
    assert result["items"][0]["final_decision"] == "分析未完成"
    assert result["errors"] == ["analysis_timeout：关键来源取证超时，本轮未形成公司结论"]

def test_renderer_distinguishes_screening_rejection_from_system_unavailable() -> None:
    rejected = _item("000001", ["pass", "fail"])
    insufficient = _item("000002", ["pass", "insufficient"])
    answer = _build_professional_buy_decision_answer(
        [
            {
                "tool": "evaluate_multi_stock_buy_criteria",
                "arguments": {"symbols": "000001,000002"},
                "result": {
                    "items": [rejected, insufficient],
                    "errors": [],
                },
            }
        ]
    )

    assert answer is not None
    assert "| 公司000001 (000001) | **不符合本次买入条件**" in answer
    assert "| 公司000002 (000002) | **分析未完成**" in answer
    assert "不符合本次买入条件 1 只" in answer
    assert "分析未完成 1 只" in answer
    assert "证据不足 1 只" not in answer

def test_renderer_accepts_only_complete_ordered_eight_passes() -> None:
    passed = _item("600519", ["pass"] * 8)
    stopped = _item("000858", ["pass", "fail"])
    answer = _build_professional_buy_decision_answer(
        [
            {
                "tool": "evaluate_multi_stock_buy_criteria",
                "arguments": {"symbols": "600519,000858"},
                "result": {
                    "items": [passed, stopped],
                    "errors": [],
                },
            }
        ]
    )

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
    answer = _build_professional_buy_decision_answer(
        [
            {
                "tool": "evaluate_multi_stock_buy_criteria",
                "arguments": {"symbols": "600519"},
                "result": {"items": [malformed], "errors": []},
            }
        ]
    )

    assert answer is not None
    assert "执行结构异常" in answer
    assert "| 公司600519 (600519) | **分析失败**" in answer
