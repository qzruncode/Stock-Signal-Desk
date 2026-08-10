"""Tests for active-universe financial report synchronization."""

from __future__ import annotations

import importlib
import threading
from datetime import date

import pytest


@pytest.fixture
def fin_sync():
    """Reload the module so each test starts with a clean injected state."""
    import api.v1.endpoints.stocks._financials_sync as mod

    importlib.reload(mod)
    return mod


def test_latest_report_period_uses_most_recent_quarter(fin_sync):
    assert fin_sync.latest_report_period(date(2025, 8, 15)) == "20250630"


def test_latest_report_period_buffer_skips_recent(fin_sync):
    assert fin_sync.latest_report_period(date(2025, 5, 1)) == "20250331"
    assert fin_sync.latest_report_period(date(2025, 4, 15)) == "20241231"


def test_periods_from_explicit_report_period_keeps_requested_latest_period(fin_sync):
    periods = fin_sync._periods_from_start(date(2026, 6, 30), limit=5)

    assert periods == [
        "2026-06-30",
        "2026-03-31",
        "2025-12-31",
        "2025-09-30",
        "2025-06-30",
    ]


def test_row_to_update_keeps_one_report_period_and_computes_ttm(fin_sync):
    rows_by_period = {
        "2026-06-30": {
            "000001": {
                "SECURITY_CODE": "000001",
                "REPORT_DATE": "2026-06-30",
                "TOTALOPERATEREVE": 120.0,
                "PARENTNETPROFIT": 24.0,
                "KCFJCXSYJLR": 20.0,
                "ZCFZL": 40.0,
            }
        },
        "2025-12-31": {
            "000001": {
                "SECURITY_CODE": "000001",
                "REPORT_DATE": "2025-12-31",
                "TOTALOPERATEREVE": 400.0,
                "PARENTNETPROFIT": 80.0,
                "KCFJCXSYJLR": 70.0,
            }
        },
        "2025-06-30": {
            "000001": {
                "SECURITY_CODE": "000001",
                "REPORT_DATE": "2025-06-30",
                "TOTALOPERATEREVE": 100.0,
                "PARENTNETPROFIT": 20.0,
                "KCFJCXSYJLR": 18.0,
            }
        },
    }

    result = fin_sync._row_to_update(
        "000001",
        "2026-06-30",
        rows_by_period["2026-06-30"]["000001"],
        rows_by_period,
    )

    assert result["report_date"] == "2026-06-30"
    assert result["revenue_latest"] == 120.0
    assert result["net_profit_latest"] == 24.0
    assert result["debt_ratio"] == 40.0
    assert result["revenue_ttm"] == 420.0
    assert result["parent_net_profit_ttm"] == 84.0
    assert result["deducted_net_profit_ttm"] == 72.0


def test_collect_period_rows_uses_latest_available_period_per_active_code(fin_sync, monkeypatch):
    rows = {
        "2026-06-30": {
            "000001": {"SECURITY_CODE": "000001", "TOTALOPERATEREVE": 1.0},
        },
        "2026-03-31": {
            "000001": {"SECURITY_CODE": "000001", "TOTALOPERATEREVE": 0.8},
            "000002": {"SECURITY_CODE": "000002", "TOTALOPERATEREVE": 0.5},
            "999999": {"SECURITY_CODE": "999999", "TOTALOPERATEREVE": 0.2},
        },
    }
    monkeypatch.setattr(fin_sync, "_fetch_period_snapshot", lambda period: rows[period])

    selected, _, source_codes, errors = fin_sync._collect_period_rows(
        {"000001", "000002"},
        ["2026-06-30", "2026-03-31"],
    )

    assert not errors
    assert selected["000001"][0] == "2026-06-30"
    assert selected["000002"][0] == "2026-03-31"
    assert "999999" in source_codes


def test_fallback_update_uses_latest_available_aggregated_report(fin_sync, monkeypatch):
    monkeypatch.setattr(
        "src.tools.get_financials.get_financials",
        lambda code, periods, use_cache: {
            "items": [
                {
                    "report_date": "2025-09-30",
                    "revenue": 10.0,
                    "parent_net_profit": 2.0,
                    "debt_ratio": 45.0,
                },
                {
                    "report_date": "2025-12-31",
                    "revenue": 12.0,
                    "parent_net_profit": 3.0,
                    "debt_ratio": 44.0,
                },
            ]
        },
    )

    result = fin_sync._fallback_update_from_financials("000002")

    assert result is not None
    assert result["report_date"] == "2025-12-31"
    assert result["revenue_latest"] == 12.0
    assert result["net_profit_latest"] == 3.0
    assert result["debt_ratio"] == 44.0


def test_run_financial_sync_uses_active_stock_count_as_total(fin_sync, monkeypatch):
    state: dict = {}
    fin_sync.attach_state(
        state=state,
        lock=threading.Lock(),
        set_state=lambda **updates: state.update(updates),
        initial_state=lambda: {},
        utc_now_iso=lambda: "2026-08-10T00:00:00+00:00",
    )

    current = {
        "SECURITY_CODE": "000001",
        "REPORT_DATE": "2026-06-30",
        "TOTALOPERATEREVE": 120.0,
        "PARENTNETPROFIT": 24.0,
        "KCFJCXSYJLR": 20.0,
        "ZCFZL": 40.0,
    }
    annual = {
        "SECURITY_CODE": "000001",
        "REPORT_DATE": "2025-12-31",
        "TOTALOPERATEREVE": 400.0,
        "PARENTNETPROFIT": 80.0,
        "KCFJCXSYJLR": 70.0,
    }
    prior = {
        "SECURITY_CODE": "000001",
        "REPORT_DATE": "2025-06-30",
        "TOTALOPERATEREVE": 100.0,
        "PARENTNETPROFIT": 20.0,
        "KCFJCXSYJLR": 18.0,
    }

    def fetch(period: str):
        return {
            "2026-06-30": {"000001": current},
            "2025-12-31": {"000001": annual},
            "2025-06-30": {"000001": prior},
        }.get(period, {})

    monkeypatch.setattr(fin_sync, "_fetch_period_snapshot", fetch)
    monkeypatch.setattr(
        fin_sync,
        "_fallback_update_from_financials",
        lambda code: {
            "report_date": "2025-12-31",
            "revenue_latest": 50.0,
            "net_profit_latest": 5.0,
            "debt_ratio": 30.0,
            "revenue_ttm": 50.0,
            "parent_net_profit_ttm": 5.0,
            "deducted_net_profit_ttm": 4.0,
            "financial_fetched_at": __import__("datetime").datetime.now(),
        },
    )
    monkeypatch.setattr(
        fin_sync,
        "_persist_updates",
        lambda updates, *, progress_offset=0: (len(updates), 0, 0),
    )
    monkeypatch.setattr(fin_sync, "_record_terminal_job", lambda **kwargs: None)

    fin_sync.run_financial_sync("20260630", ["000001", "000002"])

    assert state["total"] == 2
    assert state["progress"] == 2
    assert state["updated_count"] == 2
    assert state["no_data_count"] == 0
    assert state["status"] == "success"
