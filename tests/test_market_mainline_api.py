# -*- coding: utf-8 -*-

from unittest.mock import patch
from types import SimpleNamespace

import pytest

from src.services.market_theme._context import build_report_evidence_pack
from fastapi.testclient import TestClient

from api.app import create_app
import src.auth as auth
from src.services.market_theme_service import MarketThemeService
from src.services.market_theme._context import collect_context
from src.services.buy_criteria.data_service import DataService, _clear_cache
from src.storage import MarketMainlineReport


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


def test_market_mainline_route_returns_service_payload(client, monkeypatch):
    from api.v1.endpoints import market_themes

    monkeypatch.setattr(
        market_themes.MarketThemeService,
        "analyze",
        lambda self, force=False, use_llm=True: {
            "headline": "测试主线",
            "market_regime": "政策预期驱动",
            "primary_judgement": "这里是摘要",
            "investment_takeaway": "这里是提示",
            "policy_watchlist": ["政策观察点"],
            "current_themes": [],
            "next_themes": [],
            "source_notes": ["测试源"],
            "llm_used": False,
            "model_used": None,
            "generated_at": "2026-06-11 12:00:00 CST",
        },
    )

    response = client.get("/api/v1/market/mainline", params={"force": "true", "use_llm": "false"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["headline"] == "测试主线"
    assert payload["market_regime"] == "政策预期驱动"
    assert payload["llm_used"] is False


def test_market_mainline_storage_backfills_full_report_from_raw_response():
    row = MarketMainlineReport(
        report_key="market_mainline",
        as_of_date="2026-06-13",
        mode="llm",
        model_used="openai/glm-5.1",
        overview="摘要",
        market_stage_label="主升",
        market_stage_description="阶段说明",
        raw_response='{"full_report":"完整模型输出","overview":"摘要"}',
        payload='{"overview":"摘要"}',
    )

    payload = row.to_dict()

    assert payload["full_report"] == "完整模型输出"
    assert payload["overview"] == "摘要"


def test_market_mainline_storage_defaults_raw_stream_output():
    row = MarketMainlineReport(
        report_key="market_mainline",
        as_of_date="2026-06-13",
        mode="llm",
        model_used="openai/glm-5.1",
        overview="摘要",
        market_stage_label="主升",
        market_stage_description="阶段说明",
        raw_response='{"full_report":"完整模型输出","overview":"摘要"}',
        payload='{"overview":"摘要","full_report":"完整模型输出"}',
    )

    payload = row.to_dict()

    assert payload["raw_stream_output"] == '{"full_report":"完整模型输出","overview":"摘要"}'


def test_market_mainline_context_passes_explicit_sector_flow_period(monkeypatch):
    """Direct endpoint calls must not leak FastAPI Query defaults downstream."""
    from api.v1.endpoints import macro, market_status, sectors

    calls = []

    def fake_sector_flow(*, type, top_n, period):
        calls.append((type, top_n, period))
        return {"inflow_top": [], "outflow_top": []}

    monkeypatch.setattr(macro, "get_market_breadth", lambda: {})
    monkeypatch.setattr(macro, "get_sector_flow", fake_sector_flow)
    monkeypatch.setattr(market_status, "get_market_status", lambda force=False: {})
    monkeypatch.setattr(sectors, "get_sector_list", lambda type, force=False: {"items": []})

    payload = collect_context(force=False, include_rss=False)

    assert payload["source_snapshot"]["industry_flow"] == {
        "inflow_top": [],
        "outflow_top": [],
        "records": [],
    }
    assert calls == [
        ("industry", 30, "today"),
        ("concept", 30, "today"),
    ]


def test_buy_criteria_uses_current_evidence_while_daily_report_is_pending(monkeypatch):
    monkeypatch.setattr(
        MarketThemeService,
        "get_model_report",
        lambda self, force=False, trigger_generation=True: {
            "report_pending": True,
            "as_of_date": "2026-07-21",
            "current_mainlines": [],
        },
    )
    monkeypatch.setattr(
        MarketThemeService,
        "get_cached_evidence",
        lambda self: {
            "generated_at": "2026-07-21 10:00:00 CST",
            "data_time": "2026-07-21",
            "market_stage": {"label": "主线扩散期"},
            "current_themes": [{
                "name": "科技成长",
                "rank_label": "主线",
                "stage": "发酵期",
                "components": ["机器人"],
                "thesis": "产业与资金共振",
                "evidence": ["机器人板块走强"],
            }],
            "next_themes": [],
        },
    )
    _clear_cache()

    report = DataService().get_market_mainline_report()

    assert report["report_pending"] is False
    assert report["report_source"] == "current_evidence_fallback"
    assert report["current_mainlines"][0]["name"] == "科技成长"
    assert report["current_mainlines"][0]["branches"] == ["机器人"]


def test_report_evidence_pack_uses_the_public_feed_summarizer() -> None:
    context = {
        "generated_at": "2026-07-21T12:00:00+08:00",
        "source_snapshot": {
            "market_status": {"data_time": "2026-07-21"},
            "rss": {
                "market_news": {
                    "items": [{
                        "title": "人形机器人产业进展",
                        "summary": "<p>核心零部件进入验证阶段</p>",
                        "published": "2026-07-21",
                    }],
                },
            },
        },
    }
    packed = build_report_evidence_pack(context)
    assert packed["market_news"] == [{
        "evidence_id": "market_news:0",
        "title": "人形机器人产业进展",
        "summary": "核心零部件进入验证阶段",
        "published": "2026-07-21",
        "link": "",
    }]


def test_model_report_read_does_not_spawn_background_work_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.market_theme_service.get_latest_report",
        lambda _key: None,
    )
    service = MarketThemeService()

    def forbidden_submit(*, force=True):
        raise AssertionError("short-lived readers must not start the daily report task")

    monkeypatch.setattr(service, "submit_model_report_task", forbidden_submit)
    payload = service.get_model_report()
    assert payload["report_pending"] is True
