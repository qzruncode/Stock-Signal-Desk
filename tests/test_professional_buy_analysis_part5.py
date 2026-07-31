from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.services.buy_criteria.professional_analysis import (
    ANALYST_SYSTEM_PROMPT,
    DIMENSION_DEFINITIONS,
    DimensionAssessment,
    ForcedSchemaResponseError,
    ModelDimensionAssessment,
    OverallAssessment,
    ProfessionalAssessment,
    _call_professional_model,
    _dimension_company_context,
    _enforce_mainline_gate_policy,
    _inline_json_schema,
    _repair_incomplete_dimension_headline,
    _refresh_professional_evidence_metadata,
    _target_dimension_context,
    _redact_unsupported_numeric_claims,
    _unsupported_numeric_claims,
    analyze_professional_buy,
    resolve_investment_thesis,
)
from src.services.buy_criteria.base import CriterionEvidence
from src.services.buy_criteria.evaluators.mainline_position import (
    MainlinePositionEvaluator,
)
from src.services.buy_criteria.evaluators.industrial_competitiveness import (
    IndustrialCompetitivenessEvaluator,
)
from src.services.buy_criteria.evidence_queries import structured_thesis_queries
from src.services.buy_criteria.research_enrichment import (
    collect_public_research,
    derive_research_scope,
)
from src.services.catalyst_evidence import extract_business_passages



"""Focused test slice 5; shared fixtures remain local to this slice."""

def _dimension(index: int, status: str) -> DimensionAssessment:
    dimension_id, title = DIMENSION_DEFINITIONS[index]
    return DimensionAssessment(
        dimension_id=dimension_id,
        status=status,
        evaluated_subjects=[],
        headline=f"{title}结论",
        analysis=f"{title}已经同时核验支持证据和主要反证。",
        key_evidence=[f"{title}支持事实"],
        counter_evidence=[f"{title}保留因素"],
        monitoring_points=[f"继续跟踪{title}"],
        mainline_classification=(
            {
                "strategy_profile": "confirmed_mainline",
                "direction_relation": ("core" if status == "pass" else "unrelated"),
                "lifecycle": ("confirmed" if status == "pass" else None),
                "matched_mainline": ("测试主线" if status == "pass" else None),
                "matched_branch": None,
                "trigger_progress": "unknown",
            }
            if index == 0
            else None
        ),
    )

def _assessment(
    statuses: list[str],
    recommendation_code: str = "watchlist",
) -> ProfessionalAssessment:
    return ProfessionalAssessment(
        investment_profile="景气复苏与国产替代驱动的跟踪型公司",
        overall_summary="产业趋势成立，但现金流、估值赔率和经营兑现仍需进一步验证。",
        core_thesis="需求增长推动收入，产品升级与份额提升推动利润弹性",
        biggest_issue="利润向经营现金流的转化仍然偏弱",
        recommendation_code=recommendation_code,
        recommendation_reason="产业逻辑较强，但关键经营质量指标尚未同时改善。",
        dimensions=[_dimension(index, status) for index, status in enumerate(statuses)],
        bull_case_chain="行业需求增长 → 高端产品放量 → 盈利改善 → 估值修复",
        risk_chain="客户压价 → 应收存货增加 → 现金流承压 → 估值长期折价",
        monitoring_points=[
            "经营现金流与净利润的匹配程度",
            "应收账款和存货相对收入的增速",
            "核心新产品的规模收入与毛利率",
        ],
        evidence_gaps=[],
    )

def _evidence() -> dict:
    return {
        "stock_info": {"name": "新强联", "symbol": "300850"},
        "base_packet_meta": {
            "data_time": "2026-07-23T12:00:00+08:00",
            "quote_basis": "盘中最新价",
            "quote_is_intraday": True,
        },
        "requested_at": "2026-07-23T12:00:00+08:00",
        "mainline_strategy": "confirmed_mainline",
        "market_mainline_snapshot": {
            "current_mainlines": [
                {
                    "name": "测试主线",
                    "lifecycle": "confirmed",
                    "branches": ["测试方向"],
                }
            ],
            "candidate_mainlines": [],
        },
        "source_links": [],
        "evidence_gaps": [],
    }

