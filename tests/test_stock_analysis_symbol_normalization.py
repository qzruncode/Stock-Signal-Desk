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


def test_stock_info_accepts_ts_code_suffix(client, monkeypatch):
    from api.v1.endpoints import stock_info

    seen = {}

    def fake_cninfo(symbol: str):
        seen["cninfo"] = symbol
        return {"name": "测试公司", "_cninfo_ok": True}

    def fake_em(symbol: str):
        seen["em"] = symbol
        return {"_em_ok": True}

    monkeypatch.setattr(stock_info, "_fetch_from_cninfo", fake_cninfo)
    monkeypatch.setattr(stock_info, "_fetch_from_em", fake_em)

    response = client.get("/api/v1/stocks/info", params={"symbol": "300850.SZ", "force": True})

    assert response.status_code == 200
    assert response.json()["symbol"] == "300850"
    assert seen == {"cninfo": "300850", "em": "300850"}


def test_financials_accepts_ts_code_suffix(client, monkeypatch):
    from api.v1.endpoints import financials

    seen = {}

    def fake_fetch(symbol: str, periods: int):
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

    monkeypatch.setattr(financials, "_fetch_financials", fake_fetch)

    response = client.get("/api/v1/stocks/financials", params={"symbol": "300850.SZ", "force": True})

    assert response.status_code == 200
    assert response.json()["symbol"] == "300850"
    assert seen == {"symbol": "300850", "periods": 12}


def test_financial_statements_accepts_ts_code_suffix(client, monkeypatch):
    from api.v1.endpoints import financials

    seen = {}

    def fake_fetch(symbol: str, periods: int):
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

    monkeypatch.setattr(financials, "_fetch_financial_statements", fake_fetch)

    response = client.get("/api/v1/stocks/financials/statements", params={"symbol": "300850.SZ", "force": True})

    assert response.status_code == 200
    assert response.json()["symbol"] == "300850"
    assert seen == {"symbol": "300850", "periods": 12}


