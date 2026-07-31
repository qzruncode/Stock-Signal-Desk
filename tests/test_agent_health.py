# -*- coding: utf-8 -*-
"""Agent health module tests — staleness and fallback decision logic.

Covers api.v1.endpoints.agent.health._assess_tool_data_health and the
datetime parsing helpers for normal / failure / boundary paths.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from api.v1.endpoints.agent.health import (
    _assess_tool_data_health,
    _latest_date_from_items,
    _parse_iso_datetime,
)


# ---------------------------------------------------------------------------
# _parse_iso_datetime
# ---------------------------------------------------------------------------


def test_parse_iso_datetime_accepts_full_iso():
    assert _parse_iso_datetime("2026-06-01T10:00:00") == datetime(2026, 6, 1, 10, 0, 0)


def test_parse_iso_datetime_handles_z_suffix():
    parsed = _parse_iso_datetime("2026-06-01T10:00:00Z")
    assert parsed is not None
    assert parsed.year == 2026 and parsed.month == 6 and parsed.day == 1


def test_parse_iso_datetime_accepts_date_only():
    assert _parse_iso_datetime("2026-06-01") == datetime(2026, 6, 1)


def test_parse_iso_datetime_accepts_compact_date():
    assert _parse_iso_datetime("20260601") == datetime(2026, 6, 1)


def test_parse_iso_datetime_returns_none_for_empty_or_invalid():
    assert _parse_iso_datetime(None) is None
    assert _parse_iso_datetime("") is None
    assert _parse_iso_datetime("not-a-date") is None


# ---------------------------------------------------------------------------
# _latest_date_from_items
# ---------------------------------------------------------------------------


def test_latest_date_from_items_picks_latest_across_keys():
    items = [
        {"publish_time": "2026-05-01T00:00:00"},
        {"publish_date": "2026-06-01"},
        {"date_str": "20260401"},
    ]
    latest = _latest_date_from_items(items, ["publish_time", "publish_date", "date_str"])
    assert latest == datetime(2026, 6, 1)


def test_latest_date_from_items_returns_none_for_empty_or_non_list():
    assert _latest_date_from_items(None, ["date"]) is None
    assert _latest_date_from_items([], ["date"]) is None
    assert _latest_date_from_items([{"date": "x"}], ["date"]) is None


def test_latest_date_from_items_can_compare_naive_and_offset_aware_values():
    latest = _latest_date_from_items(
        [
            {"publish_time": "2026-07-15T10:00:00"},
            {"publish_time": "2026-07-15T11:00:00+08:00"},
        ],
        ["publish_time"],
    )

    assert latest is not None


# ---------------------------------------------------------------------------
# _assess_tool_data_health
# ---------------------------------------------------------------------------


def test_assess_health_non_dict_result_no_fallback():
    assert _assess_tool_data_health("get_realtime_quotes", "not-a-dict") == {
        "should_fallback": False,
        "reason": None,
    }


def test_assess_health_tool_error_triggers_fallback():
    result = {"error": "boom"}
    health = _assess_tool_data_health("get_realtime_quotes", result)
    assert health["should_fallback"] is True
    assert health["reason"] == "tool_error"


def test_assess_health_is_stale_flag_triggers_fallback():
    result = {"is_stale": True, "data_time": "2026-06-01"}
    health = _assess_tool_data_health("get_kline", result)
    assert health["should_fallback"] is True
    assert health["reason"] == "stale_get_kline"
    assert health["latest_date"] == "2026-06-01"


def test_assess_health_realtime_quotes_empty_items_fallback():
    health = _assess_tool_data_health("get_realtime_quotes", {"items": []})
    assert health["should_fallback"] is True
    assert health["reason"] == "empty_quotes"


def test_assess_health_realtime_quotes_with_items_no_fallback():
    health = _assess_tool_data_health("get_realtime_quotes", {"items": [{"symbol": "000001"}]})
    assert health == {"should_fallback": False, "reason": None}


def test_assess_health_kline_empty_series_fallback():
    health = _assess_tool_data_health("get_kline", {"data": []})
    assert health["should_fallback"] is True
    assert health["reason"] == "empty_kline"


def test_assess_health_kline_recent_series_no_fallback():
    today = datetime.now().strftime("%Y-%m-%d")
    health = _assess_tool_data_health("get_kline", {"data": [{"date": today}]})
    assert health["should_fallback"] is False


def test_assess_health_kline_stale_data_time_fallback():
    old = (datetime.now().date() - timedelta(days=30)).isoformat()
    health = _assess_tool_data_health("get_kline", {"data": [{"date": old}], "data_time": old})
    assert health["should_fallback"] is True
    assert health["reason"] == "stale_kline"


def test_assess_health_news_family_empty_items_fallback():
    health = _assess_tool_data_health("search_news", {"items": []})
    assert health["should_fallback"] is True
    assert health["reason"] == "empty_news_family"


def test_assess_health_news_family_stale_data_time_fallback():
    old = (datetime.now() - timedelta(days=60)).isoformat()
    health = _assess_tool_data_health("search_news", {"items": [{"title": "x"}], "data_time": old, "days": 30})
    assert health["should_fallback"] is True
    assert health["reason"] == "stale_news_family"


def test_assess_health_news_family_recent_items_no_fallback():
    recent = datetime.now().strftime("%Y-%m-%d")
    health = _assess_tool_data_health("search_news", {"items": [{"publish_time": recent}], "days": 30})
    assert health["should_fallback"] is False


def test_assess_health_news_family_accepts_offset_aware_data_time():
    recent = datetime.now().astimezone().isoformat()
    health = _assess_tool_data_health(
        "get_social_sentiment",
        {"items": [{"publish_time": recent}], "data_time": recent, "days": 30},
    )

    assert health["should_fallback"] is False


def test_assess_health_unknown_tool_no_fallback():
    health = _assess_tool_data_health("some_unknown_tool", {"items": []})
    assert health == {"should_fallback": False, "reason": None}
