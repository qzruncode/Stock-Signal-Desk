from __future__ import annotations

import csv
from contextlib import contextmanager
from datetime import date

import pytest
from pydantic import ValidationError

from src.services.stock_screening import atr_volatility_screener as screener
from src.services.stock_screening.atr_volatility_screener import (
    calculate_atr_screen_metrics,
)
from src.services.stock_screening.screen_spec import (
    AtrRelativeFrequencyRule,
    QuantitativeScreenSpec,
)


def make_rule(**updates) -> AtrRelativeFrequencyRule:
    payload = {
        "strategy": "atr_relative_frequency",
        "atr_period": 2,
        "atr_average": "sma",
        "baseline_period": 2,
        "baseline_average": "sma",
        "threshold_operator": "divide",
        "threshold_value": 2,
        "daily_comparison": "gt",
        "lookback_days": 3,
        "min_qualified_days": 3,
        "min_qualified_ratio_pct": 100,
    }
    payload.update(updates)
    return AtrRelativeFrequencyRule.model_validate(payload)


def make_spec(**updates) -> QuantitativeScreenSpec:
    payload = {
        "version": "1.0",
        "universe": {
            "status": "active",
            "markets": ["sh", "sz", "bj"],
            "include_st": True,
            "min_listing_trading_days": 3,
            "price_adjustment": "qfq",
        },
        "technical_rule": make_rule().model_dump(mode="json"),
        "financial_filters": [
            {"field": "revenue_ttm", "operator": "gt", "value": 500_000_000},
        ],
        "sort": {"field": "qualified_ratio_pct", "order": "desc"},
        "output_fields": [
            "current_atr_pct",
            "long_term_mean_pct",
            "dynamic_warning_pct",
            "qualified_days",
            "qualified_ratio_pct",
            "revenue_ttm",
            "financial_report_period",
            "financial_source",
            "latest_trade_date",
        ],
        "preview_limit": 10,
    }
    payload.update(updates)
    return QuantitativeScreenSpec.model_validate(payload)


def test_calculate_atr_uses_caller_supplied_sma_periods_and_dynamic_line() -> None:
    bars = [
        {"date": f"2026-01-0{i}", "open": 10, "close": 10, "high": high, "low": low}
        for i, (high, low) in enumerate(
            [(11, 9), (11, 9), (12, 8), (13, 7), (14, 6), (15, 5)], 1
        )
    ]

    result = calculate_atr_screen_metrics(bars, make_rule())

    assert result is not None
    assert result["current_atr_pct"] == pytest.approx(90.0)
    assert result["long_term_mean_pct"] == pytest.approx(80.0)
    assert result["dynamic_warning_pct"] == pytest.approx(40.0)
    assert result["qualified_days"] == 3
    assert result["qualified_ratio_pct"] == pytest.approx(100.0)


def test_calculate_atr_uses_absolute_volatility_threshold_when_provided() -> None:
    bars = [
        {"date": f"2026-01-0{i}", "open": 100, "close": 100, "high": high, "low": low}
        for i, (high, low) in enumerate(
            [(101, 99), (101, 99), (102, 98), (103, 97), (104, 96), (105, 95)], 1
        )
    ]

    result = calculate_atr_screen_metrics(bars, make_rule(volatility_threshold_pct=8.5))

    assert result is not None
    assert result["dynamic_warning_pct"] == pytest.approx(8.5)
    assert result["qualified_days"] == 1
    assert result["qualified_ratio_pct"] == pytest.approx(100 / 3)


def test_calculate_atr_changes_when_user_changes_average_and_threshold() -> None:
    ranges = [1, 7, 2, 9, 3, 4, 11, 2]
    bars = [
        {
            "date": f"2026-01-{i:02d}",
            "open": 20,
            "close": 20,
            "high": 20 + width,
            "low": 20 - width / 2,
        }
        for i, width in enumerate(ranges, 1)
    ]
    sma = calculate_atr_screen_metrics(
        bars, make_rule(lookback_days=4, min_qualified_days=0)
    )
    ema = calculate_atr_screen_metrics(
        bars,
        make_rule(
            atr_average="ema",
            baseline_average="ema",
            threshold_operator="multiply",
            threshold_value=1.1,
            lookback_days=4,
            min_qualified_days=0,
        ),
    )

    assert sma is not None and ema is not None
    assert ema["current_atr_pct"] != pytest.approx(sma["current_atr_pct"])
    assert ema["dynamic_warning_pct"] == pytest.approx(ema["long_term_mean_pct"] * 1.1)


