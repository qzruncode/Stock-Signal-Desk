"""Public tool contracts use HTTP source selection; calculations remain local and deterministic."""

from datetime import date, datetime
from unittest.mock import patch
import pytest
from src.tools.registry import ToolRegistry
from src.tools._trading_calendar import latest_completed_trade_day
from src.services.market_data_client import DataNotReady
from src.tools.kline_gateway import read_reliable_kline, read_reliable_kline_range


@pytest.mark.parametrize(
    "source", ["auto", "sina", "tencent", "eastmoney_push", "xueqiu"]
)
def test_direct_quote_preserves_explicit_service_source(source):
    with (
        patch(
            "src.tools.realtime_quote_source_tools.resolve_local_symbol",
            return_value="600519",
        ),
        patch(
            "src.tools.realtime_quote_source_tools.read_source",
            return_value={"success": True, "data_service": {"version": "v"}},
        ) as read,
    ):
        result = ToolRegistry().execute(
            "read_realtime_quote", {"symbol": "600519", "source_id": source}
        )
    read.assert_called_once_with("quotes", {"symbol": "600519", "source_id": source})
    assert result["success"] and result["data_service"]["version"] == "v"


def test_quote_unavailable_does_not_turn_daily_close_into_realtime_quote():
    from src.tools.source_operations import read_realtime_quote

    with (
        patch(
            "src.tools.realtime_quote_source_tools.resolve_local_symbol",
            return_value="600519",
        ),
        patch(
            "src.tools.realtime_quote_source_tools.read_source",
            side_effect=DataNotReady({"status": "unknown"}),
        ),
        patch(
            "src.tools.kline_gateway.read_reliable_kline",
            side_effect=AssertionError("false quote fallback"),
        ),
        pytest.raises(DataNotReady),
    ):
        read_realtime_quote("600519")


@pytest.mark.parametrize("fallback", [True, False])
def test_kline_gateway_preserves_fallback_choice(fallback):
    with patch(
        "src.tools.kline_gateway.read_source", return_value={"success": True}
    ) as read:
        read_reliable_kline(
            "600519", preferred_source="sina", count=120, allow_fallback=fallback
        )
    read.assert_called_once_with(
        "kline",
        {
            "symbol": "600519",
            "source_id": "sina",
            "count": 120,
            "allow_fallback": fallback,
        },
    )


def test_kline_gateway_preserves_historical_window():
    with patch("src.tools.kline_gateway.read_source") as read:
        read_reliable_kline_range(
            "600519",
            preferred_source="tencent",
            start_date="20260101",
            end_date="20260201",
            allow_fallback=False,
        )
    read.assert_called_once_with(
        "kline",
        {
            "symbol": "600519",
            "source_id": "tencent",
            "start_date": "20260101",
            "end_date": "20260201",
            "allow_fallback": False,
        },
    )


def test_one_indicator_uses_service_bars_and_retains_selected_source():
    rows = [
        {
            "date": f"2026-01-{i % 28 + 1:02d}",
            "open": 100 + i,
            "close": 100 + i,
            "high": 101 + i,
            "low": 99 + i,
        }
        for i in range(120)
    ]
    # Distinct dates are required by the calculation's documented normalization.
    from datetime import timedelta

    for i, row in enumerate(rows):
        row["date"] = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
    raw = {
        "success": True,
        "data": rows,
        "source": "sina",
        "source_key": "sina",
        "data_time": rows[-1]["date"],
        "data_time_provenance": "source",
        "is_stale": False,
        "freshness_unknown": False,
        "fallback_used": False,
    }
    with (
        patch(
            "src.tools.technical_indicator_source_tools.resolve_local_symbol",
            return_value="600519",
        ),
        patch(
            "src.tools.technical_indicator_source_tools._read_source_rows",
            return_value=raw,
        ) as read,
    ):
        result = ToolRegistry().execute(
            "calculate_technical_indicator",
            {
                "source_id": "sina",
                "indicator": "moving_average",
                "symbol": "600519",
                "count": 120,
                "window": 20,
                "allow_fallback": False,
            },
        )
    assert read.call_count == 1 and result["success"]
    assert result["calculation"]["window"] == 20 and result["calculation"][
        "value"
    ] == pytest.approx(209.5)
    assert result["source_scope"] == "sina_daily_qfq_kline_plus_moving_average"


def test_completed_trade_day_excludes_intraday_bar() -> None:
    calendar = [date(2026, 8, 13), date(2026, 8, 14)]

    assert latest_completed_trade_day(datetime(2026, 8, 14, 11, 0), calendar) == date(
        2026, 8, 13
    )
    assert latest_completed_trade_day(datetime(2026, 8, 14, 15, 15), calendar) == date(
        2026, 8, 14
    )
