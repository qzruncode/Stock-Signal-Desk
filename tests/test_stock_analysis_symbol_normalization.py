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


def test_financials_accepts_ts_code_suffix(client, monkeypatch):
    seen = {}

    def fake_fetch(symbol: str, periods: int, use_cache: bool):
        seen["symbol"] = symbol
        seen["periods"] = periods
        return {
            "symbol": symbol,
            "periods": periods,
            "items": [{"report_date": "2025-12-31", "revenue": 1.0}],
            "_fetched_at": "2026-06-08T00:00:00",
            "_cached": False,
            "source": "fake",
        }

    monkeypatch.setattr("src.tools.get_financials.get_financials", fake_fetch)

    response = client.get("/api/v1/stocks/financials", params={"symbol": "300850.SZ", "force": True})

    assert response.status_code == 200
    assert response.json()["symbol"] == "300850"
    assert seen == {"symbol": "300850", "periods": 6}


def test_financial_statements_accepts_ts_code_suffix(client, monkeypatch):
    seen = {}

    def fake_fetch(symbol: str, periods: int, use_cache: bool):
        seen["symbol"] = symbol
        seen["periods"] = periods
        return {
            "symbol": symbol,
            "periods": periods,
            "balance_sheet": [{"report_date": "2025-12-31", "total_assets": 1.0}],
            "income_statement": [],
            "cashflow": [],
            "_fetched_at": "2026-06-08T00:00:00",
            "_cached": False,
            "source": "fake",
        }

    monkeypatch.setattr("src.tools._financial_statements.get_financial_statements", fake_fetch)

    response = client.get("/api/v1/stocks/financials/statements", params={"symbol": "300850.SZ", "force": True})

    assert response.status_code == 200
    assert response.json()["symbol"] == "300850"
    assert seen == {"symbol": "300850", "periods": 6}


def test_news_uses_direct_fallback_when_rsshub_is_empty(client, monkeypatch):
    import pandas as pd

    monkeypatch.setattr("src.tools.search_news._stock_name", lambda symbol: "新强联")
    monkeypatch.setattr(
        "src.tools.search_news._fetch_direct",
        lambda symbol: pd.DataFrame([{
            "新闻标题": "新强联直连新闻",
            "新闻内容": "新强联 300850 生产经营正常",
            "发布时间": "2026-07-15T00:00:00",
            "文章来源": "东方财富新闻",
            "新闻链接": "https://example.com/news",
        }]),
    )
    monkeypatch.setattr(
        "src.tools.search_news._fetch_rss",
        lambda name, limit: {"items": [], "errors": ["rss timeout"], "_cached": False},
    )

    response = client.get("/api/v1/stocks/news", params={"symbol": "300850.SZ", "source": "news", "force": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["title"] == "新强联直连新闻"
    assert payload["partial"] is True
    assert payload["fallback_used"] is False


def test_announcements_uses_rsshub_fallback_when_direct_source_is_empty(client, monkeypatch):
    import importlib

    tool_module = importlib.import_module("src.tools.get_announcements")
    monkeypatch.setattr(tool_module, "cached_call", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(tool_module, "_fetch_exchange_rss", lambda *args, **kwargs: (
        [{
            "代码": "300850",
            "名称": "新强联",
            "公告标题": "交易所公告",
            "公告类型": "交易所公告",
            "公告日期": "2026-06-08",
            "网址": "https://example.com/announcement",
        }],
        "/szse/disclosure/listed/notice/:query?",
        [],
    ))

    response = client.get("/api/v1/stocks/announcements", params={"symbol": "300850.SZ", "force": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["title"] == "交易所公告"
    assert payload["source_chain"] == [
        "RSSHub/交易所官方披露:/szse/disclosure/listed/notice/:query?"
    ]
    assert payload["fallback_used"] is True


def test_announcements_return_notice_type_distribution(client, monkeypatch):
    import importlib

    tool_module = importlib.import_module("src.tools.get_announcements")
    monkeypatch.setattr(tool_module, "get_announcements", lambda *args, **kwargs: {
        "symbol": "300850",
        "items": [],
        "item_count": 4,
        "analysis": {
            "notice_type_distribution": {"分红": 2, "业绩": 1, "高管变动": 1},
        },
        "success": True,
    })

    response = client.get("/api/v1/stocks/announcements", params={"symbol": "300850.SZ", "force": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"]["notice_type_distribution"] == {
        "分红": 2,
        "业绩": 1,
        "高管变动": 1,
    }


def test_research_uses_rsshub_fallback_when_direct_source_is_empty(client, monkeypatch):
    import importlib

    tool_module = importlib.import_module("src.tools.get_research_report")
    monkeypatch.setattr(tool_module, "cached_call", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(tool_module, "_fetch_rss_fallback", lambda *args, **kwargs: ([{
        "symbol": "300850",
        "name": "新强联",
        "title": "RSSHub研报",
        "org": "RSSHub研报源",
        "rating": None,
        "industry": None,
        "publish_date": "2026-06-08",
        "url": "https://example.com/report",
        "summary": "",
        "profit_forecasts": [],
        "monthly_report_count": None,
        "source": "RSSHub/东方财富个股研报",
        "source_type": "rss_research_report",
    }], []))

    response = client.get("/api/v1/stocks/research-report", params={"symbol": "300850.SZ", "force": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["title"] == "RSSHub研报"
    assert payload["source_chain"] == ["RSSHub/东方财富个股研报"]
    assert payload["fallback_used"] is True