def test_news_uses_direct_fallback_when_rsshub_is_empty(client, monkeypatch):
    from api.v1.endpoints import financials

    monkeypatch.setattr(financials, "_fetch_rss_stock_news", lambda symbol, days: ([], [], ["rss timeout"]))
    monkeypatch.setattr(
        financials,
        "_fetch_direct_news_sources",
        lambda symbol, days: (
            [{
                "title": "直连新闻",
                "summary": "fallback",
                "publish_time": "2026-06-08T00:00:00",
                "source": "东方财富新闻",
                "url": "https://example.com/news",
                "category": "新闻",
                "event_type": "general",
                "event_label": "一般资讯",
                "polarity": "neutral",
                "importance": "low",
                "tags": [],
            }],
            ["东方财富新闻直连(1条)"],
            [],
        ),
    )

    response = client.get("/api/v1/stocks/news", params={"symbol": "300850.SZ", "source": "news", "force": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["title"] == "直连新闻"
    assert payload["fallback_used"] is True


def test_announcements_uses_rsshub_fallback_when_direct_source_is_empty(client, monkeypatch):
    from api.v1.endpoints import financials

    class _EmptyFrame:
        empty = True

    import akshare as ak

    monkeypatch.setattr(ak, "stock_individual_notice_report", lambda **kwargs: _EmptyFrame())
    monkeypatch.setattr(
        financials,
        "_fetch_rss_announcements",
        lambda symbol, days, ann_type: (
            [{
                "title": "交易所公告",
                "notice_type": "公告",
                "publish_date": "2026-06-08",
                "url": "https://example.com/announcement",
                "source": "深交所公告",
            }],
            ["RSSHub深交所公告(1条)"],
            [],
        ),
    )

    response = client.get("/api/v1/stocks/announcements", params={"symbol": "300850.SZ", "force": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["title"] == "交易所公告"
    assert payload["source_chain"] == ["RSSHub深交所公告(1条)"]
    assert payload["fallback_used"] is True


def test_announcements_return_notice_type_distribution(client, monkeypatch):
    from api.v1.endpoints import financials

    monkeypatch.setattr(
        financials,
        "_fetch_announcements",
        lambda symbol, days, ann_type: {
            "symbol": symbol,
            "days": days,
            "type": ann_type,
            "items": [
                {"title": "年报", "notice_type": "定期报告", "publish_date": "2026-06-08", "url": "", "event_type": "earnings", "importance": "medium", "polarity": "neutral", "tags": []},
                {"title": "分红方案", "notice_type": "分红派息", "publish_date": "2026-06-07", "url": "", "event_type": "earnings", "importance": "high", "polarity": "positive", "tags": []},
                {"title": "董事辞任", "notice_type": "高管变动", "publish_date": "2026-06-06", "url": "", "event_type": "governance", "importance": "medium", "polarity": "neutral", "tags": []},
                {"title": "利润分配补充", "notice_type": "分红派息", "publish_date": "2026-06-05", "url": "", "event_type": "earnings", "importance": "medium", "polarity": "positive", "tags": []},
            ],
            "analysis": financials._build_structured_analysis(
                [
                    {"title": "年报", "notice_type": "定期报告", "publish_date": "2026-06-08", "url": "", "event_type": "earnings", "importance": "medium", "polarity": "neutral", "tags": []},
                    {"title": "分红方案", "notice_type": "分红派息", "publish_date": "2026-06-07", "url": "", "event_type": "earnings", "importance": "high", "polarity": "positive", "tags": []},
                    {"title": "董事辞任", "notice_type": "高管变动", "publish_date": "2026-06-06", "url": "", "event_type": "governance", "importance": "medium", "polarity": "neutral", "tags": []},
                    {"title": "利润分配补充", "notice_type": "分红派息", "publish_date": "2026-06-05", "url": "", "event_type": "earnings", "importance": "medium", "polarity": "positive", "tags": []},
                ],
                days=days,
                dimension="公司公告",
            ),
            "source_chain": [],
            "errors": [],
            "data_time": "2026-06-08",
            "is_stale": False,
            "fallback_used": False,
            "_fetched_at": "2026-06-08T00:00:00",
            "_cached": False,
        },
    )

    response = client.get("/api/v1/stocks/announcements", params={"symbol": "300850.SZ", "force": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"]["notice_type_distribution"] == {
        "分红派息": 2,
        "定期报告": 1,
        "高管变动": 1,
    }


def test_research_uses_rsshub_fallback_when_direct_source_is_empty(client, monkeypatch):
    from api.v1.endpoints import financials

    class _EmptyFrame:
        empty = True

    import akshare as ak

    monkeypatch.setattr(ak, "stock_research_report_em", lambda **kwargs: _EmptyFrame())
    monkeypatch.setattr(
        financials,
        "_fetch_rss_research_reports",
        lambda symbol, days: (
            [{
                "title": "RSSHub研报",
                "org": "RSSHub研报源",
                "rating": None,
                "industry": None,
                "publish_date": "2026-06-08",
                "url": "https://example.com/report",
                "profit_forecasts": [],
                "monthly_report_count": None,
                "event_type": "research",
                "event_label": "研究评级",
                "polarity": "neutral",
                "importance": "medium",
                "tags": [],
            }],
            ["RSSHub东方财富个股研报(1条)"],
            [],
        ),
    )

    response = client.get("/api/v1/stocks/research-report", params={"symbol": "300850.SZ", "force": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["title"] == "RSSHub研报"
    assert payload["source_chain"] == ["RSSHub东方财富个股研报(1条)"]
    assert payload["fallback_used"] is True


def test_rsshub_entries_skip_slow_phase_when_fast_sources_are_enough(monkeypatch):
    from api.v1.endpoints import financials
    from api.v1.endpoints import rss

    called_urls = []

    monkeypatch.setattr(rss, "_build_feed_url", lambda source, **params: f"{source}:{params}")

    def fake_fetch(url: str, limit: int = 20, timeout: float = 15.0):
        called_urls.append((url, timeout))
        if url.startswith("eastmoney_search"):
            return {
                "items": [{
                    "title": "快源命中",
                    "summary": "300850 新闻",
                    "published": "2026-06-08T00:00:00",
                    "author": "",
                    "link": "https://example.com/fast",
                }],
                "errors": [],
            }
        return {"items": [], "errors": []}

    monkeypatch.setattr(rss, "_fetch_rss_feed", fake_fetch)

    entries, _, _ = financials._fetch_rsshub_entries(
        "300850",
        30,
        [
            ("eastmoney_search", {"keyword": "300850"}, "东方财富搜索:300850", True),
            ("36kr", {"category": "information/web_news"}, "36氪网页新闻", True),
        ],
        keywords=["300850"],
        target_items=1,
    )

    assert len(entries) == 1
    assert len(called_urls) == 1
    assert called_urls[0][0].startswith("eastmoney_search")
    assert called_urls[0][1] <= 4.0


def test_rsshub_entries_fall_back_to_slow_phase_when_fast_sources_are_thin(monkeypatch):
    from api.v1.endpoints import financials
    from api.v1.endpoints import rss

    called_urls = []

    monkeypatch.setattr(rss, "_build_feed_url", lambda source, **params: f"{source}:{params}")

    def fake_fetch(url: str, limit: int = 20, timeout: float = 15.0):
        called_urls.append((url, timeout))
        if url.startswith("36kr"):
            return {
                "items": [{
                    "title": "慢源补位",
                    "summary": "300850 补充资讯",
                    "published": "2026-06-08T00:00:00",
                    "author": "",
                    "link": "https://example.com/slow",
                }],
                "errors": [],
            }
        return {"items": [], "errors": []}

    monkeypatch.setattr(rss, "_fetch_rss_feed", fake_fetch)

    entries, _, _ = financials._fetch_rsshub_entries(
        "300850",
        30,
        [
            ("eastmoney_search", {"keyword": "300850"}, "东方财富搜索:300850", True),
            ("36kr", {"category": "information/web_news"}, "36氪网页新闻", True),
        ],
        keywords=["300850"],
        target_items=1,
    )

    assert len(entries) == 1
    assert len(called_urls) == 2
    assert called_urls[1][0].startswith("36kr")
    assert called_urls[1][1] <= 2.5
