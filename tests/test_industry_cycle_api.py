# -*- coding: utf-8 -*-

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
import src.auth as auth


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


def test_industry_cycle_route_returns_service_payload(client, monkeypatch):
    from api.v1.endpoints import industry_cycle

    monkeypatch.setattr(
        industry_cycle.IndustryCycleService,
        "analyze",
        lambda self, symbol, force=False: {
            "symbol": symbol,
            "industry_cycle": {
                "stock_name": "测试股份",
                "industry_name": "半导体设备",
                "analysis_status": "分支主线",
                "beneficiary_level": "核心受益",
                "beneficiary_reason": "主营关键词与主线分支重合。",
                "cycle_phase": "发酵期",
                "cycle_phase_reason": "资金和消息面都在持续强化。",
                "prosperity_score": 78,
                "prosperity_judgement": "行业仍处于景气上行区间。",
                "core_logic": "当前映射主线，且主营真实受益。",
                "killer_reason": None,
                "observation_window": "未来 6-12 个月",
                "catalysts": ["订单持续验证"],
                "risks": ["估值偏高"],
                "observation_points": ["观察订单兑现"],
                "mainline_detector": {
                    "passed": True,
                    "conclusion": "主线属性成立。",
                    "failed_reason": None,
                    "checklist": [],
                },
                "industry_beta_detector": {
                    "passed": True,
                    "conclusion": "行业 beta 成立。",
                    "failed_reason": None,
                    "checklist": [],
                },
                "evidence": {
                    "market_mainline": {
                        "report_pending": False,
                        "market_stage": {"label": "结构轮动"},
                        "matched_current_mainlines": [],
                        "matched_future_mainlines": [],
                        "current_theme_detail": {},
                        "future_theme_detail": {},
                    },
                    "sector_snapshot": {"rank": 5, "total": 86},
                    "fund_flow": {"rank": 3, "total": 86},
                    "peer_group": {"sample_size": 12, "sample_names": ["A", "B"]},
                    "valuation_snapshot": {"pe_ttm": 38.2, "price_overdraft_status": "watch"},
                    "sentiment_snapshot": {"news_count": 8},
                    "risk_snapshot": {"high_risk_count": 0},
                    "driver_signals": {"technology": ["国产替代"]},
                },
            },
            "_fetched_at": "2026-06-14T12:00:00",
            "_cached": False,
            "fallback_used": False,
        },
    )

    response = client.get("/api/v1/stocks/industry-cycle", params={"symbol": "688012", "force": "true"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "688012"
    assert payload["industry_cycle"]["analysis_status"] == "分支主线"
    assert payload["industry_cycle"]["beneficiary_level"] == "核心受益"
    assert payload["industry_cycle"]["cycle_phase"] == "发酵期"
    assert payload["industry_cycle"]["evidence"]["valuation_snapshot"]["pe_ttm"] == 38.2


def test_industry_cycle_report_route_returns_cached_payload(client, monkeypatch):
    from api.v1.endpoints import industry_cycle

    monkeypatch.setattr(
        industry_cycle.IndustryCycleService,
        "get_report",
        lambda self, symbol, force=False: {
            "symbol": symbol,
            "industry_cycle": None,
            "report_pending": True,
            "_cached": False,
        },
    )

    response = client.get("/api/v1/stocks/industry-cycle/report", params={"symbol": "600519"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "600519"
    assert payload["report_pending"] is True


def test_industry_cycle_report_task_route_returns_task_payload(client, monkeypatch):
    from types import SimpleNamespace
    from api.v1.endpoints import industry_cycle

    monkeypatch.setattr(
        industry_cycle.IndustryCycleService,
        "submit_report_task",
        lambda self, symbol, force=True: SimpleNamespace(
            task_id="task-industry-cycle-1",
            status=SimpleNamespace(value="pending"),
            message="行业周期模型研判任务已提交",
        ),
    )

    response = client.post("/api/v1/stocks/industry-cycle/report/tasks", params={"symbol": "600519", "force": "true"})

    assert response.status_code == 202
    payload = response.json()
    assert payload["task_id"] == "task-industry-cycle-1"
    assert payload["status"] == "pending"


def test_parse_llm_json_payload_accepts_missing_outer_braces():
    from src.services.industry_cycle_service import _parse_llm_json_payload

    parsed = _parse_llm_json_payload(
        '"analysis_status":"观察","prosperity_score":12,"core_logic":"主营仍待验证"',
        "",
    )

    assert parsed is not None
    assert parsed["analysis_status"] == "观察"
    assert parsed["prosperity_score"] == 12


def test_build_llm_industry_cycle_report_falls_back_when_model_returns_non_json(monkeypatch):
    from src.services import industry_cycle_service as service_module

    monkeypatch.setattr(
        service_module,
        "_cache_put",
        lambda symbol, payload: None,
    )
    monkeypatch.setattr(
        service_module,
        "_report_cache_put",
        lambda symbol, payload: None,
    )

    class _Analyzer:
        @staticmethod
        def is_available():
            return True

    monkeypatch.setattr("src.analyzer.get_analyzer", lambda: _Analyzer())
    monkeypatch.setattr(
        "src.ai_caller.call_ai_structured",
        lambda *args, **kwargs: (
            '"reason":"风电装机需求增长驱动","prosperity_judgement":"景气改善中"',
            "openai/glm-5.1",
            {},
        ),
    )

    service = service_module.IndustryCycleService()
    evidence_pack = {
        "stock_name": "测试股份",
        "industry_name": "风电设备",
        "mainline_context": {"report_pending": True},
        "company_specific_evidence": {"financial_snapshot": {"revenue_yoy": 18.5}},
        "industry_beta_evidence": {
            "sector_snapshot": {"rank": 6},
            "fund_flow_snapshot": {"rank": 3},
            "peer_snapshot": {"sample_size": 8},
        },
        "supporting_judgement": {
            "data_quality": {"summary": "部分证据缺口"},
            "driver_clues": {"demand": ["装机增长"]},
        },
    }
    evidence_bundle = {
        "market_report": {"report_pending": True, "current_mainlines": [], "future_mainlines": []},
        "market_evidence": {"market_stage": {}, "current_themes": [], "next_themes": []},
        "valuation_snapshot": {"pe_ttm": 32.1},
        "sentiment_snapshot": {"news_count": 4},
        "risk_snapshot": {"high_risk_count": 0},
    }

    payload = service._build_llm_industry_cycle_report(
        symbol="300850",
        evidence_bundle=evidence_bundle,
        evidence_pack=evidence_pack,
        system_prompt="system",
        user_prompt="user",
        on_text=None,
    )

    assert payload["report_pending"] is False
    assert payload["fallback_used"] is True
    assert payload["model_used"] == "openai/glm-5.1"
    assert payload["industry_cycle"]["analysis_status"] == "观察"
    assert "格式异常" in (payload["industry_cycle"]["killer_reason"] or "")


def test_build_llm_industry_cycle_report_disables_structured_json_validator(monkeypatch):
    from src.services import industry_cycle_service as service_module

    monkeypatch.setattr("src.analyzer.get_analyzer", lambda: type("Analyzer", (), {"is_available": staticmethod(lambda: True)})())

    captured = {}

    def _fake_call_ai_structured(*args, **kwargs):
        captured["validator"] = kwargs.get("response_validator")
        return ('{"analysis_status":"观察","prosperity_score":10,"prosperity_judgement":"观察","core_logic":"测试","mainline_detector":{},"industry_beta_detector":{}}', "openai/glm-5.1", {})

    monkeypatch.setattr("src.ai_caller.call_ai_structured", _fake_call_ai_structured)
    monkeypatch.setattr(service_module, "_cache_put", lambda symbol, payload: None)
    monkeypatch.setattr(service_module, "_report_cache_put", lambda symbol, payload: None)

    service = service_module.IndustryCycleService()
    payload = service._build_llm_industry_cycle_report(
        symbol="300850",
        evidence_bundle={
            "market_report": {},
            "market_evidence": {},
            "sector_snapshot": {},
            "fund_flow_snapshot": {},
            "peer_snapshot": {},
            "valuation_snapshot": {},
            "sentiment_snapshot": {},
            "risk_snapshot": {},
            "stock_info": {},
        },
        evidence_pack={
            "stock_name": "测试股份",
            "industry_name": "风电设备",
            "supporting_judgement": {"data_quality": {}, "driver_clues": {}},
        },
        system_prompt="system",
        user_prompt="user",
        on_text=None,
    )

    assert callable(captured["validator"])
    captured["validator"]("not json")
    assert payload["industry_cycle"]["analysis_status"] == "观察"
