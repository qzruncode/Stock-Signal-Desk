from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.tools.get_multi_stock_financials import (
    _PERIOD_MEMORY_CACHE,
    _load_period_snapshot,
    get_multi_stock_financials,
)


def test_multi_stock_financials_reads_only_local_synchronized_rows() -> None:
    resolved = [
        {"input": "甲公司", "name": "甲公司", "symbol": "000001"},
        {"input": "乙公司", "name": "乙公司", "symbol": "000002"},
    ]
    rows = [
        SimpleNamespace(
            code="000001",
            report_date="2026-03-31",
            debt_ratio=69.9,
            revenue_ttm=10.0,
            deducted_net_profit_ttm=1.0,
            financial_fetched_at=datetime(2026, 7, 18, 22, 42, 40),
        ),
        SimpleNamespace(
            code="000002",
            report_date="2026-03-31",
            debt_ratio=75.5,
            revenue_ttm=20.0,
            deducted_net_profit_ttm=2.0,
            financial_fetched_at=datetime(2026, 7, 18, 22, 42, 40),
        ),
    ]
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = rows
    db = MagicMock()
    db.get_session.return_value = nullcontext(session)

    with (
        patch(
            "src.tools.get_multi_stock_financials.resolve_securities_csv",
            return_value=(resolved, []),
        ),
        patch("src.storage.DatabaseManager.get_instance", return_value=db),
    ):
        result = get_multi_stock_financials("甲公司,乙公司")

    assert result["success"] is True
    assert result["covered_count"] == 2
    assert [item["debt_ratio_pct"] for item in result["items"]] == [69.9, 75.5]
    assert result["source"].startswith("stock_meta 本地")
    assert result["data_time"] == "2026-07-18T22:42:40"


def test_multi_stock_financials_marks_missing_metric_as_partial() -> None:
    resolved = [
        {"input": "甲公司", "name": "甲公司", "symbol": "000001"},
        {"input": "乙公司", "name": "乙公司", "symbol": "000002"},
    ]
    rows = [
        SimpleNamespace(
            code="000001",
            report_date="2026-03-31",
            debt_ratio=50.0,
            revenue_ttm=None,
            deducted_net_profit_ttm=None,
            financial_fetched_at=None,
        )
    ]
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = rows
    db = MagicMock()
    db.get_session.return_value = nullcontext(session)

    with (
        patch(
            "src.tools.get_multi_stock_financials.resolve_securities_csv",
            return_value=(resolved, []),
        ),
        patch("src.storage.DatabaseManager.get_instance", return_value=db),
    ):
        result = get_multi_stock_financials("甲公司,乙公司")

    assert result["success"] is True
    assert result["partial"] is True
    assert result["missing_financial_symbols"] == ["000002"]


def test_multi_stock_financials_all_missing_is_a_clean_failure() -> None:
    resolved = [{"input": "甲公司", "name": "甲公司", "symbol": "000001"}]
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = []
    db = MagicMock()
    db.get_session.return_value = nullcontext(session)

    with (
        patch(
            "src.tools.get_multi_stock_financials.resolve_securities_csv",
            return_value=(resolved, []),
        ),
        patch("src.storage.DatabaseManager.get_instance", return_value=db),
    ):
        result = get_multi_stock_financials("甲公司")

    assert result["success"] is False
    assert result["partial"] is False
    assert result["missing_financial_symbols"] == ["000001"]


def test_multi_stock_financials_reads_exact_previous_fiscal_year_revenue() -> None:
    resolved = [
        {"input": "甲公司", "name": "甲公司", "symbol": "000001"},
        {"input": "乙公司", "name": "乙公司", "symbol": "000002"},
    ]
    annual_rows = {
        "000001": {
            "SECURITY_CODE": "000001",
            "REPORT_DATE": "2025-12-31",
            "TOTALOPERATEREVE": 499_000_000.0,
        },
        "000002": {
            "SECURITY_CODE": "000002",
            "REPORT_DATE": "2025-12-31",
            "TOTALOPERATEREVE": 800_000_000.0,
        },
    }

    with (
        patch(
            "src.tools.get_multi_stock_financials.resolve_securities_csv",
            return_value=(resolved, []),
        ),
        patch(
            "src.tools.get_multi_stock_financials._load_period_snapshot",
            return_value=(annual_rows, datetime(2026, 7, 21, 13, 0, 0)),
        ) as snapshot,
    ):
        result = get_multi_stock_financials(
            "甲公司,乙公司",
            metric="revenue",
            period_basis="previous_fiscal_year",
        )

    snapshot.assert_called_once_with("2025-12-31")
    assert result["success"] is True
    assert result["requested_fiscal_year"] == 2025
    assert [item["financial_value"] for item in result["items"]] == [
        499_000_000.0,
        800_000_000.0,
    ]
    assert all(item["value_unit"] == "cny" for item in result["items"])
    assert all(item["report_date"] == "2025-12-31" for item in result["items"])


def test_multi_stock_financials_reads_net_profit_without_substituting_deducted_profit() -> None:
    resolved = [
        {"input": "甲公司", "name": "甲公司", "symbol": "000001"},
        {"input": "乙公司", "name": "乙公司", "symbol": "000002"},
    ]
    annual_rows = {
        "000001": {
            "SECURITY_CODE": "000001",
            "REPORT_DATE": "2025-12-31",
            "PARENTNETPROFIT": -10_000_000.0,
            "KCFJCXSYJLR": 5_000_000.0,
        },
        "000002": {
            "SECURITY_CODE": "000002",
            "REPORT_DATE": "2025-12-31",
            "PARENTNETPROFIT": 20_000_000.0,
            "KCFJCXSYJLR": -5_000_000.0,
        },
    }

    with (
        patch(
            "src.tools.get_multi_stock_financials.resolve_securities_csv",
            return_value=(resolved, []),
        ),
        patch(
            "src.tools.get_multi_stock_financials._load_period_snapshot",
            return_value=(annual_rows, datetime(2026, 7, 21, 13, 0, 0)),
        ),
    ):
        result = get_multi_stock_financials(
            "甲公司,乙公司",
            metric="net_profit",
            period_basis="previous_fiscal_year",
        )

    assert result["success"] is True
    assert [item["financial_value"] for item in result["items"]] == [
        -10_000_000.0,
        20_000_000.0,
    ]
    assert result["field_basis"]["financial_value"].startswith("归母净利润")


def test_financial_period_snapshot_is_fetched_once_for_sequential_batches() -> None:
    period = "2025-12-31"
    rows = {"000001": {"SECURITY_CODE": "000001", "TOTALOPERATEREVE": 1.0}}
    db = MagicMock()
    db.get_tool_cache.return_value = None
    _PERIOD_MEMORY_CACHE.pop(period, None)

    try:
        with (
            patch("src.storage.DatabaseManager.get_instance", return_value=db),
            patch(
                "src.services.stock_screening.atr_volatility_screener.fetch_financial_period_snapshot",
                return_value=rows,
            ) as fetch,
        ):
            first, first_time = _load_period_snapshot(period)
            second, second_time = _load_period_snapshot(period)
    finally:
        _PERIOD_MEMORY_CACHE.pop(period, None)

    assert first == second == rows
    assert first_time == second_time
    fetch.assert_called_once_with(period)
    db.save_tool_cache.assert_called_once()
