from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from src.tools.get_multi_stock_financials import (
    _load_period_snapshot,
    read_annual_financial_snapshot_eastmoney,
    read_local_financial_snapshot,
)


def _resolved() -> list[dict[str, str]]:
    return [
        {"input": "甲公司", "name": "甲公司", "symbol": "000001"},
        {"input": "乙公司", "name": "乙公司", "symbol": "000002"},
    ]


def test_financial_snapshot_reads_only_service_versions():
    rows = {
        code: {
            "financials": {
                "report_date": "2026-06-30",
                "debt_ratio": ratio,
                "revenue_ttm": rev,
            },
            "versions": {"financials": "v" + code},
        }
        for code, ratio, rev in [("000001", 69.9, 10), ("000002", 75.5, 20)]
    }
    client = MagicMock()
    client.snapshot.return_value = {"items": rows}
    with (
        patch(
            "src.tools.get_multi_stock_financials.resolve_local_securities_csv",
            return_value=(_resolved(), []),
        ),
        patch(
            "src.services.market_data_client.get_market_data_client",
            return_value=client,
        ),
        patch(
            "src.storage.DatabaseManager.get_instance",
            side_effect=AssertionError("business SQL forbidden"),
        ),
    ):
        result = read_local_financial_snapshot("甲公司,乙公司")
    client.snapshot.assert_called_once_with(["000001", "000002"], ["financials"])
    assert result["covered_count"] == 2 and [
        r["debt_ratio_pct"] for r in result["items"]
    ] == [69.9, 75.5]
    assert result["source"] == "market-data-service" and result["items"][0][
        "data_versions"
    ] == {"financials": "v000001"}


def test_financial_snapshot_marks_missing_source_rows_as_partial():
    client = MagicMock()
    client.snapshot.return_value = {
        "items": {
            "000001": {
                "financials": {"report_date": "2026-06-30", "debt_ratio": 50},
                "versions": {},
            }
        }
    }
    with (
        patch(
            "src.tools.get_multi_stock_financials.resolve_local_securities_csv",
            return_value=(_resolved(), []),
        ),
        patch(
            "src.services.market_data_client.get_market_data_client",
            return_value=client,
        ),
    ):
        result = read_local_financial_snapshot("甲公司,乙公司")
    assert (
        result["success"]
        and result["partial"]
        and result["missing_symbols"] == ["000002"]
    )


def test_financial_snapshot_fails_cleanly_when_no_rows_exist():
    client = MagicMock()
    client.snapshot.return_value = {"items": {}}
    with (
        patch(
            "src.tools.get_multi_stock_financials.resolve_local_securities_csv",
            return_value=([_resolved()[0]], []),
        ),
        patch(
            "src.services.market_data_client.get_market_data_client",
            return_value=client,
        ),
    ):
        result = read_local_financial_snapshot("甲公司")
    assert (
        not result["success"]
        and not result["partial"]
        and result["missing_symbols"] == ["000001"]
    )


def test_annual_financial_snapshot_reads_one_explicit_eastmoney_period() -> None:
    annual_rows = {
        "000001": {
            "SECURITY_CODE": "000001",
            "REPORT_DATE": "2025-12-31",
            "TOTALOPERATEREVE": 499_000_000.0,
            "PARENTNETPROFIT": -10_000_000.0,
            "KCFJCXSYJLR": 5_000_000.0,
            "ZCFZL": 48.0,
        },
        "000002": {
            "SECURITY_CODE": "000002",
            "REPORT_DATE": "2025-12-31",
            "TOTALOPERATEREVE": 800_000_000.0,
            "PARENTNETPROFIT": 20_000_000.0,
            "KCFJCXSYJLR": -5_000_000.0,
            "ZCFZL": 52.0,
        },
    }

    with (
        patch(
            "src.tools.get_multi_stock_financials.resolve_local_securities_csv",
            return_value=(_resolved(), []),
        ),
        patch(
            "src.tools.get_multi_stock_financials._load_period_snapshot",
            return_value=(annual_rows, datetime(2026, 7, 21, 13, 0, 0)),
        ) as snapshot,
    ):
        result = read_annual_financial_snapshot_eastmoney(
            "甲公司,乙公司", fiscal_year=2025
        )

    snapshot.assert_called_once_with("2025-12-31")
    assert result["success"] is True
    assert result["fiscal_year"] == 2025
    assert [item["revenue"] for item in result["items"]] == [
        499_000_000.0,
        800_000_000.0,
    ]
    assert [item["net_profit"] for item in result["items"]] == [
        -10_000_000.0,
        20_000_000.0,
    ]
    assert [item["deducted_net_profit"] for item in result["items"]] == [
        5_000_000.0,
        -5_000_000.0,
    ]
    assert all(item["report_date"] == "2025-12-31" for item in result["items"])
    assert result["data_time"] == "2025-12-31"
    assert result["data_time_provenance"] == "source"


def test_period_cache_and_checked_time_belong_to_data_service():
    rows = {"000001": {"SECURITY_CODE": "000001", "TOTALOPERATEREVE": 1}}
    with patch(
        "src.services.market_data_client.read_source",
        return_value={
            "data": rows,
            "data_service": {
                "checked_at": "2026-09-07T01:00:00Z",
                "version": "immutable",
            },
        },
    ) as read:
        result, checked = _load_period_snapshot("2025-12-31")
    read.assert_called_once_with("financials.fetch_period", {"period": "2025-12-31"})
    assert result == rows and checked.isoformat() == "2026-09-07T01:00:00+00:00"
