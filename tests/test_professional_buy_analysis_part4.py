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



"""Focused test slice 4; shared fixtures remain local to this slice."""

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
        request = __import__("json").loads(kwargs["messages"][1]["content"])
        captured.append(request)
        payload = _dimension(1, "fail").model_dump()
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
            precomputed_dimensions=(_dimension(0, "pass"),),
        )

    assert error == ""
    assert assessment is not None
    assert len(captured) == 1
    assert captured[0]["requested_dimension"]["dimension_id"] == ("industrial_competitiveness")
    assert captured[0]["prior_dimensions"][0]["dimension_id"] == ("market_mainline")

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
                "matched_items": [
                    {
                        "name": "减速器",
                        "code": "BK001",
                        "change_pct": 8.8,
                        "net_flow": 100,
                    }
                ],
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
    structured = CriterionEvidence(
        raw_data={
            "market_subject": {"investment_thesis": "减速器"},
            "market_mainline_report": {"report_pending": True},
            "thesis_membership": {"requested_domains": ["减速器"]},
        }
    )
    no_direction = CriterionEvidence(
        raw_data={
            "market_subject": {},
            "market_mainline_report": {"report_pending": True},
        }
    )

    assert evaluator.evidence_failure_reason(structured) is None
    assert "无法确定市场主线判断对象" in (evaluator.evidence_failure_reason(no_direction) or "")

def test_competitiveness_rejects_failed_dict_payloads_as_available_sources() -> None:
    evaluator = IndustrialCompetitivenessEvaluator()
    evidence = CriterionEvidence(
        raw_data={
            "profile": {},
            "business_segments": {"success": False, "errors": ["timeout"]},
            "financials": {"success": False, "errors": ["timeout"]},
            "announcements": {"success": False, "errors": ["timeout"]},
            "formal_business_evidence": {
                "success": False,
                "errors": ["timeout"],
            },
        }
    )

    assert "均获取失败" in (evaluator.evidence_failure_reason(evidence) or "")

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
    assert risk["financials"]["latest_full_year"]["operating_cash_flow_亿元"] == 0.04
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
        "current_mainlines": [
            {
                "name": "国产算力",
                "branches": ["算力概念"],
            }
        ],
        "future_mainlines": [],
    }
    monkeypatch.setattr(
        "src.services.buy_criteria.evaluators.mainline_position.DataService.get_market_mainline_report",
        lambda _self: (_ for _ in ()).throw(AssertionError("逐股分析不得重新读取数据库快照")),
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

    assert evidence.raw_data["market_mainline_snapshot_source"] == ("frozen_batch_snapshot")
    assert evidence.raw_data["market_mainline_snapshot_id"] == ("shared-snapshot")
    assert evidence.raw_data["market_mainline_report"]["current_mainlines"][0]["name"] == "国产算力"

def test_market_mainline_scope_only_never_checks_a_candidate_company(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "src.services.buy_criteria.evaluators.mainline_position.DataService.get_investment_thesis_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("共享主线闸门不得查询个股候选关系")),
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
                "domains": [
                    {
                        "label": "减速器",
                        "board_queries": ["减速器"],
                        "mapping_type": "catalog_binding",
                    }
                ],
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
    assert evidence.raw_data["thesis_membership"]["requested_domains"] == ["减速器"]

def test_dimension_context_derives_latest_full_year_and_annual_yoy() -> None:
    items = []
    for year, revenue in ((2024, 100_000_000), (2025, 150_000_000)):
        for month in (3, 6, 9, 12):
            items.append(
                {
                    "report_date": f"{year}-{month:02d}-28",
                    "report_period": f"{year}Q{month // 3}",
                    "revenue": revenue,
                    "parent_net_profit": 10_000_000,
                    "deducted_net_profit": 9_000_000,
                    "operating_cash_flow": 5_000_000,
                    "free_cash_flow": 2_000_000,
                }
            )
    items.append(
        {
            "report_date": "2026-03-31",
            "report_period": "2026Q1",
            "revenue": 200_000_000,
            "revenue_yoy": 33.33,
        }
    )

    context = _dimension_company_context(
        {"financials": {"items": items}},
        "industry_cycle",
    )

    latest = context["financials"]["latest_full_year"]
    assert latest["year"] == 2025
    assert latest["revenue_亿元"] == 6
    assert latest["revenue_yoy_pct"] == 50
    assert context["financials"]["quarterly"][-1]["reported_period_revenue_yoy_pct"] == 33.33
    assert "annual_2025" not in context["financials"]
