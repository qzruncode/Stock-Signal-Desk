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



"""Focused test slice 1; shared fixtures remain local to this slice."""

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
def test_prompt_requires_all_eight_professional_axes_and_counter_evidence() -> None:
    for _, title in DIMENSION_DEFINITIONS:
        assert title in ANALYST_SYSTEM_PROMPT
    assert "当前维度不通过时，程序不会再调用后续维度" in ANALYST_SYSTEM_PROMPT
    assert "同时列出支持证据与反证" in ANALYST_SYSTEM_PROMPT
    assert "估值不能只报一个PE" in ANALYST_SYSTEM_PROMPT
    assert "现金流、应收、存货、客户集中" in ANALYST_SYSTEM_PROMPT
    assert "1—3年结构性趋势" in ANALYST_SYSTEM_PROMPT
    assert "未来1—6个月A股主导产业叙事" in ANALYST_SYSTEM_PROMPT
    assert "资金流、涨跌幅、成交排名、均线、技术指标和个股走势完全不属于第一维证据" in ANALYST_SYSTEM_PROMPT
    assert "公司真实受益由第二维判断" in ANALYST_SYSTEM_PROMPT
    assert "detected 只证明曾出现过" in ANALYST_SYSTEM_PROMPT
    assert "不能笼统写“证据不足”" in ANALYST_SYSTEM_PROMPT

def test_model_dimension_schema_exposes_only_binary_qualification_status() -> None:
    status_schema = ModelDimensionAssessment.model_json_schema()["properties"]["status"]
    assert status_schema["enum"] == ["pass", "fail"]

def test_confirmed_strategy_preserves_candidate_membership_but_fails_gate() -> None:
    evidence = _candidate_gate_evidence()
    evidence["mainline_strategy"] = "confirmed_mainline"
    result = _enforce_mainline_gate_policy(
        _candidate_gate_result(
            strategy_profile="confirmed_mainline",
            status="fail",
        ),
        evidence,
    )

    assert result.status == "fail"
    assert "属于具身智能候选分支" in result.headline
    assert "未通过确认型主线门槛" in result.headline
    assert "不表示该产业不存在" in result.analysis

def test_early_positioning_candidate_passes_only_with_structured_activation() -> None:
    result = _enforce_mainline_gate_policy(
        _candidate_gate_result(strategy_profile="early_positioning"),
        _candidate_gate_evidence(),
    )

    assert result.status == "pass"
    assert result.mainline_classification is not None
    assert result.mainline_classification.matched_branch == "减速器"

def test_early_positioning_candidate_without_trigger_is_program_blocked() -> None:
    result = _enforce_mainline_gate_policy(
        _candidate_gate_result(strategy_profile="early_positioning"),
        _candidate_gate_evidence(trigger_status="unmet"),
    )

    assert result.status == "fail"
    assert "前瞻布局型准入条件" in result.headline
    assert "触发条件" in result.analysis

def test_mainline_classification_rejects_composite_dynamic_branch_name() -> None:
    result = _candidate_gate_result(
        strategy_profile="confirmed_mainline",
        status="fail",
    ).model_copy(deep=True)
    assert result.mainline_classification is not None
    result.mainline_classification.matched_branch = "减速器/机器人"

    with pytest.raises(ForcedSchemaResponseError) as caught:
        _enforce_mainline_gate_policy(
            result,
            {
                **_candidate_gate_evidence(),
                "mainline_strategy": "confirmed_mainline",
            },
        )

    assert caught.value.issues == [
        {
            "pointer": "/mainline_classification/matched_branch",
            "code": "mainline_branch_binding_invalid",
            "expected": ("one exact branch name copied from the matched structured " "mainline"),
            "allowed": ["减速器", "机器人"],
        }
    ]

def test_source_failures_are_recorded_without_leaking_provider_payloads() -> None:
    evidence = {
        "dimension_evidence": {
            "industrial_competitiveness": {
                "evidence_gap": None,
                "raw_data": {
                    "formal_business_evidence_error": ("<html><h1>504 Gateway Time-out</h1></html>"),
                },
            },
        },
        "public_research": {
            "lens_status": {"company_position": "retrieval_failed"},
            "attempts": [],
            "items": [],
        },
        "base_packet_meta": {"errors": []},
    }

    _refresh_professional_evidence_metadata(evidence)

    assert evidence["source_failures"] == [
        {
            "section": "industrial_competitiveness",
            "source": "formal_business_evidence",
            "error_code": "timeout",
            "summary": ("industrial_competitiveness/formal_business_evidence：取证超时"),
        },
        {
            "section": "public_research",
            "source": "company_position",
            "error_code": "unavailable",
            "summary": "public_research/company_position：来源不可用",
        },
    ]
    assert "<html>" not in str(evidence["evidence_gaps"])

