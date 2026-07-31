# -*- coding: utf-8 -*-
"""Stock_info business helper tests — cache keys, sanitization, prompt builders.

Covers api.v1.endpoints.stock_info.business pure helpers (non-SSE):
- _business_cache_key / _business_cache_get / _business_cache_put
- _sanitize
- _build_growth_text / format_llm_input
- get_stock_business route (cache hit, force refetch)
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.stock_info import business as biz
import src.auth as auth


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
    ):
        yield
    auth._auth_enabled = None


# ---------------------------------------------------------------------------
# cache helpers
# ---------------------------------------------------------------------------


def test_business_cache_get_returns_none_when_db_raises():
    with patch("src.storage.DatabaseManager") as db_cls:
        db_cls.get_instance.side_effect = RuntimeError("no db")
        assert biz._business_cache_get("000001") is None


def test_business_cache_get_parses_string_payload():
    with patch("src.storage.DatabaseManager") as db_cls:
        db = MagicMock()
        db.get_kline_snapshot.return_value = json.dumps({"symbol": "000001"})
        db_cls.get_instance.return_value = db
        out = biz._business_cache_get("000001")
    assert out == {"symbol": "000001"}


def test_business_cache_get_returns_dict_payload_as_is():
    payload = {"symbol": "000001"}
    with patch("src.storage.DatabaseManager") as db_cls:
        db = MagicMock()
        db.get_kline_snapshot.return_value = payload
        db_cls.get_instance.return_value = db
        assert biz._business_cache_get("000001") == payload


def test_business_cache_put_swallows_db_errors():
    with patch("src.storage.DatabaseManager") as db_cls:
        db_cls.get_instance.side_effect = RuntimeError("no db")
        # should not raise
        biz._business_cache_put("000001", {"x": 1})


def test_business_cache_put_serializes_dict_to_json():
    with patch("src.storage.DatabaseManager") as db_cls:
        db = MagicMock()
        db_cls.get_instance.return_value = db
        biz._business_cache_put("000001", {"x": 1})
    db.save_kline_snapshot.assert_called_once()
    args = db.save_kline_snapshot.call_args[0]
    assert json.loads(args[1]) == {"x": 1}


# ---------------------------------------------------------------------------
# _sanitize
# ---------------------------------------------------------------------------


def test_get_stock_business_returns_cached_without_force(client):
    cached = {"symbol": "000001", "cached": True}
    with patch("api.v1.endpoints.stock_info.business._business_cache_get", return_value=cached):
        resp = client.get("/api/v1/stocks/business", params={"symbol": "000001"})
    assert resp.status_code == 200
    assert resp.json()["cached"] is True


def test_get_stock_business_force_refetches_and_caches(client):
    result = {"symbol": "000001", "fresh": True}
    with (
        patch("api.v1.endpoints.stock_info.business._business_cache_get", return_value=None),
        patch("api.v1.endpoints.stock_info.business._fetch_business_intro", return_value={}),
        patch("api.v1.endpoints.stock_info.business._fetch_business_composition", return_value=[]),
        patch("api.v1.endpoints.stock_info.business._fetch_profit_forecast", return_value=[]),
        patch("api.v1.endpoints.stock_info.business._fetch_financial_summary", return_value={}),
        patch("api.v1.endpoints.stock_info.business._fetch_recent_events", return_value={}),
        patch("api.v1.endpoints.stock_info.business._generate_llm_business_analysis", return_value=result),
        patch("api.v1.endpoints.stock_info.business._business_cache_put") as put,
    ):
        resp = client.get("/api/v1/stocks/business", params={"symbol": "sh000001", "force": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["fresh"] is True
    assert body["symbol"] == "000001"
    put.assert_called_once()


# ---------------------------------------------------------------------------
# Regression: fetcher output must align with prompt builder field access.
# _build_business_prompt reads keys via subscript (a['date'], n['time'], f['analyst']...),
# so the fetchers MUST normalize akshare's raw Chinese column names into these keys.
# Previously _fetch_recent_events returned raw stock_news_em dicts (新闻标题/发布时间...)
# and _fetch_profit_forecast returned raw stock_profit_forecast_em rows, both of which
# KeyError'd inside _build_business_prompt whenever news/forecast were non-empty.
# ---------------------------------------------------------------------------

from api.v1.endpoints.stock_info._data import (
    _fetch_profit_forecast,
    _fetch_recent_events,
)
from api.v1.endpoints.stock_info._llm_prompts import (
    _build_business_prompt,
    _build_catalyst_prompt,
)


def _fake_news_df():
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "关键词": "600519",
                "新闻标题": "测试新闻标题",
                "新闻内容": "正文内容",
                "发布时间": "2026-07-07 22:23:00",
                "文章来源": "证券时报网",
                "新闻链接": "http://example.com/n1",
            }
        ]
    )


def _fake_research_df():
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "序号": 1,
                "股票代码": "600519",
                "股票简称": "贵州茅台",
                "报告名称": "测试研报",
                "东财评级": "买入",
                "机构": "诚通证券",
                "2026-盈利预测-收益": 66.68,
                "2027-盈利预测-收益": 69.43,
                "2028-盈利预测-收益": 72.56,
                "日期": "2026-05-25",
            }
        ]
    )


def _fake_notice_df():
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "代码": "600519",
                "名称": "贵州茅台",
                "公告标题": "权益分派实施公告",
                "公告类型": "分配方案实施",
                "公告日期": "2026-06-22",
                "网址": "http://example.com/a1",
            }
        ]
    )


def test_fetch_recent_events_normalizes_news_and_announcement_fields():
    with (
        patch("akshare.stock_news_em", return_value=_fake_news_df()),
        patch("akshare.stock_individual_notice_report", return_value=_fake_notice_df()),
    ):
        events = _fetch_recent_events("600519")
    assert events["news"] and events["announcements"]
    n = events["news"][0]
    assert set(["time", "source", "title", "content", "url"]).issubset(n.keys())
    assert n["title"] == "测试新闻标题" and n["source"] == "证券时报网"
    a = events["announcements"][0]
    assert set(["date", "type", "title", "url"]).issubset(a.keys())
    assert a["title"] == "权益分派实施公告" and a["type"] == "分配方案实施"


def test_fetch_profit_forecast_normalizes_research_report_fields():
    with patch("akshare.stock_research_report_em", return_value=_fake_research_df()):
        pf = _fetch_profit_forecast("600519")
    assert pf
    f = pf[0]
    assert set(["analyst", "researcher", "eps_2026", "eps_2027", "eps_2028"]).issubset(f.keys())
    assert f["analyst"] == "诚通证券"
    assert f["eps_2026"] == 66.68 and f["eps_2028"] == 72.56


def test_build_business_prompt_does_not_raise_with_normalized_data():
    """The prompt reads a['date']/n['time']/f['analyst'] via subscript — must not KeyError."""
    # Build normalized structures directly (mirrors fetcher output) to test prompt in isolation.
    events_norm = {
        "news": [
            {
                "time": "2026-07-07 22:23:00",
                "source": "证券时报网",
                "title": "测试新闻标题",
                "content": "正文内容",
                "url": "http://example.com/n1",
            }
        ],
        "announcements": [
            {
                "date": "2026-06-22",
                "type": "分配方案实施",
                "title": "权益分派实施公告",
                "url": "http://example.com/a1",
            }
        ],
    }
    forecast_norm = [
        {
            "analyst": "诚通证券",
            "researcher": "买入",
            "rating": "买入",
            "eps_2026": 66.68,
            "eps_2027": 69.43,
            "eps_2028": 72.56,
            "title": "测试研报",
            "date": "2026-05-25",
        }
    ]
    system, user, _ = _build_business_prompt(
        "600519",
        {"main_business": "白酒"},
        [],
        forecast_norm,
        [],
        events_norm,
    )
    assert "诚通证券" in user and "权益分派实施公告" in user and "证券时报网" in user


def test_build_catalyst_prompt_does_not_raise_with_normalized_events():
    events_norm = {
        "news": [{"time": "2026-07-07", "source": "证券时报网", "title": "测试新闻标题"}],
        "announcements": [{"date": "2026-06-22", "title": "权益分派实施公告"}],
    }
    system, user = _build_catalyst_prompt("600519", "白酒", events_norm, [])
    assert "测试新闻标题" in user and "权益分派实施公告" in user
