from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.tools.get_multi_stock_financials import get_multi_stock_financials


def test_multi_stock_financials_reads_only_local_synchronized_rows() -> None:
    resolved = [
        {"input": "甲公司", "name": "甲公司", "symbol": "000001"},
        {"input": "乙公司", "name": "乙公司", "symbol": "000002"},
    ]
    rows = [
        SimpleNamespace(
            code="000001", report_date="2026-03-31", debt_ratio=69.9,
            revenue_ttm=10.0, deducted_net_profit_ttm=1.0,
            financial_fetched_at=datetime(2026, 7, 18, 22, 42, 40),
        ),
        SimpleNamespace(
            code="000002", report_date="2026-03-31", debt_ratio=75.5,
            revenue_ttm=20.0, deducted_net_profit_ttm=2.0,
            financial_fetched_at=datetime(2026, 7, 18, 22, 42, 40),
        ),
    ]
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = rows
    db = MagicMock()
    db.get_session.return_value = nullcontext(session)

    with patch(
        "src.tools.get_multi_stock_financials.resolve_securities_csv",
        return_value=(resolved, []),
    ), patch("src.storage.DatabaseManager.get_instance", return_value=db):
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
    rows = [SimpleNamespace(
        code="000001", report_date="2026-03-31", debt_ratio=50.0,
        revenue_ttm=None, deducted_net_profit_ttm=None, financial_fetched_at=None,
    )]
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = rows
    db = MagicMock()
    db.get_session.return_value = nullcontext(session)

    with patch(
        "src.tools.get_multi_stock_financials.resolve_securities_csv",
        return_value=(resolved, []),
    ), patch("src.storage.DatabaseManager.get_instance", return_value=db):
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

    with patch(
        "src.tools.get_multi_stock_financials.resolve_securities_csv",
        return_value=(resolved, []),
    ), patch("src.storage.DatabaseManager.get_instance", return_value=db):
        result = get_multi_stock_financials("甲公司")

    assert result["success"] is False
    assert result["partial"] is False
    assert result["missing_financial_symbols"] == ["000001"]
