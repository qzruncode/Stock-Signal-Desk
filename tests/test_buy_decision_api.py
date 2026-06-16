# -*- coding: utf-8 -*-

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
import src.auth as auth
from src.services.buy_decision_workbench_service import BuyDecisionWorkbenchService


def _legacy_report(
    *,
    analysis_status: str = "观察",
    beneficiary_level: str = "直接受益",
    mainline_passed: bool = True,
    beta_passed: bool = True,
    killer_reason: str | None = None,
    prosperity_score: int = 72,
    valuation_status: str = "medium",
    high_risk_count: int = 0,
    medium_risk_count: int = 1,
):
    return {
        "symbol": "688012",
        "model_used": "test-model",
        "raw_stream_output": "{\"ok\": true}",
        "industry_cycle": {
            "analysis_status": analysis_status,
            "beneficiary_level": beneficiary_level,
            "beneficiary_reason": "主营与行业景气方向一致。",
            "cycle_phase": "发酵期",
            "prosperity_score": prosperity_score,
            "prosperity_judgement": "行业景气有改善迹象。",
            "core_logic": "行业 beta 和主营受益都具备跟踪价值。",
            "killer_reason": killer_reason,
            "catalysts": ["订单验证", "行业催化"],
            "observation_points": ["等待回调", "观察催化兑现"],
            "mainline_detector": {
                "passed": mainline_passed,
                "conclusion": "主线属性成立" if mainline_passed else "主线属性不足",
                "failed_reason": None if mainline_passed else "当前不属于市场主线 / 分支主线",
                "checklist": [{"item": "当前属于市场主线 / 分支主线", "passed": mainline_passed, "reason": "匹配主题。", "source": "market"}],
            },
            "industry_beta_detector": {
                "passed": beta_passed,
                "conclusion": "行业 beta 成立" if beta_passed else "行业 beta 不足",
                "failed_reason": None if beta_passed else "行业处于上升周期证据不足",
                "checklist": [{"item": "行业处于上升周期", "passed": beta_passed, "reason": "板块景气改善。", "source": "sector"}],
            },
            "evidence": {
                "market_mainline": {
                    "matched_current_mainlines": [{"name": "科技成长"}] if mainline_passed else [],
                    "matched_future_mainlines": [],
                },
                "sector_snapshot": {"change_pct": 1.2, "rank": 8, "total": 86},
                "fund_flow": {"rank": 6, "total": 86, "main_net_inflow": 9.5},
                "peer_group": {"sample_size": 10, "sample_names": ["A", "B"]},
                "financial_snapshot": {"revenue_yoy": 18.0, "profit_yoy": 15.0},
                "valuation_snapshot": {
                    "pe_ttm": 36.2,
                    "price_overdraft_status": valuation_status,
                    "price_overdraft_score": 78.0 if valuation_status == "high" else 61.0 if valuation_status == "medium" else 24.0,
                    "reasoning": ["估值溢价较高"] if valuation_status != "low" else ["估值压力可控"],
                },
                "stock_focus_snapshot": {
                    "focus_view": "主营受益明确",
                    "business_binding_strength": "强",
                    "finance_state": "healthy",
                    "holder_state": "stable",
                    "trading_state": "active",
                    "direct_evidence_strength": "高",
                    "finance_points": ["收入增速改善"],
                    "holder_points": ["机构持仓稳定"],
                    "trading_points": ["成交活跃"],
                },
                "sentiment_snapshot": {
                    "news_count": 10,
                    "research_count": 6,
                    "positive_research_count": 4,
                    "sentiment_score": 72,
                    "social_score": 67,
                    "discussion_count": 1200,
                },
                "risk_snapshot": {
                    "high_risk_count": high_risk_count,
                    "medium_risk_count": medium_risk_count,
                    "top_risk_labels": ["竞争加剧"] if high_risk_count or medium_risk_count else [],
                },
                "driver_signals": {"technology": ["国产替代"]},
            },
        },
    }


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False):
        yield
    auth._auth_enabled = None


@pytest.fixture(autouse=True)
def patch_stock_info(monkeypatch):
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_stock_info",
        lambda symbol, force=False: {
            "symbol": symbol,
            "short_name": "测试股份",
            "name": "测试股份",
            "industry": "半导体设备",
            "main_business": "半导体设备与零部件",
            "market": "沪市主板",
        },
    )


