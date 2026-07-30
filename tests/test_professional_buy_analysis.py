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
                "direction_relation": (
                    "core" if status == "pass" else "unrelated"
                ),
                "lifecycle": (
                    "confirmed" if status == "pass" else None
                ),
                "matched_mainline": (
                    "测试主线" if status == "pass" else None
                ),
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
        dimensions=[
            _dimension(index, status)
            for index, status in enumerate(statuses)
        ],
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
            "current_mainlines": [{
                "name": "测试主线",
                "lifecycle": "confirmed",
                "branches": ["测试方向"],
            }],
            "candidate_mainlines": [],
        },
        "source_links": [],
        "evidence_gaps": [],
    }


def test_prompt_requires_all_eight_professional_axes_and_counter_evidence() -> None:
    for _, title in DIMENSION_DEFINITIONS:
        assert title in ANALYST_SYSTEM_PROMPT
    assert "当前维度不通过时，程序不会再调用后续维度" in ANALYST_SYSTEM_PROMPT
    assert "同时列出支持证据与反证" in ANALYST_SYSTEM_PROMPT
    assert "估值不能只报一个PE" in ANALYST_SYSTEM_PROMPT
    assert "现金流、应收、存货、客户集中" in ANALYST_SYSTEM_PROMPT
    assert "1—3年结构性趋势" in ANALYST_SYSTEM_PROMPT
    assert "未来1—6个月A股主导产业叙事" in ANALYST_SYSTEM_PROMPT
    assert (
        "资金流、涨跌幅、成交排名、均线、技术指标和个股走势完全不属于第一维证据"
        in ANALYST_SYSTEM_PROMPT
    )
    assert "公司真实受益由第二维判断" in ANALYST_SYSTEM_PROMPT
    assert "detected 只证明曾出现过" in ANALYST_SYSTEM_PROMPT
    assert "不能笼统写“证据不足”" in ANALYST_SYSTEM_PROMPT


def test_model_dimension_schema_exposes_only_binary_qualification_status() -> None:
    status_schema = ModelDimensionAssessment.model_json_schema()[
        "properties"
    ]["status"]
    assert status_schema["enum"] == ["pass", "fail"]


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
                        "candidate_mainlines": [{
                            "name": "具身智能",
                            "lifecycle": "validating",
                            "branches": ["减速器", "机器人"],
                            "expected_horizon": "one_to_six_months",
                            "evidence_axes": [{
                                "axis": "policy",
                                "evidence_refs": ["strategy_report:1"],
                            }, {
                                "axis": "supply_demand",
                                "evidence_refs": ["industry_report:1"],
                            }],
                            "trigger_assessments": [{
                                "description": "核心零部件订单验证",
                                "status": trigger_status,
                                "evidence_refs": (
                                    ["industry_report:1"]
                                    if trigger_status in {"met", "partial"}
                                    else []
                                ),
                            }],
                        }],
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

    assert caught.value.issues == [{
        "pointer": "/mainline_classification/matched_branch",
        "code": "mainline_branch_binding_invalid",
        "expected": (
            "one exact branch name copied from the matched structured "
            "mainline"
        ),
        "allowed": ["减速器", "机器人"],
    }]