def test_ema_result_is_not_changed_when_provider_returns_extra_history() -> None:
    rule = make_rule(
        atr_average="ema",
        baseline_average="ema",
        lookback_days=4,
        min_qualified_days=0,
    )
    required = rule.atr_period + rule.baseline_period + rule.lookback_days - 1
    tail = [
        {
            "date": f"2026-01-{index:02d}",
            "open": 20,
            "close": 20,
            "high": 20 + width,
            "low": 20 - width / 2,
        }
        for index, width in enumerate([1, 7, 2, 9, 3, 4, 11, 2][-required:], 1)
    ]
    prefix = [
        {"date": "2025-12-30", "open": 20, "close": 20, "high": 40, "low": 1},
        {"date": "2025-12-31", "open": 20, "close": 20, "high": 21, "low": 19},
    ]

    exact = calculate_atr_screen_metrics(tail, rule)
    with_extra = calculate_atr_screen_metrics(prefix + tail, rule)

    assert exact is not None and with_extra is not None
    assert with_extra["current_atr_pct"] == pytest.approx(exact["current_atr_pct"])
    assert with_extra["qualified_days"] == exact["qualified_days"]


def test_calculate_atr_never_shrinks_requested_denominator() -> None:
    bars = [
        {"date": "2026-01-01", "open": 10, "close": 10, "high": 11, "low": 9},
        {"date": "2026-01-02", "open": 10, "close": 10, "high": 12, "low": 8},
        {"date": "2026-01-03", "open": 10, "close": 10, "high": 13, "low": 7},
        {"date": "2026-01-04", "open": 10, "close": 10, "high": 14, "low": 6},
    ]
    assert calculate_atr_screen_metrics(bars, make_rule()) is None


def test_screen_spec_rejects_incomplete_or_inconsistent_conditions() -> None:
    with pytest.raises(ValidationError, match="达标天数和达标比例"):
        make_rule(min_qualified_days=None, min_qualified_ratio_pct=None)
    with pytest.raises(ValidationError, match="不能大于"):
        make_rule(lookback_days=5, min_qualified_days=6)
    with pytest.raises(ValidationError):
        make_spec(
            technical_rule={"strategy": "atr_relative_frequency", "atr_period": 20}
        )


def test_build_ttm_financials_keeps_available_fields_independently():
    from market_data_service.providers.financial_sync import _ttm_fields

    periods = {
        "2025-12-31": {
            "000001": {
                "TOTALOPERATEREVE": 100,
                "PARENTNETPROFIT": 90,
                "KCFJCXSYJLR": 10,
            }
        },
        "2025-03-31": {
            "000001": {"TOTALOPERATEREVE": 15, "PARENTNETPROFIT": 12, "KCFJCXSYJLR": 3}
        },
    }
    result = _ttm_fields(
        "000001",
        "2026-03-31",
        {"TOTALOPERATEREVE": 20, "PARENTNETPROFIT": 18, "KCFJCXSYJLR": 4},
        periods,
    )
    assert result == {
        "revenue_ttm": 105,
        "parent_net_profit_ttm": 96,
        "deducted_net_profit_ttm": 11,
    }
    result = _ttm_fields(
        "000001",
        "2026-03-31",
        {"TOTALOPERATEREVE": 20, "PARENTNETPROFIT": None},
        periods,
    )
    assert result == {"revenue_ttm": 105}


def test_financial_service_requires_four_consecutive_quarters(monkeypatch):
    from market_data_service.providers.financial_sync import (
        _fallback_update_from_financials,
    )

    rows = [
        {
            "report_date": day,
            "revenue": 10,
            "parent_net_profit": 6,
            "deducted_profit": 2,
            "debt_ratio": 44,
        }
        for day in ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
    ]
    monkeypatch.setattr(
        "market_data_service.providers.financials.get_financials",
        lambda *a, **k: {"items": rows},
    )
    assert _fallback_update_from_financials("000001")["revenue_ttm"] == 40
    rows.pop(1)
    assert "revenue_ttm" not in _fallback_update_from_financials("000001")


