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
        for i, (high, low) in enumerate([(11, 9), (11, 9), (12, 8), (13, 7), (14, 6), (15, 5)], 1)
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
        for i, (high, low) in enumerate([(101, 99), (101, 99), (102, 98), (103, 97), (104, 96), (105, 95)], 1)
    ]

    result = calculate_atr_screen_metrics(bars, make_rule(volatility_threshold_pct=8.5))

    assert result is not None
    assert result["dynamic_warning_pct"] == pytest.approx(8.5)
    assert result["qualified_days"] == 1
    assert result["qualified_ratio_pct"] == pytest.approx(100 / 3)


def test_calculate_atr_changes_when_user_changes_average_and_threshold() -> None:
    ranges = [1, 7, 2, 9, 3, 4, 11, 2]
    bars = [
        {"date": f"2026-01-{i:02d}", "open": 20, "close": 20, "high": 20 + width, "low": 20 - width / 2}
        for i, width in enumerate(ranges, 1)
    ]
    sma = calculate_atr_screen_metrics(bars, make_rule(lookback_days=4, min_qualified_days=0))
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
        {"date": f"2026-01-{index:02d}", "open": 20, "close": 20, "high": 20 + width, "low": 20 - width / 2}
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
        make_spec(technical_rule={"strategy": "atr_relative_frequency", "atr_period": 20})


def test_build_ttm_financials_keeps_available_fields_independently(monkeypatch) -> None:
    rows = {
        "2026-03-31": {
            "000001": {"TOTALOPERATEREVE": 20, "PARENTNETPROFIT": 18, "KCFJCXSYJLR": 4, "ZCFZL": 50},
            "000002": {"TOTALOPERATEREVE": 30, "PARENTNETPROFIT": None, "KCFJCXSYJLR": None, "ZCFZL": 40},
        },
        "2025-12-31": {
            "000001": {"TOTALOPERATEREVE": 100, "PARENTNETPROFIT": 90, "KCFJCXSYJLR": 10},
            "000002": {"TOTALOPERATEREVE": 80, "PARENTNETPROFIT": None, "KCFJCXSYJLR": None},
        },
        "2025-03-31": {
            "000001": {"TOTALOPERATEREVE": 15, "PARENTNETPROFIT": 12, "KCFJCXSYJLR": 3},
            "000002": {"TOTALOPERATEREVE": 20, "KCFJCXSYJLR": None},
        },
    }
    monkeypatch.setattr(screener, "_fetch_financial_period", lambda period: rows[period])

    result, period = screener._build_ttm_financials(date(2026, 7, 18))

    assert period == "2026-03-31"
    assert result["000001"]["revenue_ttm"] == 105.0
    assert result["000001"]["parent_net_profit_ttm"] == 96.0
    assert result["000001"]["deducted_net_profit_ttm"] == 11.0
    assert result["000002"]["revenue_ttm"] == 90.0
    assert "deducted_net_profit_ttm" not in result["000002"]
    assert result["000002"]["debt_ratio"] == 40.0


def test_sina_fallback_builds_ttm_only_from_four_consecutive_quarters(monkeypatch) -> None:
    import importlib

    financial_fetcher = importlib.import_module("api.v1.endpoints.financials._fetch_financials")

    rows = [
        {"report_date": "2025-03-31", "revenue": 10, "parent_net_profit": 6, "deducted_profit": 1, "debt_ratio": 41},
        {"report_date": "2025-06-30", "revenue": 20, "parent_net_profit": 7, "deducted_profit": 2, "debt_ratio": 42},
        {"report_date": "2025-09-30", "revenue": 30, "parent_net_profit": 8, "deducted_profit": 3, "debt_ratio": 43},
        {"report_date": "2025-12-31", "revenue": 40, "parent_net_profit": 10, "deducted_profit": 4, "debt_ratio": 44},
    ]
    monkeypatch.setattr(financial_fetcher, "_fetch_from_sina", lambda _code, _periods: rows)

    result = screener._fetch_sina_ttm_financial("000001")

    assert result == {
        "financial_report_period": "2025-12-31",
        "financial_source": "新浪财经财务摘要补源",
        "debt_ratio": 44.0,
        "revenue_ttm": 100.0,
        "parent_net_profit_ttm": 31.0,
        "deducted_net_profit_ttm": 10.0,
    }


