from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.services.stock_screening import atr_volatility_screener as screener
from src.services.stock_screening.atr_volatility_screener import (
    ScreenRules,
    calculate_atr_screen_metrics,
)


def test_calculate_atr_uses_simple_moving_averages_and_exact_dynamic_line() -> None:
    bars = [
        {"date": f"2026-01-0{i}", "open": 10, "close": 10, "high": high, "low": low}
        for i, (high, low) in enumerate([(11, 9), (12, 8), (13, 7), (14, 6), (15, 5)], 1)
    ]
    rules = ScreenRules(
        atr_period=2,
        long_period=2,
        warning_divisor=2,
        lookback_days=3,
        min_qualified_days=3,
        min_qualified_ratio=100,
    )

    result = calculate_atr_screen_metrics(bars, rules)

    assert result is not None
    assert result["current_atr_pct"] == pytest.approx(90.0)
    assert result["long_term_mean_pct"] == pytest.approx(80.0)
    assert result["dynamic_warning_pct"] == pytest.approx(40.0)
    assert result["qualified_days"] == 3
    assert result["qualified_ratio_pct"] == pytest.approx(100.0)


def test_calculate_atr_never_shrinks_requested_denominator() -> None:
    rules = ScreenRules(atr_period=2, long_period=2, lookback_days=3)
    bars = [
        {"date": "2026-01-01", "open": 10, "close": 10, "high": 11, "low": 9},
        {"date": "2026-01-02", "open": 10, "close": 10, "high": 12, "low": 8},
        {"date": "2026-01-03", "open": 10, "close": 10, "high": 13, "low": 7},
        {"date": "2026-01-04", "open": 10, "close": 10, "high": 14, "low": 6},
    ]
    assert calculate_atr_screen_metrics(bars, rules) is None


def test_build_ttm_financials_uses_current_plus_annual_minus_prior_same(monkeypatch) -> None:
    rows = {
        "2026-03-31": {"000001": {"TOTALOPERATEREVE": 20, "KCFJCXSYJLR": 4, "ZCFZL": 50}},
        "2025-12-31": {"000001": {"TOTALOPERATEREVE": 100, "KCFJCXSYJLR": 10}},
        "2025-03-31": {"000001": {"TOTALOPERATEREVE": 15, "KCFJCXSYJLR": 3}},
    }
    monkeypatch.setattr(screener, "_fetch_financial_period", lambda period: rows[period])

    result, period = screener._build_ttm_financials(date(2026, 7, 18))

    assert period == "2026-03-31"
    assert result["000001"] == {
        "revenue_ttm": 105.0,
        "deducted_net_profit_ttm": 11.0,
        "debt_ratio": 50.0,
        "financial_report_period": "2026-03-31",
        "financial_source": "东方财富财务主指标",
    }


def test_export_is_utf8_csv_and_returns_safe_relative_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(screener, "EXPORT_DIR", tmp_path)
    file_id, url = screener._write_export([{
        "code": "000001", "name": "平安银行", "current_atr_pct": 1.2,
        "long_term_mean_pct": 1.3, "dynamic_warning_pct": 1.02,
        "qualified_days": 180, "qualified_ratio_pct": 72,
        "revenue_ttm": 1_000_000_000, "deducted_net_profit_ttm": 10_000_000,
        "debt_ratio": 50, "financial_report_period": "2026-03-31",
        "financial_source": "东方财富财务主指标", "latest_trade_date": "2026-07-17",
    }])

    assert url == f"/api/v1/agent/exports/{file_id}"
    content = (tmp_path / file_id).read_text(encoding="utf-8-sig")
    assert "股票代码" in content
    assert "平安银行" in content
    assert "东方财富财务主指标" in content