def test_financial_service_does_not_mix_absent_quarter_fields(monkeypatch):
    from market_data_service.providers.financial_sync import (
        _fallback_update_from_financials,
    )

    rows = [
        {
            "report_date": day,
            "revenue": 10,
            "parent_net_profit": 6,
            "deducted_profit": 2,
            "debt_ratio": 44,
        }
        for day in ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
    ]
    rows[0]["deducted_profit"] = None
    monkeypatch.setattr(
        "market_data_service.providers.financials.get_financials",
        lambda *a, **k: {"items": rows},
    )
    result = _fallback_update_from_financials("000001")
    assert result["revenue_ttm"] == 40 and "deducted_net_profit_ttm" not in result


def test_export_headers_follow_caller_periods_and_requested_fields(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(screener, "EXPORT_DIR", tmp_path)
    spec = make_spec(
        technical_rule=make_rule(
            baseline_period=3,
            lookback_days=4,
            min_qualified_days=2,
            min_qualified_ratio_pct=50,
        ).model_dump(mode="json")
    )
    columns = screener._column_defs(spec)
    file_id, url = screener._write_export(
        [
            {
                "code": "000001",
                "name": "平安银行",
                "current_atr_pct": 1.2,
                "long_term_mean_pct": 1.3,
                "dynamic_warning_pct": 1.02,
                "qualified_days": 3,
                "qualified_ratio_pct": 75,
                "revenue_ttm": 1_000_000_000,
                "financial_report_period": "2026-03-31",
                "financial_source": "东方财富财务主指标",
                "latest_trade_date": "2026-07-17",
            }
        ],
        columns,
        "abcdef1234567890",
    )

    assert url == f"/api/v1/agent/exports/{file_id}"
    with (tmp_path / file_id).open(encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    assert "3日长期波动均值(%)" in rows[0]
    assert "近4日达标比例(%)" in rows[0]
    assert "60日长期均值(%)" not in rows[0]
    assert rows[0][-1] == "筛选规格指纹"
    assert rows[1][0] == '="000001"'
    assert rows[1][-1] == "abcdef1234567890"
    assert "东方财富财务主指标" in rows[1]


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("code", "000792", '="000792"'),
        ("code", "600482", '="600482"'),
        ("name", "000792", "000792"),
        ("code", "invalid", "invalid"),
    ],
)
def test_export_cell_value_preserves_six_digit_stock_codes(
    field: str,
    value: str,
    expected: str,
) -> None:
    assert screener._export_cell_value(field, value) == expected


def test_executor_rejects_missing_screen_spec_without_running_sources(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        screener,
        "financial_rows",
        lambda *args: pytest.fail("invalid spec must fail before data access"),
    )
    result = screener.run_atr_volatility_screen(
        screen_spec={"version": "1.0"},
        refresh_if_stale=True,
    )
    assert result["success"] is False
    assert result["failure_stage"] == "spec_validation"
    assert result["items"] == []


def _install_fake_market(monkeypatch, financials):
    from types import SimpleNamespace

    rows = [
        {"code": code, "name": "样本" + code, "ipo_date": "2010-01-01"}
        for code in ("000001", "000002")
    ]
    bars = [
        {"date": f"2026-01-{i:02d}", "open": 10, "close": 10, "high": high, "low": low}
        for i, (high, low) in enumerate(
            [(11, 9), (11, 9), (12, 8), (13, 7), (14, 6), (15, 5)], 1
        )
    ]
    monkeypatch.setattr(
        screener,
        "get_market_data_client",
        lambda: SimpleNamespace(securities=lambda **k: {"items": rows}),
    )
    monkeypatch.setattr(
        "src.services.data_maintenance.ensure_stock_universe",
        lambda **k: {"maintenance_status": "ready", "is_stale": False},
    )
    monkeypatch.setattr(
        screener, "_expected_latest_kline_date", lambda: date(2026, 1, 6)
    )
    monkeypatch.setattr(
        screener, "financial_rows", lambda codes: (financials, "2025-12-31")
    )
    monkeypatch.setattr(
        screener,
        "daily_rows",
        lambda codes, count: (
            {code: bars for code in codes},
            {},
            {"market-data-service": len(codes)},
        ),
    )


def test_daily_batch_preserves_failure_without_direct_source_retry(monkeypatch):
    from src.services.stock_screening import data
    from src.services.market_data_client import DataNotReady
    from unittest.mock import Mock

    client = Mock()
    client.snapshot.side_effect = DataNotReady({"not_ready": {"000001": ["kline"]}})
    monkeypatch.setattr(data, "get_market_data_client", lambda: client)
    bars, failures, sources = data.daily_rows(["000001"], 30)
    assert not bars and "000001" in failures and not sources
    client.snapshot.assert_called_once_with(["000001"], ["kline"], count=30, wait=1)


