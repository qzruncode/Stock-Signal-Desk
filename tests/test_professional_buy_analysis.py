from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.services.buy_criteria.professional_analysis import (
    ANALYST_SYSTEM_PROMPT,
    DIMENSION_DEFINITIONS,
    DimensionAssessment,
    OverallAssessment,
    ProfessionalAssessment,
    _call_professional_model,
    _dimension_company_context,
    _inline_json_schema,
    _repair_incomplete_dimension_headline,
    _redact_unsupported_numeric_claims,
    _unsupported_numeric_claims,
    analyze_professional_buy,
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
        headline=f"{title}结论",
        analysis=f"{title}已经同时核验支持证据和主要反证。",
        key_evidence=[f"{title}支持事实"],
        counter_evidence=[f"{title}保留因素"],
        monitoring_points=[f"继续跟踪{title}"],
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
    assert "1—3年结构性产业趋势" in ANALYST_SYSTEM_PROMPT
    assert "1—6个月A股主导叙事" in ANALYST_SYSTEM_PROMPT
    assert "1—10日板块交易确认" in ANALYST_SYSTEM_PROMPT
    assert "个股涨跌、均线和资金流不能否定业务的主线归属" in ANALYST_SYSTEM_PROMPT
    assert "detected 只证明曾出现过" in ANALYST_SYSTEM_PROMPT
    assert "不能笼统写“证据不足”" in ANALYST_SYSTEM_PROMPT


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
    assert "未核验数值" in redacted.analysis
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
    assert "未核验数值" in redacted.monitoring_points[0]
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


def test_model_failure_marks_first_gate_insufficient_and_skips_seven() -> None:
    with patch(
        "src.services.buy_criteria.professional_analysis.collect_professional_evidence",
        return_value=_evidence(),
    ), patch(
        "src.services.buy_criteria.professional_analysis._call_professional_model",
        return_value=(None, "TLS unavailable"),
    ):
        result = analyze_professional_buy("300850")

    assert result["recommendation_code"] == "evidence_insufficient"
    assert result["counts"]["insufficient"] == 1
    assert result["counts"]["not_evaluated"] == 7
    assert len(result["dimensions"]) == 8
    assert result["dimensions"][0]["status"] == "insufficient"
    assert all(
        item["status"] == "not_evaluated"
        for item in result["dimensions"][1:]
    )
