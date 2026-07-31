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



"""Focused test slice 2; shared fixtures remain local to this slice."""

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
def test_numeric_claim_guard_rejects_untraceable_numbers_and_allows_rounding() -> None:
    item = _dimension(1, "pass")
    item.analysis = "公司毛利率约28%，但不能据此声称行业通常为35%。"
    unsupported = _unsupported_numeric_claims(
        item,
        "证据显示公司毛利率为28.48%；分析窗口为未来6—12个月。",
    )
    assert "35%" in unsupported
    assert "28%" not in unsupported

    item.analysis = "未来6个月到12个月需要继续观察。"
    assert (
        _unsupported_numeric_claims(
            item,
            "分析窗口为未来6—12个月。",
        )
        == []
    )

    item.analysis = "截至2026年7月23日，结论仍需复核。"
    assert (
        _unsupported_numeric_claims(
            item,
            "requested_at=2026-07-23T12:00:00+08:00",
        )
        == []
    )

    item.analysis = "当前价格为24.84元。"
    assert (
        _unsupported_numeric_claims(
            item,
            {"quote": {"price": 24.84}, "url": "https://example.test/35"},
        )
        == []
    )

    item.analysis = "行业毛利率通常为35%。"
    assert "35%" in _unsupported_numeric_claims(
        item,
        {"quote": {"price": 24.84}, "url": "https://example.test/35"},
    )

    item.analysis = "资金金额为3902万元，年度收入为46.28亿元。"
    assert (
        _unsupported_numeric_claims(
            item,
            {"capital_flow_yuan": 39_020_000, "revenue_yuan": 4_628_000_000},
        )
        == []
    )

    item.analysis = "行业毛利率通常为35%，估值折价60%。"
    redacted = _redact_unsupported_numeric_claims(item, ["35%", "60%"])
    assert "35%" not in redacted.analysis
    assert "60%" not in redacted.analysis
    assert "相关时间或数值陈述" in redacted.analysis
    assert "已从展示中删除" in redacted.counter_evidence[-1]

def test_numeric_guard_covers_bare_overall_monitoring_thresholds() -> None:
    overall = OverallAssessment(
        investment_profile="公司具备产业机会，但验证项仍然较多。",
        overall_summary="当前进入跟踪池，等待经营质量与收入增速共同改善。",
        core_thesis="需求回升带动收入增长，盈利改善推动估值修复。",
        biggest_issue="利润尚未充分转化为经营现金流。",
        recommendation_code="watchlist",
        recommendation_reason="基本面方向可跟踪，但关键经营数据仍需验证。",
        bull_case_chain="需求改善 → 利润增长 → 估值修复",
        risk_chain="现金流偏弱 → 经营质量承压 → 估值折价",
        monitoring_points=[
            "现金利润比回升至0.3以上",
            "季度营收恢复同比增长",
            "应收账款周转速度改善",
        ],
        evidence_gaps=[],
    )
    unsupported = _unsupported_numeric_claims(
        overall,
        {"validated_dimensions": [{"analysis": "现金利润比偏低"}]},
    )
    assert unsupported == ["0.3"]
    redacted = _redact_unsupported_numeric_claims(overall, unsupported)
    assert isinstance(redacted, OverallAssessment)
    assert redacted.monitoring_points == [
        "该项包含未核验数值，原陈述已删除。",
        "季度营收恢复同比增长",
        "应收账款周转速度改善",
    ]
    assert "已从展示中删除" in redacted.evidence_gaps[-1]

def test_incomplete_dimension_headline_is_repaired_from_analysis() -> None:
    item = _dimension(0, "pass")
    item.headline = "中期主导叙事为"
    item.analysis = "中期主导叙事为科技与出海，公司所在方向属于结构趋势下的轮动支线。" "短期交易确认偏强。"

    repaired = _repair_incomplete_dimension_headline(item)

    assert repaired.headline == ("中期主导叙事为科技与出海，公司所在方向属于结构趋势下的轮动支线")

def test_professional_model_executes_each_dimension_and_overall_contract() -> None:
    evidence = {
        **_evidence(),
        "base_company_packet": {},
        "investment_thesis": "测试投资逻辑",
        "dimension_evidence": {
            dimension_id: {
                "success": True,
                "evidence_gap": None,
                "summary": f"{title}的本轮测试证据",
            }
            for dimension_id, title in DIMENSION_DEFINITIONS
        },
    }

    called_dimensions: list[str] = []

    def completion(**kwargs):
        tool_name = kwargs["tools"][0]["function"]["name"]
        request = __import__("json").loads(kwargs["messages"][1]["content"])
        if tool_name == "submit_dimension_assessment":
            dimension_id = request["requested_dimension"]["dimension_id"]
            called_dimensions.append(dimension_id)
            index = [key for key, _ in DIMENSION_DEFINITIONS].index(dimension_id)
            payload = _dimension(index, "pass").model_dump()
        else:
            payload = {
                key: value
                for key, value in _assessment(
                    ["pass"] * 8,
                    recommendation_code="conditional_buy",
                )
                .model_dump()
                .items()
                if key != "dimensions"
            }
        return {
            "model": "test-model",
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": tool_name,
                                    "arguments": __import__("json").dumps(
                                        payload,
                                        ensure_ascii=False,
                                    ),
                                }
                            }
                        ]
                    }
                }
            ],
            "usage": {},
        }

    with (
        patch(
            "src.llm.anthropic_gateway.completion_gateway",
            side_effect=completion,
        ),
        patch("src.storage.persist_llm_usage"),
    ):
        assessment, error = _call_professional_model(evidence)

    assert error == ""
    assert assessment is not None
    assert called_dimensions == [key for key, _ in DIMENSION_DEFINITIONS]
    assert [item.dimension_id for item in assessment.dimensions] == [key for key, _ in DIMENSION_DEFINITIONS]
    assert all(item.status == "pass" for item in assessment.dimensions)