@pytest.fixture(autouse=True)
def patch_legacy_industry_cycle(monkeypatch):
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.IndustryCycleService.get_report",
        lambda self, symbol, force=False: _legacy_report(),
    )


@pytest.fixture(autouse=True)
def patch_buy_decision_auxiliary_sources(monkeypatch):
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_valuation_ratios",
        lambda symbol, with_history=True, force=False: {
            "symbol": symbol,
            "pe_ttm": 36.2,
            "pb": 4.8,
            "industry_average": {"pe": 29.5, "pb": 3.2},
            "price_overdraft_signal": {"status": "medium", "score": 61.0, "reasoning": ["估值溢价较高"]},
        },
    )
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_price_overdraft_signal",
        lambda symbol, force=False: {
            "symbol": symbol,
            "price_overdraft_signal": {"status": "medium", "score": 61.0, "reasoning": ["估值溢价较高"]},
        },
    )
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_shareholder_structure",
        lambda symbol, force=False: {
            "symbol": symbol,
            "institution_holding_pct": 22.6,
            "holder_count": 18234,
            "actual_controller": "测试实控人",
        },
    )
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_sentiment",
        lambda symbol, days=90, force=False: {
            "symbol": symbol,
            "sentiment_score": 72,
            "items": [1] * 10,
            "research_items": [1] * 6,
        },
    )
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_social_sentiment",
        lambda symbol, days=90, force=False: {
            "symbol": symbol,
            "overall_score": 67,
            "total_discussion": 1200,
        },
    )
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_risk_events",
        lambda symbol, days=90, force=False: {
            "symbol": symbol,
            "analysis": {
                "severity_distribution": {"high": 0, "medium": 1},
                "top_risk_labels": ["竞争加剧"],
            },
        },
    )


def test_buy_decision_workbench_init_returns_session(client):
    response = client.post(
        "/api/v1/stocks/buy-decision/workbench",
        json={"symbol": "688012", "force_reset": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "688012"
    assert payload["stock_name"] == "测试股份"
    assert payload["current_step"] == "profile_mapping"
    assert payload["steps"]["profile_mapping"]["status"] == "idle"


def test_buy_decision_run_steps_and_get_report(client):
    init_response = client.post(
        "/api/v1/stocks/buy-decision/workbench",
        json={"symbol": "688012", "force_reset": True},
    )
    session_id = init_response.json()["session_id"]

    for step in (
        "profile_mapping",
        "industry_beta",
        "mainline_position",
        "company_benefit",
        "buy_constraints",
        "final_decision",
    ):
        step_response = client.post(
            f"/api/v1/stocks/buy-decision/workbench/{session_id}/steps/{step}/run",
            json={"force": False},
        )
        assert step_response.status_code == 200
        assert step_response.json()["status"] == "success"

    report_response = client.get(f"/api/v1/stocks/buy-decision/workbench/{session_id}/report")
    assert report_response.status_code == 200
    payload = report_response.json()
    assert payload["symbol"] == "688012"
    assert payload["buy_decision"]["decision"] == "可跟踪等待"
    assert payload["industry_cycle"]["analysis_status"] == "观察"


def test_buy_decision_prevents_skipping_previous_steps(client):
    init_response = client.post(
        "/api/v1/stocks/buy-decision/workbench",
        json={"symbol": "688012", "force_reset": True},
    )
    session_id = init_response.json()["session_id"]

    response = client.post(
        f"/api/v1/stocks/buy-decision/workbench/{session_id}/steps/final_decision/run",
        json={"force": False},
    )
    assert response.status_code == 400
    payload = response.json()
    detail = payload.get("detail", payload)
    assert detail["error"] == "invalid_step"
    assert "previous step not completed" in detail["message"]


def test_buy_decision_service_returns_buy_when_detectors_and_risks_are_clean(monkeypatch):
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.IndustryCycleService.get_report",
        lambda self, symbol, force=False: _legacy_report(
            analysis_status="主线",
            beneficiary_level="核心受益",
            mainline_passed=True,
            beta_passed=True,
            prosperity_score=84,
            valuation_status="low",
            high_risk_count=0,
            medium_risk_count=0,
        ),
    )
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_valuation_ratios",
        lambda symbol, with_history=True, force=False: {
            "symbol": symbol,
            "pe_ttm": 28.1,
            "pb": 3.1,
            "industry_average": {"pe": 29.5, "pb": 3.2},
            "price_overdraft_signal": {"status": "low", "score": 24.0, "reasoning": ["估值压力可控"]},
        },
    )
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_price_overdraft_signal",
        lambda symbol, force=False: {
            "symbol": symbol,
            "price_overdraft_signal": {"status": "low", "score": 24.0, "reasoning": ["估值压力可控"]},
        },
    )
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_risk_events",
        lambda symbol, days=90, force=False: {
            "symbol": symbol,
            "analysis": {"severity_distribution": {"high": 0, "medium": 0}, "top_risk_labels": []},
        },
    )

    service = BuyDecisionWorkbenchService()
    session_id = service.create_session("688012", force_reset=True)["session_id"]
    for step in ("profile_mapping", "industry_beta", "mainline_position", "company_benefit", "buy_constraints", "final_decision"):
        service.run_step(session_id, step)

    report = service.get_report(session_id)
    assert report["buy_decision"]["decision"] == "可买入"
    assert report["buy_decision"]["entry_type"] == "趋势跟随"