def test_secondary_financial_fallback_uses_sina_after_ths_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        screener,
        "_fetch_ths_ttm_financial",
        lambda _code: (_ for _ in ()).throw(ConnectionError("ths down")),
    )
    monkeypatch.setattr(
        screener,
        "_fetch_sina_ttm_financial",
        lambda _code: {
            "revenue_ttm": 1,
            "deducted_net_profit_ttm": 1,
            "debt_ratio": 50,
            "financial_report_period": "2025-12-31",
            "financial_source": "新浪财经财务摘要补源",
        },
    )

    result = screener._fetch_secondary_ttm_financial("000001", {"revenue_ttm", "deducted_net_profit_ttm", "debt_ratio"})

    assert result is not None
    assert result["financial_source"] == "新浪财经财务摘要补源"


def test_export_headers_follow_caller_periods_and_requested_fields(tmp_path, monkeypatch) -> None:
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


def test_executor_rejects_missing_screen_spec_without_running_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        screener,
        "_build_ttm_financials",
        lambda: pytest.fail("invalid spec must fail before data access"),
    )
    result = screener.run_atr_volatility_screen(
        screen_spec={"version": "1.0"},
        refresh_if_stale=True,
    )
    assert result["success"] is False
    assert result["failure_stage"] == "spec_validation"
    assert result["items"] == []


def _install_fake_market(monkeypatch, financials: dict[str, dict]) -> None:
    class Result:
        def mappings(self):
            return self

        def all(self):
            return [
                {"code": "000001", "name": "样本一", "ipo_date": "2010-01-01"},
                {"code": "000002", "name": "样本二", "ipo_date": "2010-01-01"},
            ]

    class Session:
        def execute(self, *_args, **_kwargs):
            return Result()

    class DB:
        @contextmanager
        def session_scope(self):
            yield Session()

    bars = [
        {"date": f"2026-01-{index:02d}", "open": 10, "close": 10, "high": high, "low": low}
        for index, (high, low) in enumerate([(11, 9), (11, 9), (12, 8), (13, 7), (14, 6), (15, 5)], 1)
    ]
    monkeypatch.setattr(screener.DatabaseManager, "get_instance", classmethod(lambda _cls: DB()))
    monkeypatch.setattr(
        "src.services.data_maintenance.ensure_stock_universe",
        lambda **_kwargs: {
            "maintenance_status": "ready",
            "refreshed": False,
            "is_stale": False,
        },
    )
    monkeypatch.setattr(screener, "_expected_latest_kline_date", lambda: date(2026, 1, 5))
    monkeypatch.setattr(screener, "_build_ttm_financials", lambda: (financials, "2025-12-31"))
    monkeypatch.setattr(screener, "_persist_financials", lambda *_args: None)
    monkeypatch.setattr(screener, "_fetch_tencent_bars", lambda *_args: [])
    monkeypatch.setattr(
        screener,
        "_fetch_adjusted_bars",
        lambda code, _count, _allow: (code, bars, None, "测试行情源"),
    )


def test_kline_fetch_retries_transient_source_error(monkeypatch) -> None:
    attempts = {"count": 0}
    bars = [
        {"date": "2026-07-17", "open": 10, "close": 10, "high": 11, "low": 9},
    ]

    def transient(_code, _count):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("temporary reset")
        return bars

    monkeypatch.setattr(screener, "_fetch_tencent_bars", transient)
    monkeypatch.setattr(
        screener,
        "_fetch_sina_bars",
        lambda *_args: pytest.fail("retry should recover first source"),
    )
    monkeypatch.setattr(screener.time, "sleep", lambda *_args: None)

    code, result, error, source = screener._fetch_adjusted_bars("000001", 10, True)

    assert code == "000001" and result == bars and error is None
    assert source == "腾讯前复权日线"
    assert attempts["count"] == 3


def test_executor_applies_changed_financial_threshold_instead_of_example_default(monkeypatch) -> None:
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
    assert changed_result["screen_spec"]["financial_filters"][0]["value"] == 1_000_000_000
    assert "营业收入TTM>10亿元" in changed_result["applied_rules"]
    assert any("至少需要6根日线" in rule for rule in changed_result["applied_rules"])
    assert base_result["spec_fingerprint"] != changed_result["spec_fingerprint"]


