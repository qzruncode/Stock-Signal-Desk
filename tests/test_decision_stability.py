# -*- coding: utf-8 -*-
"""Typed decision boundary tests."""

from src.analyzer import AnalysisResult, stabilize_decision_with_structure


def _result(*, decision_type: str, operation_advice: str) -> AnalysisResult:
    return AnalysisResult(
        code="002812",
        name="恩捷股份",
        sentiment_score=68,
        trend_prediction="模型结论",
        operation_advice=operation_advice,
        decision_type=decision_type,
        report_language="zh",
        current_price=33.4,
        dashboard={"core_conclusion": {"one_sentence": "模型原始结论"}},
    )


def test_explicit_decision_is_not_rewritten_from_market_evidence() -> None:
    result = _result(decision_type="buy", operation_advice="建议买入")

    stabilize_decision_with_structure(
        result,
        {"support_levels": [30.0], "resistance_levels": [34.0]},
        {
            "capital_flow": {
                "status": "ok",
                "data": {"stock_flow": {"main_net_inflow": -1_000_000}},
            }
        },
    )

    assert result.decision_type == "buy"
    assert result.operation_advice == "建议买入"
    assert result.sentiment_score == 68
    assert result.dashboard["decision_validation"] == {
        "source": "model_structured_output",
        "rule_based_rewrite": False,
    }


def test_free_form_decision_text_is_not_classified() -> None:
    result = _result(decision_type="建议卖出", operation_advice="建议卖出")

    stabilize_decision_with_structure(result)

    assert result.decision_type == "hold"
    assert result.operation_advice == "建议卖出"


def test_typed_sell_is_preserved_even_when_other_evidence_conflicts() -> None:
    result = _result(decision_type="sell", operation_advice="卖出")

    stabilize_decision_with_structure(
        result,
        {"support_levels": [33.0]},
        {
            "capital_flow": {
                "status": "ok",
                "data": {"stock_flow": {"main_net_inflow": 2_000_000}},
            }
        },
    )

    assert result.decision_type == "sell"
    assert result.operation_advice == "卖出"
    assert result.dashboard["decision_type"] == "sell"