def test_source_failures_are_recorded_without_leaking_provider_payloads() -> None:
    evidence = {
        "dimension_evidence": {
            "industrial_competitiveness": {
                "evidence_gap": None,
                "raw_data": {
                    "formal_business_evidence_error": (
                        "<html><h1>504 Gateway Time-out</h1></html>"
                    ),
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
            "summary": (
                "industrial_competitiveness/formal_business_evidence：取证超时"
            ),
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
                "domains": [{
                    "label": "减速器",
                    "board_queries": ["减速器"],
                    "mapping_type": "catalog_binding",
                    "rationale": "关节传动环节",
                    "unresolved_parts": [],
                }],
            },
        },
        {
            "business_segments": {
                "source_url": "https://example.com/segments",
                "items": [{
                    "report_date": "2025-12-31",
                    "category": "product",
                    "segment_name": "精密轴承",
                    "revenue_share_pct": 17.18,
                }],
            },
        },
    )

    assert scope["primary_labels"][:2] == ["减速器", "精密轴承"]
    assert scope["primary_labels"][-1] == "人形机器人产业里的减速器财务筛选候选集合"


def test_empty_plain_thesis_resolves_from_structured_domain() -> None:
    context = {
        "summary": "人形机器人产业里的减速器候选",
        "domains": [{
            "label": "减速器",
            "board_queries": ["减速器"],
            "mapping_type": "catalog_binding",
        }],
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
                "results": [{
                    "title": "可核验研究",
                    "url": "https://example.com/research",
                    "snippet": "公开研究摘要",
                    "source": "测试来源",
                }],
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
    assert result["lens_status"]["company_position"] == (
        "no_matching_public_material"
    )


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
    assert _unsupported_numeric_claims(
        item,
        "分析窗口为未来6—12个月。",
    ) == []

    item.analysis = "截至2026年7月23日，结论仍需复核。"
    assert _unsupported_numeric_claims(
        item,
        "requested_at=2026-07-23T12:00:00+08:00",
    ) == []

    item.analysis = "当前价格为24.84元。"
    assert _unsupported_numeric_claims(
        item,
        {"quote": {"price": 24.84}, "url": "https://example.test/35"},
    ) == []

    item.analysis = "行业毛利率通常为35%。"
    assert "35%" in _unsupported_numeric_claims(
        item,
        {"quote": {"price": 24.84}, "url": "https://example.test/35"},
    )

    item.analysis = "资金金额为3902万元，年度收入为46.28亿元。"
    assert _unsupported_numeric_claims(
        item,
        {"capital_flow_yuan": 39_020_000, "revenue_yuan": 4_628_000_000},
    ) == []

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
    item.analysis = (
        "中期主导叙事为科技与出海，公司所在方向属于结构趋势下的轮动支线。"
        "短期交易确认偏强。"
    )

    repaired = _repair_incomplete_dimension_headline(item)

    assert repaired.headline == (
        "中期主导叙事为科技与出海，公司所在方向属于结构趋势下的轮动支线"
    )


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
                ).model_dump().items()
                if key != "dimensions"
            }
        return {
            "model": "test-model",
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": tool_name,
                            "arguments": __import__("json").dumps(
                                payload,
                                ensure_ascii=False,
                            ),
                        }
                    }]
                }
            }],
            "usage": {},
        }

    with patch(
        "src.llm.anthropic_gateway.completion_gateway",
        side_effect=completion,
    ), patch("src.storage.persist_llm_usage"):
        assessment, error = _call_professional_model(evidence)

    assert error == ""
    assert assessment is not None
    assert called_dimensions == [key for key, _ in DIMENSION_DEFINITIONS]
    assert [item.dimension_id for item in assessment.dimensions] == [
        key for key, _ in DIMENSION_DEFINITIONS
    ]
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
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": tool_name,
                            "arguments": __import__("json").dumps(
                                payload,
                                ensure_ascii=False,
                            ),
                        }
                    }]
                }
            }],
            "usage": {},
        }

    with patch(
        "src.llm.anthropic_gateway.completion_gateway",
        side_effect=completion,
    ), patch("src.storage.persist_llm_usage"):
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
        "domains": [{
            "label": "减速器",
            "board_queries": ["减速器"],
            "mapping_type": "catalog_binding",
            "rationale": "人形机器人关节传动环节",
            "unresolved_parts": [],
        }],
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
                        "concept": [{
                            "name": "减速器",
                            "code": "BK001",
                        }],
                    },
                },
            },
        },
    }
    captured: list[dict] = []

    def completion(**kwargs):
        request = __import__("json").loads(kwargs["messages"][1]["content"])
        captured.append(request)
        payload = _dimension(0, "fail").model_copy(
            update={"evaluated_subjects": ["减速器"]}
        ).model_dump()
        return {
            "model": "test-model",
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "submit_dimension_assessment",
                            "arguments": __import__("json").dumps(
                                payload,
                                ensure_ascii=False,
                            ),
                        },
                    }],
                },
            }],
            "usage": {},
        }

    with patch(
        "src.llm.anthropic_gateway.completion_gateway",
        side_effect=completion,
    ), patch("src.storage.persist_llm_usage"):
        assessment, error = _call_professional_model(evidence)

    assert error == ""
    assert assessment is not None
    assert captured[0]["thesis_context"] == thesis_context
    structured = captured[0]["dimension_evidence"]["market_mainline"][
        "structured_context"
    ]
    assert structured["thesis_membership"]["requested_domains"] == ["减速器"]
    assert structured["direction_board_mapping"]["concept"][0]["name"] == "减速器"
    assert "target_board_flows" not in structured
    assert "减速器" not in captured[0]["dimension_evidence"]["market_mainline"]["summary"]


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
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "submit_dimension_assessment",
                            "arguments": __import__("json").dumps(payload),
                        },
                    }],
                },
            }],
            "usage": {},
        }

    with patch(
        "src.llm.anthropic_gateway.completion_gateway",
        side_effect=completion,
    ), patch("src.storage.persist_llm_usage"):
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
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "submit_dimension_assessment",
                            "arguments": __import__("json").dumps(payload),
                        },
                    }],
                },
            }],
            "usage": {},
        }

    with patch(
        "src.llm.anthropic_gateway.completion_gateway",
        side_effect=completion,
    ), patch("src.storage.persist_llm_usage"):
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
                "choices": [{
                    "finish_reason": "length",
                    "message": {
                        "content": "",
                        "reasoning_content": "完整但被截断的分析过程" * 1000,
                        "tool_calls": [],
                    },
                }],
                "usage": {"completion_tokens": 12_000},
            }
        payload = _dimension(0, "fail").model_dump()
        return {
            "model": "test-model",
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "submit_dimension_assessment",
                            "arguments": __import__("json").dumps(payload),
                        },
                    }],
                },
            }],
            "usage": {},
        }

    with patch(
        "src.llm.anthropic_gateway.completion_gateway",
        side_effect=completion,
    ), patch("src.storage.persist_llm_usage"):
        assessment, error = _call_professional_model(evidence)

    assert error == ""
    assert assessment is not None
    assert assessment.dimensions[0].status == "fail"
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 20_000
    assert calls[1]["max_tokens"] == 16_000
    assert "timeout" not in calls[0]
    assert "extra_body" not in calls[0]
    repair_request = __import__("json").loads(
        calls[1]["messages"][1]["content"]
    )["targeted_repair"]
    assert repair_request["invalid_payload"]["finish_reason"] == "length"
    assert "完整但被截断的分析过程" in repair_request[
        "invalid_payload"
    ]["reasoning_content"]
    assert repair_request["issues"][0]["pointer"] == (
        "/choices/0/message/tool_calls"
    )


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
                choices=[SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content="先核对产业方向与市场主线。",
                        tool_calls=None,
                    ),
                )],
                usage=None,
            ),
            SimpleNamespace(
                model="test-model",
                choices=[SimpleNamespace(
                    finish_reason="tool_calls",
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        tool_calls=[SimpleNamespace(
                            index=0,
                            function=SimpleNamespace(
                                name="submit_dimension_assessment",
                                arguments=__import__("json").dumps(payload),
                            ),
                        )],
                    ),
                )],
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                ),
            ),
        ]

    with patch(
        "src.llm.anthropic_gateway.completion_gateway",
        side_effect=completion,
    ), patch("src.storage.persist_llm_usage"):
        assessment, error = _call_professional_model(
            evidence,
            on_reasoning=reasoning.append,
        )

    assert error == ""
    assert assessment is not None
    assert assessment.dimensions[0].status == "fail"
    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}
    assert captured["tool_choice"]["function"]["name"] == (
        "submit_dimension_assessment"
    )
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
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "submit_dimension_assessment",
                            "arguments": __import__("json").dumps(
                                payload,
                                ensure_ascii=False,
                            ),
                        },
                    }],
                },
            }],
            "usage": {},
        }

    with patch(
        "src.llm.anthropic_gateway.completion_gateway",
        side_effect=completion,
    ), patch("src.storage.persist_llm_usage"):
        assessment, error = _call_professional_model(evidence)

    assert error == ""
    assert assessment is not None
    assert len(calls) == 2
    repair_request = __import__("json").loads(
        calls[1]["messages"][1]["content"]
    )["targeted_repair"]
    assert repair_request["invalid_payload"]["headline"].startswith("过长标题")
    assert repair_request["issues"][0]["pointer"] == "/headline"
    assert repair_request["issues"][0]["code"] == "string_too_long"


