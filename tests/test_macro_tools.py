# -*- coding: utf-8 -*-

from datetime import date

import pandas as pd

from src.tools._macro_common import expected_indicator_period
from src.tools.get_bond_yield import _same_date_spread
from src.tools.get_index_data import _merge_snapshot


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
        "date": "2026-07-16", "close": 101.0, "previous_close": 100.0,
        "record_type": "realtime_snapshot",
    }
    stale_or_unrelated = {**connected, "previous_close": 95.0}

    merged = _merge_snapshot(history, connected, 5)
    rejected = _merge_snapshot(history, stale_or_unrelated, 5)

    assert [row["date"] for row in merged] == ["2026-07-14", "2026-07-15", "2026-07-16"]
    assert merged[-1]["record_type"] == "realtime_snapshot"
    assert rejected == history


def test_bond_curve_spread_uses_one_common_observation_date():
    frame = pd.DataFrame([
        {"日期": "2026-07-14", "中国国债收益率10年": 2.0, "中国国债收益率2年": 1.0},
        {"日期": "2026-07-15", "中国国债收益率10年": 2.1, "中国国债收益率2年": None},
    ])

    spread, spread_date = _same_date_spread(frame, "cn")

    assert spread == 1.0
    assert spread_date == "2026-07-14"