def test_buy_decision_service_blocks_chasing_when_high_valuation(monkeypatch):
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.IndustryCycleService.get_report",
        lambda self, symbol, force=False: _legacy_report(
            analysis_status="分支主线",
            beneficiary_level="直接受益",
            mainline_passed=True,
            beta_passed=True,
            prosperity_score=76,
            valuation_status="high",
            high_risk_count=0,
            medium_risk_count=1,
        ),
    )
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_valuation_ratios",
        lambda symbol, with_history=True, force=False: {
            "symbol": symbol,
            "pe_ttm": 48.5,
            "pb": 6.2,
            "industry_average": {"pe": 29.5, "pb": 3.2},
            "price_overdraft_signal": {"status": "high", "score": 78.0, "reasoning": ["估值明显透支"]},
        },
    )
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_price_overdraft_signal",
        lambda symbol, force=False: {
            "symbol": symbol,
            "price_overdraft_signal": {"status": "high", "score": 78.0, "reasoning": ["估值明显透支"]},
        },
    )

    service = BuyDecisionWorkbenchService()
    session_id = service.create_session("688012", force_reset=True)["session_id"]
    for step in ("profile_mapping", "industry_beta", "mainline_position", "company_benefit", "buy_constraints", "final_decision"):
        service.run_step(session_id, step)

    report = service.get_report(session_id)
    assert report["buy_decision"]["decision"] == "禁止追高"
    assert report["buy_decision"]["entry_type"] == "等待分歧"


def test_buy_decision_service_returns_not_buy_when_cycle_is_fading(monkeypatch):
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.IndustryCycleService.get_report",
        lambda self, symbol, force=False: _legacy_report(
            analysis_status="退潮",
            beneficiary_level="待验证",
            mainline_passed=False,
            beta_passed=False,
            killer_reason="公司受益路径证据不足",
            prosperity_score=38,
            valuation_status="medium",
            high_risk_count=1,
            medium_risk_count=2,
        ),
    )
    monkeypatch.setattr(
        "src.services.buy_decision_workbench_service.get_risk_events",
        lambda symbol, days=90, force=False: {
            "symbol": symbol,
            "analysis": {
                "severity_distribution": {"high": 1, "medium": 2},
                "top_risk_labels": ["订单下修", "竞争加剧"],
            },
        },
    )

    service = BuyDecisionWorkbenchService()
    session_id = service.create_session("688012", force_reset=True)["session_id"]
    for step in ("profile_mapping", "industry_beta", "mainline_position", "company_benefit", "buy_constraints", "final_decision"):
        service.run_step(session_id, step)

    report = service.get_report(session_id)
    assert report["buy_decision"]["decision"] == "暂不买入"
    assert "公司受益路径证据不足" in report["buy_decision"]["not_buy_reasons"]