def test_precomputed_mainline_is_not_rejudged_and_is_sent_to_gate_two() -> None:
    evidence = {
        **_evidence(),
        "base_company_packet": {},
        "dimension_evidence": {
            "industrial_competitiveness": {
                "success": True,
                "evidence_gap": None,
                "summary": "公司业务和竞争优势证据",
                "structured_context": {},
            },
        },
    }
    captured: list[dict] = []

    def completion(**kwargs):
        request = __import__("json").loads(
            kwargs["messages"][1]["content"]
        )
        captured.append(request)
        payload = _dimension(1, "fail").model_dump()
        return {
            "model": "test-model",
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "submit_dimension_assessment",
                            "arguments": __import__("json").dumps(payload),
                        },
                    }],
                },
            }],
            "usage": {},
        }

    with patch(
        "src.llm.anthropic_gateway.completion_gateway",
        side_effect=completion,
    ), patch("src.storage.persist_llm_usage"):
        assessment, error = _call_professional_model(
            evidence,
            precomputed_dimensions=(_dimension(0, "pass"),),
        )

    assert error == ""
    assert assessment is not None
    assert len(captured) == 1
    assert captured[0]["requested_dimension"]["dimension_id"] == (
        "industrial_competitiveness"
    )
    assert captured[0]["prior_dimensions"][0]["dimension_id"] == (
        "market_mainline"
    )


