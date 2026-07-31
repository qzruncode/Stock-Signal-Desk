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



"""Focused test slice 3; shared fixtures remain local to this slice."""

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
def test_model_result_without_structured_subject_confirmation_is_rejected() -> None:
    evidence = {
        **_evidence(),
        "investment_thesis": "减速器",
        "thesis_context": {
            "summary": "减速器候选",
            "domains": [{"label": "减速器"}],
        },
        "base_company_packet": {},
        "dimension_evidence": {
            "market_mainline": {
                "success": True,
                "evidence_gap": None,
                "summary": "减速器市场证据",
                "structured_context": {},
            },
        },
    }

    def completion(**_kwargs):
        payload = _dimension(0, "fail").model_dump()
        return {
            "model": "test-model",
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "submit_dimension_assessment",
                                    "arguments": __import__("json").dumps(payload),
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

    assert assessment is not None
    assert assessment.dimensions[0].status == "insufficient"
    assert "结构化产业方向" in error

def test_first_gate_failure_does_not_load_later_gate_evidence() -> None:
    evidence = {
        **_evidence(),
        "base_company_packet": {},
        "dimension_evidence": {
            "market_mainline": {
                "success": True,
                "evidence_gap": None,
                "summary": "当前市场主线证据",
                "structured_context": {},
            },
        },
    }
    loaded: list[str] = []

    def completion(**_kwargs):
        payload = _dimension(0, "fail").model_dump()
        return {
            "model": "test-model",
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "submit_dimension_assessment",
                                    "arguments": __import__("json").dumps(payload),
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
        assessment, error = _call_professional_model(
            evidence,
            dimension_loader=loaded.append,
        )

    assert error == ""
    assert assessment is not None
    assert assessment.dimensions[0].status == "fail"
    assert loaded == []

def test_truncated_forced_schema_gets_one_targeted_repair_without_timeout() -> None:
    evidence = {
        **_evidence(),
        "base_company_packet": {},
        "dimension_evidence": {
            "market_mainline": {
                "success": True,
                "evidence_gap": None,
                "summary": "当前市场主线证据",
                "structured_context": {},
            },
        },
    }
    calls: list[dict] = []

    def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": "",
                            "reasoning_content": "完整但被截断的分析过程" * 1000,
                            "tool_calls": [],
                        },
                    }
                ],
                "usage": {"completion_tokens": 12_000},
            }
        payload = _dimension(0, "fail").model_dump()
        return {
            "model": "test-model",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "submit_dimension_assessment",
                                    "arguments": __import__("json").dumps(payload),
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
    assert assessment.dimensions[0].status == "fail"
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 20_000
    assert calls[1]["max_tokens"] == 16_000
    assert "timeout" not in calls[0]
    assert "extra_body" not in calls[0]
    repair_request = __import__("json").loads(calls[1]["messages"][1]["content"])["targeted_repair"]
    assert repair_request["invalid_payload"]["finish_reason"] == "length"
    assert "完整但被截断的分析过程" in repair_request["invalid_payload"]["reasoning_content"]
    assert repair_request["issues"][0]["pointer"] == ("/choices/0/message/tool_calls")

def test_professional_gate_streams_provider_reasoning_without_changing_schema() -> None:
    evidence = {
        **_evidence(),
        "base_company_packet": {},
        "dimension_evidence": {
            "market_mainline": {
                "success": True,
                "evidence_gap": None,
                "summary": "当前市场主线证据",
                "structured_context": {},
            },
        },
    }
    payload = _dimension(0, "fail").model_dump()
    reasoning: list[str] = []
    captured: dict = {}

    def completion(**kwargs):
        captured.update(kwargs)
        return [
            SimpleNamespace(
                model="test-model",
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(
                            content=None,
                            reasoning_content="先核对产业方向与市场主线。",
                            tool_calls=None,
                        ),
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                model="test-model",
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        delta=SimpleNamespace(
                            content=None,
                            reasoning_content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    function=SimpleNamespace(
                                        name="submit_dimension_assessment",
                                        arguments=__import__("json").dumps(payload),
                                    ),
                                )
                            ],
                        ),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                ),
            ),
        ]

    with (
        patch(
            "src.llm.anthropic_gateway.completion_gateway",
            side_effect=completion,
        ),
        patch("src.storage.persist_llm_usage"),
    ):
        assessment, error = _call_professional_model(
            evidence,
            on_reasoning=reasoning.append,
        )

    assert error == ""
    assert assessment is not None
    assert assessment.dimensions[0].status == "fail"
    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}
    assert captured["tool_choice"]["function"]["name"] == ("submit_dimension_assessment")
    assert reasoning == ["先核对产业方向与市场主线。"]

def test_schema_validation_repair_uses_exact_invalid_field_pointer() -> None:
    evidence = {
        **_evidence(),
        "base_company_packet": {},
        "dimension_evidence": {
            "market_mainline": {
                "success": True,
                "evidence_gap": None,
                "summary": "当前市场主线证据",
                "structured_context": {},
            },
        },
    }
    calls: list[dict] = []

    def completion(**kwargs):
        calls.append(kwargs)
        payload = _dimension(0, "fail").model_dump()
        if len(calls) == 1:
            payload["headline"] = "过长标题" * 40
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
    assert len(calls) == 2
    repair_request = __import__("json").loads(calls[1]["messages"][1]["content"])["targeted_repair"]
    assert repair_request["invalid_payload"]["headline"].startswith("过长标题")
    assert repair_request["issues"][0]["pointer"] == "/headline"
    assert repair_request["issues"][0]["code"] == "string_too_long"