def test_executor_applies_changed_financial_threshold_instead_of_example_default(
    monkeypatch,
) -> None:
    financials = {
        "000001": {
            "revenue_ttm": 600_000_000,
            "financial_report_period": "2025-12-31",
            "financial_source": "测试源",
        },
        "000002": {
            "revenue_ttm": 1_200_000_000,
            "financial_report_period": "2025-12-31",
            "financial_source": "测试源",
        },
    }
    _install_fake_market(monkeypatch, financials)
    base = make_spec()
    changed = make_spec(
        financial_filters=[
            {"field": "revenue_ttm", "operator": "gt", "value": 1_000_000_000},
        ]
    )

    base_result = screener.run_atr_volatility_screen(
        screen_spec=base.model_dump(mode="json"),
        refresh_if_stale=True,
    )
    changed_result = screener.run_atr_volatility_screen(
        screen_spec=changed.model_dump(mode="json"),
        refresh_if_stale=True,
    )

    assert base_result["success"] is True and base_result["total"] == 2
    assert changed_result["success"] is True and changed_result["total"] == 1
    assert changed_result["items"][0]["code"] == "000002"
    assert (
        changed_result["screen_spec"]["financial_filters"][0]["value"] == 1_000_000_000
    )
    assert "营业收入TTM>10亿元" in changed_result["applied_rules"]
    assert any("至少需要6根日线" in rule for rule in changed_result["applied_rules"])
    assert base_result["spec_fingerprint"] != changed_result["spec_fingerprint"]


def test_executor_fails_closed_until_the_data_service_recovers_kline(monkeypatch):
    financials = {
        code: {"revenue_ttm": 1_200_000_000, "financial_report_period": "2025-12-31"}
        for code in ("000001", "000002")
    }
    _install_fake_market(monkeypatch, financials)
    monkeypatch.setattr(
        screener, "daily_rows", lambda *a: ({}, {"000001": "待补采"}, {})
    )
    result = screener.run_atr_volatility_screen(
        screen_spec=make_spec().model_dump(mode="json")
    )
    assert result["success"] is False and result["failure_stage"] == "kline_coverage"
    assert result["items"] == [] and result["failed_symbols"]


def test_executor_does_not_fetch_unused_financial_data(monkeypatch) -> None:
    _install_fake_market(monkeypatch, {})
    monkeypatch.setattr(
        screener,
        "financial_rows",
        lambda *args: pytest.fail(
            "technical-only screen must not fetch financial data"
        ),
    )
    spec = make_spec(
        financial_filters=[],
        output_fields=[
            "current_atr_pct",
            "qualified_days",
            "qualified_ratio_pct",
            "latest_trade_date",
        ],
    )

    result = screener.run_atr_volatility_screen(
        screen_spec=spec.model_dump(mode="json"),
        refresh_if_stale=True,
    )

    assert result["success"] is True
    assert result["coverage"]["financial_covered"] is None
    assert "东方财富财务主指标" not in result["source"]


def test_executor_does_not_bypass_unavailable_financial_service(monkeypatch):
    _install_fake_market(monkeypatch, {})

    def unavailable(codes):
        raise ConnectionError("data service unavailable")

    monkeypatch.setattr(screener, "financial_rows", unavailable)
    monkeypatch.setattr(
        screener,
        "daily_rows",
        lambda *a: pytest.fail("no downstream work after failed readiness"),
    )
    result = screener.run_atr_volatility_screen(
        screen_spec=make_spec().model_dump(mode="json")
    )
    assert not result["success"] and result["failure_stage"] == "data_readiness"


def test_executor_fails_closed_when_financial_fields_lack_coverage(monkeypatch):
    _install_fake_market(monkeypatch, {})
    monkeypatch.setattr(
        screener,
        "daily_rows",
        lambda *a: pytest.fail("incomplete financial universe cannot be screened"),
    )
    result = screener.run_atr_volatility_screen(
        screen_spec=make_spec().model_dump(mode="json")
    )
    assert not result["success"] and result["failure_stage"] == "financial_coverage"
    assert result["coverage"]["financial_covered"] == 0 and result["items"] == []