def _candidate_gate_evidence(
    *,
    trigger_status: str = "partial",
) -> dict:
    return {
        "mainline_strategy": "early_positioning",
        "dimension_evidence": {
            "market_mainline": {
                "raw_data": {
                    "market_mainline_report": {
                        "current_mainlines": [],
                        "candidate_mainlines": [
                            {
                                "name": "具身智能",
                                "lifecycle": "validating",
                                "branches": ["减速器", "机器人"],
                                "expected_horizon": "one_to_six_months",
                                "evidence_axes": [
                                    {
                                        "axis": "policy",
                                        "evidence_refs": ["strategy_report:1"],
                                    },
                                    {
                                        "axis": "supply_demand",
                                        "evidence_refs": ["industry_report:1"],
                                    },
                                ],
                                "trigger_assessments": [
                                    {
                                        "description": "核心零部件订单验证",
                                        "status": trigger_status,
                                        "evidence_refs": (
                                            ["industry_report:1"] if trigger_status in {"met", "partial"} else []
                                        ),
                                    }
                                ],
                            }
                        ],
                    },
                },
            },
        },
    }

def _candidate_gate_result(
    *,
    strategy_profile: str,
    status: str = "pass",
) -> DimensionAssessment:
    return DimensionAssessment(
        dimension_id="market_mainline",
        status=status,
        evaluated_subjects=["减速器"],
        headline="减速器属于具身智能候选分支",
        analysis="产业归属、生命周期和触发进度已经分别核验。",
        mainline_classification={
            "strategy_profile": strategy_profile,
            "direction_relation": "emerging_branch",
            "lifecycle": "validating",
            "matched_mainline": "具身智能",
            "matched_branch": "减速器",
            "trigger_progress": "partial",
        },
    )
def test_program_rejects_at_first_failed_gate_without_score() -> None:
    statuses = [
        "pass",
        "pass",
        "fail",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
    ]
    assessment = _assessment(statuses, recommendation_code="conditional_buy")
    with (
        patch(
            "src.services.buy_criteria.professional_analysis.collect_professional_evidence",
            return_value=_evidence(),
        ),
        patch(
            "src.services.buy_criteria.professional_analysis._call_professional_model",
            return_value=(assessment, ""),
        ),
    ):
        result = analyze_professional_buy("300850")

    assert "score" not in result
    assert "score_total" not in result
    assert result["counts"] == {
        "pass": 2,
        "fail": 1,
        "insufficient": 0,
        "not_evaluated": 5,
    }
    assert result["recommendation_code"] == "wait"
    assert len(result["dimensions"]) == 8
    assert result["dimensions"][-1]["dimension_id"] == "major_risks"
    assert result["stopped_at"] == "industry_cycle"
    assert result["gate_pass_complete"] is False

def test_program_allows_conditional_buy_only_after_eight_passes() -> None:
    assessment = _assessment(
        ["pass"] * 8,
        recommendation_code="conditional_buy",
    )
    with (
        patch(
            "src.services.buy_criteria.professional_analysis.collect_professional_evidence",
            return_value=_evidence(),
        ),
        patch(
            "src.services.buy_criteria.professional_analysis._call_professional_model",
            return_value=(assessment, ""),
        ),
    ):
        result = analyze_professional_buy("300850")

    assert result["counts"]["pass"] == 8
    assert result["recommendation_code"] == "conditional_buy"
    assert result["gate_pass_complete"] is True
    assert result["coverage_complete"] is True
    assert result["stopped_at"] is None

def test_model_failure_marks_analysis_unavailable_and_skips_seven() -> None:
    with (
        patch(
            "src.services.buy_criteria.professional_analysis.collect_professional_evidence",
            return_value=_evidence(),
        ),
        patch(
            "src.services.buy_criteria.professional_analysis._call_professional_model",
            return_value=(None, "TLS unavailable"),
        ),
    ):
        result = analyze_professional_buy("300850")

    assert result["recommendation_code"] == "analysis_unavailable"
    assert result["analysis_status"] == "execution_failed"
    assert result["final_decision"] == "分析失败"
    assert result["counts"]["insufficient"] == 1
    assert result["counts"]["not_evaluated"] == 7
    assert len(result["dimensions"]) == 8
    assert result["dimensions"][0]["status"] == "insufficient"
    assert all(item["status"] == "not_evaluated" for item in result["dimensions"][1:])
