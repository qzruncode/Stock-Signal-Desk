"""Contract tests for the authoritative eight-dimensional buy gate."""

from __future__ import annotations

from unittest.mock import patch

from src.services.buy_criteria.base import CriterionEvidence, CriterionResult
from src.services.buy_criteria.orchestrator import (
    CriterionOrchestrator,
    _analysis_to_results,
    _build_summary,
    _cache_matches_current_contract,
    _format_sse,
)
from src.services.buy_criteria.professional_analysis import (
    DIMENSION_DEFINITIONS,
    PROFESSIONAL_BUY_CONTRACT_VERSION,
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


def _dimension(index: int, status: str) -> dict:
    dimension_id, title = DIMENSION_DEFINITIONS[index]
    return {
        "dimension_id": dimension_id,
        "status": status,
        "headline": f"{title}结论",
        "analysis": f"{title}的证据和反证已经完成核验。",
        "key_evidence": [f"{title}支持证据"],
        "counter_evidence": [f"{title}主要反证"],
        "monitoring_points": [f"跟踪{title}"],
    }


def _analysis(statuses: list[str]) -> dict:
    return {
        "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
        "dimensions": [
            _dimension(index, status)
            for index, status in enumerate(statuses)
        ],
        "recommendation_code": (
            "conditional_buy"
            if all(status == "pass" for status in statuses)
            else "wait"
        ),
        "recommendation_reason": "八维布尔闸门结果",
        "overall_summary": "八维布尔闸门已经按顺序执行。",
    }


def _criterion(index: int, status: str) -> CriterionResult:
    dimension_id, title = DIMENSION_DEFINITIONS[index]
    return CriterionResult(
        criterion_id=dimension_id,
        criterion_name=title,
        index=index,
        passed=status == "pass",
        status=status,
        verdict=f"{title}:{status}",
        evidence=CriterionEvidence(),
    )


def test_dimension_contract_is_exactly_the_user_required_eight_axes() -> None:
    assert tuple(item[0] for item in DIMENSION_DEFINITIONS) == EXPECTED_DIMENSIONS


def test_analysis_conversion_stops_at_first_non_pass_dimension() -> None:
    analysis = _analysis([
        "pass",
        "pass",
        "fail",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
    ])

    results = _analysis_to_results(analysis)

    assert [result.criterion_id for result in results] == list(
        EXPECTED_DIMENSIONS[:3]
    )
    assert [result.status for result in results] == ["pass", "pass", "fail"]


def test_summary_allows_buy_only_when_all_eight_dimensions_pass() -> None:
    all_pass = [_criterion(index, "pass").to_dict() for index in range(8)]
    accepted = _build_summary(all_pass, from_cache=False)
    assert accepted["final_decision"] == "可买入"
    assert accepted["gate_pass_complete"] is True
    assert accepted["passed_count"] == 8
    assert accepted["not_evaluated_count"] == 0

    stopped = [
        _criterion(0, "pass").to_dict(),
        _criterion(1, "fail").to_dict(),
    ]
    rejected = _build_summary(stopped, from_cache=False)
    assert rejected["final_decision"] == "不可买入"
    assert rejected["stopped_at"] == EXPECTED_DIMENSIONS[1]
    assert rejected["not_evaluated_count"] == 6
    assert rejected["gate_pass_complete"] is False


def test_cache_accepts_only_a_valid_fail_fast_prefix() -> None:
    valid_failure = {
        "results": [
            _criterion(0, "pass").to_dict(),
            _criterion(1, "insufficient").to_dict(),
        ]
    }
    assert _cache_matches_current_contract(valid_failure) is True
    assert _cache_matches_current_contract({
        "results": [_criterion(index, "pass").to_dict() for index in range(8)]
    }) is True

    assert _cache_matches_current_contract({
        "results": [
            _criterion(0, "fail").to_dict(),
            _criterion(1, "pass").to_dict(),
        ]
    }) is False
    assert _cache_matches_current_contract({
        "results": [_criterion(index, "pass").to_dict() for index in range(7)]
    }) is False
    assert _cache_matches_current_contract({
        "results": [_criterion(1, "fail").to_dict()]
    }) is False


def test_orchestrator_has_one_professional_analysis_entrypoint() -> None:
    analysis = _analysis([
        "pass",
        "fail",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
    ])
    with patch(
        "src.services.buy_criteria.orchestrator.analyze_professional_buy",
        return_value=analysis,
    ) as professional:
        results = CriterionOrchestrator().run(
            "600519",
            save_to_db=False,
            thesis="测试逻辑",
        )

    professional.assert_called_once()
    assert [result.status for result in results] == ["pass", "fail"]


def test_sse_formatter_preserves_structured_payload() -> None:
    output = _format_sse("criterion_complete", {"status": "pass"})
    assert output.startswith("event: criterion_complete\n")
    assert '"status": "pass"' in output
