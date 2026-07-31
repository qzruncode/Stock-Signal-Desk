from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from src.tools.get_multi_stock_snapshot import get_multi_stock_snapshot
from src.tools.get_technical_indicators import get_technical_indicators


def _daily_rows(count: int = 80) -> list[dict]:
    start = date.today() - timedelta(days=count - 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "open": 10 + index * 0.1,
            "high": 10.5 + index * 0.1,
            "low": 9.5 + index * 0.1,
            "close": 10.2 + index * 0.1,
            "volume": 100_000 + index * 1_000,
        }
        for index in range(count)
    ]


def test_technical_indicators_use_sufficient_local_history_without_network():
    rows = _daily_rows()
    with (
        patch(
            "src.tools.get_technical_indicators._get_kline_from_stock_daily",
            return_value=rows,
        ),
        patch(
            "src.tools.get_technical_indicators.get_kline",
            side_effect=AssertionError("external K-line must not run"),
        ),
    ):
        result = get_technical_indicators("300508")

    assert result["success"] is True
    assert result["source"] == "stock_daily"
    assert result["indicators"]["ma20"] is not None


def test_technical_indicators_refresh_stale_local_history_before_calculating():
    stale_rows = _daily_rows()
    stale_rows[-1]["date"] = (date.today() - timedelta(days=5)).isoformat()
    refreshed_rows = _daily_rows()
    with (
        patch(
            "src.tools.get_technical_indicators._get_kline_from_stock_daily",
            return_value=stale_rows,
        ),
        patch(
            "src.tools.get_technical_indicators.get_kline",
            return_value={
                "success": True,
                "data": refreshed_rows,
                "source": "fresh-test-source",
                "data_time": refreshed_rows[-1]["date"],
                "is_stale": False,
                "fallback_used": False,
                "_cached": False,
                "bar_complete": True,
            },
        ) as refresh,
    ):
        result = get_technical_indicators("300508")

    refresh.assert_called_once()
    assert result["source"] == "fresh-test-source"
    assert result["is_stale"] is False


def test_multi_stock_snapshot_keeps_verified_mapping_and_financial_period():
    resolved = [
        {"input": "维宏股份", "name": "维宏股份", "symbol": "300508"},
        {"input": "兆威机电", "name": "兆威机电", "symbol": "003021"},
    ]
    quotes = {
        "success": True,
        "partial": False,
        "items": [
            {"code": "300508", "name": "维宏股份", "price": 38.0},
            {"code": "003021", "name": "兆威机电", "price": 81.0},
        ],
        "data_time": "2026-07-17T14:00:00+08:00",
        "is_stale": False,
        "source": ["test"],
        "errors": [],
    }
    financials = {
        "300508": {"report_date": "2026-03-31", "net_profit": -1.0},
        "003021": {"report_date": "2026-03-31", "net_profit": 2.0},
    }

    def technical(code: str):
        return {
            "success": True,
            "data_time": "2026-06-25" if code == "300508" else "2026-07-17",
            "is_stale": code == "300508",
            "source": "stock_daily",
            "indicators": {"close": 10.0},
            "errors": [],
        }

    with (
        patch(
            "src.tools.get_multi_stock_snapshot.resolve_securities_csv",
            return_value=(resolved, []),
        ),
        patch(
            "src.tools.get_multi_stock_snapshot.get_realtime_quotes",
            return_value=quotes,
        ),
        patch(
            "src.tools.get_multi_stock_snapshot.get_technical_indicators",
            side_effect=technical,
        ),
        patch(
            "src.tools.get_multi_stock_snapshot._financial_snapshots",
            return_value=financials,
        ),
        patch(
            "src.tools.get_multi_stock_snapshot.is_trading_time",
            return_value=True,
        ),
    ):
        result = get_multi_stock_snapshot("维宏股份,兆威机电")

    assert [(item["name"], item["symbol"]) for item in result["items"]] == [
        ("维宏股份", "300508"),
        ("兆威机电", "003021"),
    ]
    assert result["items"][0]["financial"]["report_date"] == "2026-03-31"
    assert result["warnings"] == ["维宏股份(300508) 技术指标截至 2026-06-25，不是最新交易日"]
    assert result["quote_is_intraday"] is True
    assert "不是收盘价" in result["quote_basis"]