def test_target_board_context_contains_mapping_but_no_market_timing_data() -> None:
    raw = {
        "thesis_membership": {
            "requested_domains": ["减速器"],
            "matched_domains": ["减速器"],
            "lookup_themes": ["减速器"],
            "boards": ["减速器"],
        },
        "board_catalog": {
            "industry": {"matched_items": []},
            "concept": {
                "matched_items": [{
                    "name": "减速器",
                    "code": "BK001",
                    "change_pct": 8.8,
                    "net_flow": 100,
                }],
            },
        },
    }

    context = _target_dimension_context("market_mainline", raw)

    assert context["direction_board_mapping"]["concept"] == [
        {
            "name": "减速器",
            "code": "BK001",
        },
    ]
    assert "target_board_flows" not in context
    assert "company_event_evidence" not in context


def test_structured_mainline_requires_a_defined_direction_not_market_flow() -> None:
    evaluator = MainlinePositionEvaluator()
    structured = CriterionEvidence(raw_data={
        "market_subject": {"investment_thesis": "减速器"},
        "market_mainline_report": {"report_pending": True},
        "thesis_membership": {"requested_domains": ["减速器"]},
    })
    no_direction = CriterionEvidence(raw_data={
        "market_subject": {},
        "market_mainline_report": {"report_pending": True},
    })

    assert evaluator.evidence_failure_reason(structured) is None
    assert "无法确定市场主线判断对象" in (
        evaluator.evidence_failure_reason(no_direction) or ""
    )


def test_competitiveness_rejects_failed_dict_payloads_as_available_sources() -> None:
    evaluator = IndustrialCompetitivenessEvaluator()
    evidence = CriterionEvidence(raw_data={
        "profile": {},
        "business_segments": {"success": False, "errors": ["timeout"]},
        "financials": {"success": False, "errors": ["timeout"]},
        "announcements": {"success": False, "errors": ["timeout"]},
        "formal_business_evidence": {
            "success": False,
            "errors": ["timeout"],
        },
    })

    assert "均获取失败" in (
        evaluator.evidence_failure_reason(evidence) or ""
    )


