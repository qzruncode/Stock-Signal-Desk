# -*- coding: utf-8 -*-

from datetime import date

import pandas as pd

from src.tools._macro_common import expected_indicator_period
from src.tools.get_bond_yield import _fetch_frame, _same_date_spread, read_bond_yield_eastmoney
from src.tools.get_index_data import _merge_snapshot
from src.tools.get_macro_indicator import read_macro_indicator_akshare


def test_indicator_release_calendar_does_not_expect_unreleased_periods():
    assert expected_indicator_period("PMI", date(2026, 7, 30)) == date(2026, 6, 30)
    assert expected_indicator_period("PMI", date(2026, 7, 31)) == date(2026, 7, 31)

    assert expected_indicator_period("CPI", date(2026, 7, 9)) == date(2026, 5, 31)
    assert expected_indicator_period("CPI", date(2026, 7, 10)) == date(2026, 6, 30)

    assert expected_indicator_period("社融", date(2026, 7, 14)) == date(2026, 5, 31)
    assert expected_indicator_period("社融", date(2026, 7, 15)) == date(2026, 6, 30)

    assert expected_indicator_period("LPR", date(2026, 7, 19)) == date(2026, 6, 30)
    assert expected_indicator_period("LPR", date(2026, 7, 20)) == date(2026, 7, 31)

    assert expected_indicator_period("GDP", date(2026, 7, 16)) == date(2026, 3, 31)
    assert expected_indicator_period("GDP", date(2026, 7, 20)) == date(2026, 6, 30)
    assert expected_indicator_period("GDP", date(2026, 1, 10)) == date(2025, 9, 30)
    assert expected_indicator_period("GDP", date(2026, 1, 20)) == date(2025, 12, 31)


def test_index_snapshot_only_appends_when_previous_close_connects_to_daily_series():
    history = [
        {"date": "2026-07-14", "close": 98.0, "record_type": "daily_close"},
        {"date": "2026-07-15", "close": 100.0, "record_type": "daily_close"},
    ]
    connected = {
        "date": "2026-07-16",
        "close": 101.0,
        "previous_close": 100.0,
        "record_type": "realtime_snapshot",
    }
    stale_or_unrelated = {**connected, "previous_close": 95.0}

    merged = _merge_snapshot(history, connected, 5)
    rejected = _merge_snapshot(history, stale_or_unrelated, 5)

    assert [row["date"] for row in merged] == ["2026-07-14", "2026-07-15", "2026-07-16"]
    assert merged[-1]["record_type"] == "realtime_snapshot"
    assert rejected == history


def test_bond_curve_spread_uses_one_common_observation_date():
    frame = pd.DataFrame(
        [
            {"日期": "2026-07-14", "中国国债收益率10年": 2.0, "中国国债收益率2年": 1.0},
            {"日期": "2026-07-15", "中国国债收益率10年": 2.1, "中国国债收益率2年": None},
        ]
    )

    spread, spread_date = _same_date_spread(frame, "cn")

    assert spread == 1.0
    assert spread_date == "2026-07-14"


def test_bond_yield_fetches_only_recent_page_with_explicit_timeout(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "result": {
                    "data": [
                        {
                            "SOLAR_DATE": "2026-07-18",
                            "EMM00588704": "1.11",
                            "EMM00166462": "1.22",
                            "EMM00166466": "1.33",
                            "EMM00166469": "1.44",
                            "EMG00001306": "3.55",
                            "EMG00001308": "3.66",
                            "EMG00001310": "3.77",
                            "EMG00001312": "3.88",
                        }
                    ],
                },
            }

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("src.tools.get_bond_yield.requests.get", fake_get)

    frame = _fetch_frame()

    assert len(frame) == 1
    assert frame.iloc[0]["中国国债收益率10年"] == 1.33
    assert calls[0][1]["params"]["p"] == "1"
    assert calls[0][1]["params"]["ps"] == "500"
    assert calls[0][1]["timeout"] == (4, 10)


def test_agent_bond_read_has_no_curve_spread_or_local_fallback(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "日期": pd.Timestamp("2026-07-14").date(),
                "中国国债收益率10年": 2.0,
            },
            {
                "日期": pd.Timestamp("2026-07-15").date(),
                "中国国债收益率10年": 2.1,
            },
        ]
    )
    monkeypatch.setattr(
        "src.tools.get_bond_yield.cached_call",
        lambda _key, _call, **_kwargs: (frame, False),
    )

    result = read_bond_yield_eastmoney(country="cn", term="10y", days=5)

    assert result["success"] is True
    assert result["source_scope"] == "single_sovereign_yield_series"
    assert "spread" not in result
    assert result["fallback_used"] is False


def test_agent_macro_read_has_no_trend_or_local_fallback(monkeypatch):
    frame = pd.DataFrame(
        [
            {"月份": "2026年5月", "制造业-指数": 49.5, "制造业-同比增长": 0.2, "非制造业-指数": 50.0},
            {"月份": "2026年6月", "制造业-指数": 50.1, "制造业-同比增长": 0.4, "非制造业-指数": 50.3},
            {"月份": "2026年7月", "制造业-指数": 50.4, "制造业-同比增长": 0.6, "非制造业-指数": 50.6},
        ]
    )
    monkeypatch.setattr(
        "src.tools.get_macro_indicator.cached_call",
        lambda _key, _call, **_kwargs: (frame, False),
    )

    result = read_macro_indicator_akshare("PMI", periods=3)

    assert result["success"] is True
    assert result["source_scope"] == "single_macro_indicator_series"
    assert "trend" not in result
    assert "history" not in result
    assert result["fallback_used"] is False
