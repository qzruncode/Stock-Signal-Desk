# -*- coding: utf-8 -*-

from unittest.mock import patch
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
import src.auth as auth
from src.services.market_theme_service import MarketThemeService
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