def test_dimension_context_uses_structured_financials_without_text_truncation() -> None:
    packet = {
        "profile": {"short_name": "测试公司", "main_business": "测试业务"},
        "financials": {
            "items": [
                {
                    "report_date": f"2025-{month:02d}-30",
                    "revenue": 100_000_000,
                    "parent_net_profit": 10_000_000,
                    "deducted_net_profit": 9_000_000,
                    "operating_cash_flow": 1_000_000,
                    "free_cash_flow": -2_000_000,
                    "accounts_receivable": 50_000_000,
                    "inventory": 40_000_000,
                    "debt_ratio": 30,
                }
                for month in (3, 6, 9, 12)
            ]
        },
        "valuation": {"pe_ttm": 12},
        "peer_comparison": {"dimensions": {"valuation": {"target_rank": 3}}},
        "risk_events": {"items": [{"title": "补充质押"}]},
    }

    risk = _dimension_company_context(packet, "major_risks")
    assert risk["financials"]["latest_full_year"]["year"] == 2025
    assert risk["financials"]["latest_full_year"]["revenue_亿元"] == 4
    assert (
        risk["financials"]["latest_full_year"]["operating_cash_flow_亿元"]
        == 0.04
    )
    assert risk["risk_events"]["items"][0]["title"] == "补充质押"

    valuation = _dimension_company_context(packet, "valuation_odds")
    assert valuation["valuation"]["pe_ttm"] == 12
    assert valuation["peer_valuation"]["target_rank"] == 3


def test_market_mainline_context_excludes_stock_timing_signals() -> None:
    context = _dimension_company_context(
        {
            "profile": {
                "short_name": "测试公司",
                "main_business": "正式披露的产业业务",
            },
            "quote": {"price": 10, "change_pct": -9},
            "technical": {"indicators": {"return_20d_pct": -30}},
            "capital_flow": {"windows": {"20d": {"main_net_inflow": -1}}},
        },
        "market_mainline",
    )

    assert context["profile"]["main_business"] == "正式披露的产业业务"
    assert "quote" not in context
    assert "technical" not in context
    assert "capital_flow" not in context


def test_market_mainline_evaluator_uses_the_frozen_batch_snapshot(
    monkeypatch,
) -> None:
    snapshot = {
        "snapshot_id": "shared-snapshot",
        "report_pending": False,
        "as_of_date": "2026-07-27",
        "overview": "测试主线",
        "market_stage": {"label": "主线形成"},
        "current_mainlines": [{
            "name": "国产算力",
            "branches": ["算力概念"],
        }],
        "future_mainlines": [],
    }
    monkeypatch.setattr(
        "src.services.buy_criteria.evaluators.mainline_position.DataService.get_market_mainline_report",
        lambda _self: (_ for _ in ()).throw(
            AssertionError("逐股分析不得重新读取数据库快照")
        ),
    )
    monkeypatch.setattr(
        "src.services.buy_criteria.evaluators.mainline_position.DataService.get_sector_list",
        lambda _self, _sector_type: {"items": []},
    )

    evidence = MainlinePositionEvaluator().collect_data(
        "000001",
        {
            "symbol": "000001",
            "name": "测试公司",
            "industry": "计算机",
            "_investment_thesis": "国产算力",
        },
        {"market_mainline_snapshot": snapshot},
    )

    assert evidence.raw_data["market_mainline_snapshot_source"] == (
        "frozen_batch_snapshot"
    )
    assert evidence.raw_data["market_mainline_snapshot_id"] == (
        "shared-snapshot"
    )
    assert evidence.raw_data["market_mainline_report"][
        "current_mainlines"
    ][0]["name"] == "国产算力"


