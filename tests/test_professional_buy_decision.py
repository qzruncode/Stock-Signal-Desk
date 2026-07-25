"""End-to-end contracts for collection-level eight-dimensional decisions."""

from __future__ import annotations

from unittest.mock import patch

from api.v1.endpoints.agent.chat import (
    _build_professional_buy_decision_answer,
)
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
)
from src.tools.evaluate_multi_stock_buy_criteria import (
    evaluate_multi_stock_buy_criteria,
)


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
        "final_decision": "可买入" if all_pass else "不可买入",
        "gate_pass_complete": all_pass,
        "stopped_at": stopped["criterion_id"] if stopped else None,
        "stopped_at_name": stopped["criterion_name"] if stopped else None,
    }


def test_professional_dimension_order_is_exactly_the_requested_eight_axes() -> None:
    assert tuple(item[0] for item in DIMENSION_DEFINITIONS) == EXPECTED_DIMENSIONS


def test_investment_workflow_sends_the_complete_collection_in_one_call() -> None:
    symbols = tuple(f"{index:06d}" for index in range(72))
    calls = compile_task(ResolvedTask(candidate=_task(), symbols=symbols))

    assert len(calls) == 1
    assert calls[0].tool_name == "evaluate_multi_stock_buy_criteria"
    assert calls[0].arguments["symbols"].split(",") == list(symbols)
    assert workflow_for(
        StandardTaskKind.INVESTMENT_DECISION
    ).max_parallel_steps == 1


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


def test_company_failure_is_fail_closed_at_first_dimension() -> None:
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
    assert item["insufficient_count"] == 1
    assert item["not_evaluated_count"] == 7
    assert item["criteria"][0]["criterion_id"] == EXPECTED_DIMENSIONS[0]
    assert item["stopped_at"] == EXPECTED_DIMENSIONS[0]
    assert item["gate_pass_complete"] is False


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
    assert "| 公司600519 (600519) | **不可买入**" in answer