def test_research_scope_comes_from_latest_official_material_segments() -> None:
    packet = {
        "business_segments": {
            "source_url": "https://example.com/segments",
            "items": [
                {
                    "report_date": "2025-12-31",
                    "category": "region",
                    "segment_name": "境内",
                    "revenue_share_pct": 95,
                },
                {
                    "report_date": "2025-12-31",
                    "category": "product",
                    "segment_name": "风电类产品",
                    "revenue_share_pct": 77.36,
                },
                {
                    "report_date": "2025-12-31",
                    "category": "industry",
                    "segment_name": "回转支承",
                    "revenue_share_pct": 80.62,
                },
                {
                    "report_date": "2024-12-31",
                    "category": "product",
                    "segment_name": "旧年度产品",
                    "revenue_share_pct": 99,
                },
            ],
        }
    }

    scope = derive_research_scope(
        {"symbol": "300850", "industry": "通用设备制造业"},
        packet,
    )

    assert scope["primary_labels"][:2] == ["回转支承", "风电类产品"]
    assert "境内" not in scope["primary_labels"]
    assert "旧年度产品" not in scope["primary_labels"]

    stock_info = {
        "industry": "通用设备制造业",
        "_derived_research_scope": scope,
    }
    assert structured_thesis_queries(stock_info)[:2] == [
        "回转支承",
        "风电类产品",
    ]

def test_structured_domain_precedes_business_but_summary_does_not_consume_search_slot() -> None:
    scope = derive_research_scope(
        {
            "symbol": "000551",
            "industry": "专用设备制造业",
            "_investment_thesis": "",
            "_investment_thesis_context": {
                "summary": "人形机器人产业里的减速器财务筛选候选集合",
                "domains": [
                    {
                        "label": "减速器",
                        "board_queries": ["减速器"],
                        "mapping_type": "catalog_binding",
                        "rationale": "关节传动环节",
                        "unresolved_parts": [],
                    }
                ],
            },
        },
        {
            "business_segments": {
                "source_url": "https://example.com/segments",
                "items": [
                    {
                        "report_date": "2025-12-31",
                        "category": "product",
                        "segment_name": "精密轴承",
                        "revenue_share_pct": 17.18,
                    }
                ],
            },
        },
    )

    assert scope["primary_labels"][:2] == ["减速器", "精密轴承"]
    assert scope["primary_labels"][-1] == "人形机器人产业里的减速器财务筛选候选集合"

def test_empty_plain_thesis_resolves_from_structured_domain() -> None:
    context = {
        "summary": "人形机器人产业里的减速器候选",
        "domains": [
            {
                "label": "减速器",
                "board_queries": ["减速器"],
                "mapping_type": "catalog_binding",
            }
        ],
    }

    assert resolve_investment_thesis("", context) == "减速器"
    assert resolve_investment_thesis("当前明确主题", context) == "当前明确主题"

def test_formal_report_retrieval_uses_derived_scope_without_user_thesis() -> None:
    content = (
        "公司通用零部件业务保持平稳。"
        "风电类产品收入增长，核心回转支承持续向大兆瓦海上机型迭代。"
        "境外销售渠道仍在建设。"
    )

    passages = extract_business_passages(
        content,
        thesis="",
        thesis_context=None,
        research_scope={"primary_labels": ["回转支承", "风电类产品"]},
    )

    assert passages
    assert "风电类产品" in passages[0]["excerpt"]
    assert passages[0]["selection_method"] == "uniform_document_coverage"

def test_public_research_distinguishes_failure_from_valid_empty_result() -> None:
    def search(query: str, **_: object) -> dict:
        if "供需 景气" in query:
            return {"results": [], "errors": ["provider timeout"]}
        if "竞争格局" in query or "近三个月" in query:
            return {
                "results": [
                    {
                        "title": "可核验研究",
                        "url": "https://example.com/research",
                        "snippet": "公开研究摘要",
                        "source": "测试来源",
                    }
                ],
                "errors": [],
            }
        return {"results": [], "errors": []}

    stock_info = {
        "symbol": "000001",
        "name": "测试公司",
        "_derived_research_scope": {"primary_labels": ["测试产品"]},
    }
    with patch("src.tools.websearch.websearch", side_effect=search):
        result = collect_public_research(stock_info)

    assert result["lens_status"]["market_consensus"] == "retrieved"
    assert result["lens_status"]["competition_structure"] == "retrieved"
    assert result["lens_status"]["cycle_supply_demand"] == "retrieval_failed"
    assert result["lens_status"]["company_position"] == ("no_matching_public_material")

def test_contract_rejects_missing_or_reordered_dimensions() -> None:
    payload = _assessment(["pass"] * 8).model_dump()
    payload["dimensions"] = list(reversed(payload["dimensions"]))
    with pytest.raises(ValidationError):
        ProfessionalAssessment.model_validate(payload)

    payload = _assessment(["pass"] * 8).model_dump()
    payload["dimensions"] = payload["dimensions"][:-1]
    with pytest.raises(ValidationError):
        ProfessionalAssessment.model_validate(payload)

def test_tool_schema_is_fully_inlined_for_gateway_json_grammar() -> None:
    schema = _inline_json_schema(ProfessionalAssessment.model_json_schema())
    serialized = str(schema)
    assert "$defs" not in serialized
    assert "$ref" not in serialized
    dimensions = schema["properties"]["dimensions"]["items"]
    assert dimensions["type"] == "object"
    assert "dimension_id" in dimensions["properties"]