def test_market_mainline_scope_only_never_checks_a_candidate_company(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "src.services.buy_criteria.evaluators.mainline_position.DataService.get_investment_thesis_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("共享主线闸门不得查询个股候选关系")
        ),
    )
    monkeypatch.setattr(
        "src.services.buy_criteria.evaluators.mainline_position.DataService.get_sector_list",
        lambda _self, _sector_type: {"items": []},
    )
    evidence = MainlinePositionEvaluator().collect_data(
        "MARKET_MAINLINE",
        {
            "symbol": "MARKET_MAINLINE",
            "name": "本批次结构化产业方向",
            "_scope_only": True,
            "_investment_thesis": "减速器",
            "_investment_thesis_context": {
                "summary": "减速器",
                "domains": [{
                    "label": "减速器",
                    "board_queries": ["减速器"],
                    "mapping_type": "catalog_binding",
                }],
            },
        },
        {
            "market_mainline_snapshot": {
                "snapshot_id": "shared",
                "as_of_date": "2026-07-29",
                "current_mainlines": [{"name": "人形机器人"}],
            },
        },
    )

    subject = evidence.raw_data["market_subject"]
    assert subject["scope_only"] is True
    assert subject["symbol"] is None
    assert "不含任何公司" in evidence.data_summary
    assert evidence.raw_data["thesis_membership"]["requested_domains"] == [
        "减速器"
    ]


def test_dimension_context_derives_latest_full_year_and_annual_yoy() -> None:
    items = []
    for year, revenue in ((2024, 100_000_000), (2025, 150_000_000)):
        for month in (3, 6, 9, 12):
            items.append({
                "report_date": f"{year}-{month:02d}-28",
                "report_period": f"{year}Q{month // 3}",
                "revenue": revenue,
                "parent_net_profit": 10_000_000,
                "deducted_net_profit": 9_000_000,
                "operating_cash_flow": 5_000_000,
                "free_cash_flow": 2_000_000,
            })
    items.append({
        "report_date": "2026-03-31",
        "report_period": "2026Q1",
        "revenue": 200_000_000,
        "revenue_yoy": 33.33,
    })

    context = _dimension_company_context(
        {"financials": {"items": items}},
        "industry_cycle",
    )

    latest = context["financials"]["latest_full_year"]
    assert latest["year"] == 2025
    assert latest["revenue_亿元"] == 6
    assert latest["revenue_yoy_pct"] == 50
    assert (
        context["financials"]["quarterly"][-1][
            "reported_period_revenue_yoy_pct"
        ]
        == 33.33
    )
    assert "annual_2025" not in context["financials"]


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
    with patch(
        "src.services.buy_criteria.professional_analysis.collect_professional_evidence",
        return_value=_evidence(),
    ), patch(
        "src.services.buy_criteria.professional_analysis._call_professional_model",
        return_value=(assessment, ""),
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
    with patch(
        "src.services.buy_criteria.professional_analysis.collect_professional_evidence",
        return_value=_evidence(),
    ), patch(
        "src.services.buy_criteria.professional_analysis._call_professional_model",
        return_value=(assessment, ""),
    ):
        result = analyze_professional_buy("300850")

    assert result["counts"]["pass"] == 8
    assert result["recommendation_code"] == "conditional_buy"
    assert result["gate_pass_complete"] is True
    assert result["coverage_complete"] is True
    assert result["stopped_at"] is None


def test_model_failure_marks_analysis_unavailable_and_skips_seven() -> None:
    with patch(
        "src.services.buy_criteria.professional_analysis.collect_professional_evidence",
        return_value=_evidence(),
    ), patch(
        "src.services.buy_criteria.professional_analysis._call_professional_model",
        return_value=(None, "TLS unavailable"),
    ):
        result = analyze_professional_buy("300850")

    assert result["recommendation_code"] == "analysis_unavailable"
    assert result["analysis_status"] == "execution_failed"
    assert result["final_decision"] == "分析失败"
    assert result["counts"]["insufficient"] == 1
    assert result["counts"]["not_evaluated"] == 7
    assert len(result["dimensions"]) == 8
    assert result["dimensions"][0]["status"] == "insufficient"
    assert all(
        item["status"] == "not_evaluated"
        for item in result["dimensions"][1:]
    )
