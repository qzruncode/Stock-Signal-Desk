from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from market_data_service.acquisition import collect_kline, next_source_run


def window():
    now = datetime(2026, 9, 7, 8)
    rows = [
        {
            "date": (now.date() - timedelta(days=49 - i)).isoformat(),
            "open": 10,
            "close": 11,
            "high": 12,
            "low": 9,
            "volume": i + 100,
        }
        for i in range(50)
    ]
    payload = {
        "data": rows,
        "source": "eastmoney",
        "success": True,
        "data_time": rows[-1]["date"],
        "full_checked_at": now.isoformat(),
    }
    return now, payload, SimpleNamespace(payload=payload, fetched_at=now)


def test_incremental_read_requests_overlap_and_keeps_the_complete_window():
    now, payload, previous = window()
    provider = Mock()
    provider.kline.return_value = {**payload, "data": payload["data"][-20:]}
    result = collect_kline(
        provider, {"symbol": "000001", "count": 50}, previous, now=now
    )
    assert provider.kline.call_args.kwargs["start_date"] == payload["data"][-20]["date"]
    assert result["count"] == 50 and result["sync_mode"] == "incremental"
    assert result["full_checked_at"] == payload["full_checked_at"]


@pytest.mark.parametrize(
    "reason", ["adjustment", "source", "missing_overlap", "older_window"]
)
def test_incompatible_incremental_data_reloads_the_full_adjusted_window(reason):
    now, payload, previous = window()
    delta = {**payload, "data": [dict(row) for row in payload["data"][-20:]]}
    if reason == "adjustment":
        delta["data"][0]["close"] = 5.5
    elif reason == "source":
        delta["source"] = "sina"
    elif reason == "missing_overlap":
        delta["data"] = delta["data"][2:]
    else:
        delta["data"] = delta["data"][:-2]
    provider = Mock()
    provider.kline.side_effect = [delta, payload]
    result = collect_kline(
        provider, {"symbol": "000001", "count": 50}, previous, now=now
    )
    assert result["sync_mode"] == "full" and provider.kline.call_count == 2
    assert "start_date" not in provider.kline.call_args.kwargs


@pytest.mark.parametrize("options", [{"force_full": True}, {"age": 8}, {"count": 100}])
def test_explicit_full_cold_windows_and_weekly_reconciliation_do_not_merge(options):
    now, payload, previous = window()
    provider = Mock(kline=Mock(return_value=payload))
    result = collect_kline(
        provider,
        {"symbol": "000001", "count": options.get("count", 50)},
        previous,
        force_full=options.get("force_full", False),
        now=now + timedelta(days=options.get("age", 0)),
    )
    assert result["sync_mode"] == "full" and provider.kline.call_count == 1
    assert "start_date" not in provider.kline.call_args.kwargs


def test_quote_refresh_skips_non_trading_hours(monkeypatch):
    from datetime import date

    from market_data_service import calendar

    monkeypatch.setattr(
        calendar, "trade_dates", lambda: [date(2026, 9, 4), date(2026, 9, 7)]
    )
    monkeypatch.setattr(calendar, "is_trading_time", lambda now: False)
    assert next_source_run("quotes", 60, now=datetime(2026, 9, 5, 8)) == datetime(
        2026, 9, 7, 1, 15
    )
    assert next_source_run("news", 60, now=datetime(2026, 9, 5, 8)) == datetime(
        2026, 9, 5, 8, 1
    )