def test_executor_recovers_small_transient_kline_tail_on_second_pass(monkeypatch) -> None:
    financials = {
        code: {
            "revenue_ttm": 1_200_000_000,
            "financial_report_period": "2025-12-31",
            "financial_source": "测试源",
        }
        for code in ("000001", "000002")
    }
    _install_fake_market(monkeypatch, financials)
    bars = [
        {"date": f"2026-01-{index:02d}", "open": 10, "close": 10, "high": high, "low": low}
        for index, (high, low) in enumerate([(11, 9), (11, 9), (12, 8), (13, 7), (14, 6), (15, 5)], 1)
    ]
    attempts: dict[str, int] = {}

    def fetch(code, _count, _allow):
        attempts[code] = attempts.get(code, 0) + 1
        if code == "000001" and attempts[code] == 1:
            return code, [], "新浪:SSLError", None
        return code, bars, None, "测试行情源"

    monkeypatch.setattr(screener, "_fetch_adjusted_bars", fetch)
    monkeypatch.setattr(screener.time, "sleep", lambda *_args: None)

    result = screener.run_atr_volatility_screen(
        screen_spec=make_spec().model_dump(mode="json"),
        refresh_if_stale=True,
    )

    assert result["success"] is True
    assert result["coverage"]["kline_retry_recovered"] == 1
    assert result["coverage"]["fresh_kline"] == 2
    assert attempts["000001"] == 2


def test_executor_does_not_fetch_unused_financial_data(monkeypatch) -> None:
    _install_fake_market(monkeypatch, {})
    monkeypatch.setattr(
        screener,
        "_build_ttm_financials",
        lambda: pytest.fail("technical-only screen must not fetch financial data"),
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


def test_executor_switches_to_same_day_financial_snapshot_when_primary_fails(monkeypatch) -> None:
    cached = {
        "000001": {
            "revenue_ttm": 600_000_000,
            "financial_report_period": "2026-03-31",
            "financial_source": "本地当日财务缓存",
        },
        "000002": {
            "revenue_ttm": 1_200_000_000,
            "financial_report_period": "2026-03-31",
            "financial_source": "本地当日财务缓存",
        },
    }
    _install_fake_market(monkeypatch, {})
    monkeypatch.setattr(
        screener,
        "_build_ttm_financials",
        lambda: (_ for _ in ()).throw(ConnectionError("primary reset")),
    )
    monkeypatch.setattr(
        screener,
        "_load_fresh_cached_financials",
        lambda _codes: (cached, "2026-03-31"),
    )

    result = screener.run_atr_volatility_screen(
        screen_spec=make_spec().model_dump(mode="json"),
        refresh_if_stale=True,
    )

    assert result["success"] is True
    assert result["total"] == 2
    assert result["coverage"]["financial_cache_count"] == 2
    assert "本地当日财务快照" in result["source"]
    assert "已自动切换" in result["warnings"][0]


def test_executor_fails_closed_when_primary_and_same_day_snapshot_lack_coverage(monkeypatch) -> None:
    _install_fake_market(monkeypatch, {})
    monkeypatch.setattr(screener, "MAX_SECONDARY_FINANCIAL_FALLBACKS", 1)
    monkeypatch.setattr(
        screener,
        "_build_ttm_financials",
        lambda: (_ for _ in ()).throw(ConnectionError("primary reset")),
    )
    monkeypatch.setattr(
        screener,
        "_load_fresh_cached_financials",
        lambda _codes: ({}, None),
    )
    monkeypatch.setattr(
        screener,
        "_fetch_secondary_ttm_financial",
        lambda _code: pytest.fail("cold-cache bulk outage must not fan out per-company calls"),
    )

    result = screener.run_atr_volatility_screen(
        screen_spec=make_spec().model_dump(mode="json"),
        refresh_if_stale=True,
    )

    assert result["success"] is False
    assert result["failure_stage"] == "financial_cache_coverage"
    assert result["coverage"]["financial_covered"] == 0
    assert result["items"] == []