def test_professional_model_stops_after_first_non_pass_dimension() -> None:
    evidence = {
        **_evidence(),
        "base_company_packet": {},
        "investment_thesis": "测试投资逻辑",
        "dimension_evidence": {
            dimension_id: {
                "success": True,
                "evidence_gap": None,
                "summary": f"{title}的本轮测试证据",
            }
            for dimension_id, title in DIMENSION_DEFINITIONS
        },
    }
    called_dimensions: list[str] = []

    def completion(**kwargs):
        tool_name = kwargs["tools"][0]["function"]["name"]
        assert tool_name == "submit_dimension_assessment"
        request = __import__("json").loads(kwargs["messages"][1]["content"])
        dimension_id = request["requested_dimension"]["dimension_id"]
        called_dimensions.append(dimension_id)
        index = [key for key, _ in DIMENSION_DEFINITIONS].index(dimension_id)
        payload = _dimension(
            index,
            "fail" if index == 2 else "pass",
        ).model_dump()
        return {
            "model": "test-model",
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": tool_name,
                                    "arguments": __import__("json").dumps(
                                        payload,
                                        ensure_ascii=False,
                                    ),
                                }
                            }
                        ]
                    }
                }
            ],
            "usage": {},
        }

    with (
        patch(
            "src.llm.anthropic_gateway.completion_gateway",
            side_effect=completion,
        ),
        patch("src.storage.persist_llm_usage"),
    ):
        assessment, error = _call_professional_model(evidence)

    assert error == ""
    assert assessment is not None
    assert called_dimensions == [
        "market_mainline",
        "industrial_competitiveness",
        "industry_cycle",
    ]
    assert [item.status for item in assessment.dimensions] == [
        "pass",
        "pass",
        "fail",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
        "not_evaluated",
    ]

def test_dimension_request_keeps_structured_thesis_and_target_board_context() -> None:
    long_summary = "全市场板块榜单" * 800
    thesis_context = {
        "summary": "人形机器人产业里的减速器候选",
        "domains": [
            {
                "label": "减速器",
                "board_queries": ["减速器"],
                "mapping_type": "catalog_binding",
                "rationale": "人形机器人关节传动环节",
                "unresolved_parts": [],
            }
        ],
    }
    evidence = {
        **_evidence(),
        "investment_thesis": "减速器",
        "thesis_context": thesis_context,
        "base_company_packet": {},
        "dimension_evidence": {
            "market_mainline": {
                "success": True,
                "evidence_gap": None,
                "summary": long_summary,
                "structured_context": {
                    "thesis_membership": {
                        "requested_domains": ["减速器"],
                    },
                    "direction_board_mapping": {
                        "industry": [],
                        "concept": [
                            {
                                "name": "减速器",
                                "code": "BK001",
                            }
                        ],
                    },
                },
            },
        },
    }
    captured: list[dict] = []

    def completion(**kwargs):
        request = __import__("json").loads(kwargs["messages"][1]["content"])
        captured.append(request)
        payload = _dimension(0, "fail").model_copy(update={"evaluated_subjects": ["减速器"]}).model_dump()
        return {
            "model": "test-model",
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "submit_dimension_assessment",
                                    "arguments": __import__("json").dumps(
                                        payload,
                                        ensure_ascii=False,
                                    ),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {},
        }

    with (
        patch(
            "src.llm.anthropic_gateway.completion_gateway",
            side_effect=completion,
        ),
        patch("src.storage.persist_llm_usage"),
    ):
        assessment, error = _call_professional_model(evidence)

    assert error == ""
    assert assessment is not None
    assert captured[0]["thesis_context"] == thesis_context
    structured = captured[0]["dimension_evidence"]["market_mainline"]["structured_context"]
    assert structured["thesis_membership"]["requested_domains"] == ["减速器"]
    assert structured["direction_board_mapping"]["concept"][0]["name"] == "减速器"
    assert "target_board_flows" not in structured
    assert "减速器" not in captured[0]["dimension_evidence"]["market_mainline"]["summary"]
