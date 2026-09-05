from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pandas as pd

from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote
from src.tools._kline import _normalize_kline_df
from src.tools._trading_calendar import latest_completed_trade_day
from src.tools.kline_gateway import read_reliable_kline, read_reliable_kline_range
from src.tools.registry import ToolRegistry


def _kline_frame(rows: int) -> pd.DataFrame:
    dates = pd.date_range("2026-01-02", periods=rows, freq="B")
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": [100.0 + index for index in range(rows)],
            "收盘": [100.5 + index for index in range(rows)],
            "最高": [101.0 + index for index in range(rows)],
            "最低": [99.5 + index for index in range(rows)],
            "成交量": [10_000 + index for index in range(rows)],
            "成交额": [1_000_000 + index for index in range(rows)],
            "涨跌幅": [0.1] * rows,
            "换手率": [1.0] * rows,
        }
    )


def _completed_kline_frame(rows: int = 20) -> pd.DataFrame:
    dates = pd.date_range(end="2026-08-13", periods=rows, freq="B")
    frame = _kline_frame(rows)
    frame["日期"] = dates.strftime("%Y-%m-%d")
    return frame


def test_direct_quote_calls_only_the_selected_provider() -> None:
    calls: list[str] = []

    def selected_provider(symbol: str) -> UnifiedRealtimeQuote:
        calls.append(symbol)
        return UnifiedRealtimeQuote(
            code=symbol,
            name="贵州茅台",
            source=RealtimeSource.EASTMONEY_PUSH,
            trade_time="2026-08-08T14:30:00+08:00",
            price=1500.0,
        )

    with patch.dict(
        "src.tools.realtime_quote_source_tools._SOURCES",
        {"eastmoney_push": ("测试来源", "test_quote", selected_provider)},
    ):
        result = ToolRegistry().execute(
            "read_realtime_quote",
            {"source_id": "eastmoney_push", "symbol": "600519"},
        )

    assert calls == ["600519"]
    assert result["success"] is True
    assert result["fallback_used"] is False
    assert result["source_scope"] == "test_quote"


def test_auto_quote_uses_the_trading_aware_gateway() -> None:
    gateway_result = {
        "success": True,
        "partial": False,
        "items": [
            {
                "code": "600519",
                "price": 1500.0,
                "source": "eastmoney_push",
                "trade_time": "2026-08-14T15:00:00+08:00",
                "quote_mode": "latest_trading_day_snapshot",
            }
        ],
        "total": 1,
        "data_time": "2026-08-14T15:00:00+08:00",
        "data_time_provenance": "source",
        "data_time_note": None,
        "is_stale": False,
        "freshness_unknown": False,
        "quote_mode": "latest_trading_day_snapshot",
        "quote_mode_label": "非交易时段的最近交易日快照，不是当前时刻实时成交",
        "fallback_used": False,
        "errors": [],
        "warnings": [],
    }
    with patch("src.tools.get_realtime_quotes.get_realtime_quotes", return_value=gateway_result) as gateway:
        result = ToolRegistry().execute(
            "read_realtime_quote",
            {"symbol": "600519"},
        )

    gateway.assert_called_once_with(["600519"])
    assert result["success"] is True
    assert result["source_scope"] == "realtime_quote_auto"
    assert result["quote_mode"] == "latest_trading_day_snapshot"
    assert result["data_time"] == "2026-08-14T15:00:00+08:00"


def test_auto_quote_falls_back_to_the_latest_completed_close() -> None:
    with (
        patch(
            "src.tools.get_realtime_quotes.get_realtime_quotes",
            return_value={
                "success": False,
                "partial": False,
                "items": [],
                "total": 0,
                "data_time": None,
                "data_time_provenance": "unavailable",
                "data_time_note": "无有效报价",
                "is_stale": None,
                "freshness_unknown": True,
                "fallback_used": False,
                "errors": ["实时行情无数据: 600519"],
                "warnings": [],
            },
        ),
        patch(
            "src.tools.kline_gateway.read_reliable_kline",
            return_value={
                "success": True,
                "data": [
                    {"date": "2026-08-13", "close": 1490.0},
                    {"date": "2026-08-14", "close": 1500.0},
                ],
                "data_time": "2026-08-14",
                "data_time_provenance": "source",
                "is_stale": False,
                "source": "东方财富日线（AKShare）",
                "source_attempts": [],
                "warnings": [],
                "_cached": True,
                "_fetched_at": "2026-08-15T17:42:00+08:00",
            },
        ),
    ):
        result = ToolRegistry().execute(
            "read_realtime_quote",
            {"symbol": "600519"},
        )

    assert result["success"] is True
    assert result["fallback_used"] is True
    assert result["items"][0]["price"] == 1500.0
    assert result["quote_mode"] == "latest_completed_bar"
    assert result["data_time"] == "2026-08-14"
    assert result["errors"] == []
    assert any("收盘快照" in warning for warning in result["warnings"])


def test_auto_quote_replaces_a_successful_but_undated_quote() -> None:
    with (
        patch(
            "src.tools.get_realtime_quotes.get_realtime_quotes",
            return_value={
                "success": True,
                "partial": False,
                "items": [{"code": "600519", "price": 1500.0}],
                "total": 1,
                "data_time": None,
                "data_time_provenance": "unavailable",
                "data_time_note": "provider did not return trade time",
                "is_stale": None,
                "freshness_unknown": True,
                "fallback_used": False,
                "errors": [],
                "warnings": [],
            },
        ),
        patch(
            "src.tools.kline_gateway.read_reliable_kline",
            return_value={
                "success": True,
                "data": [{"date": "2026-08-14", "close": 1490.0}],
                "data_time": "2026-08-14",
                "data_time_provenance": "source",
                "is_stale": False,
                "source": "东方财富日线（AKShare）",
                "source_attempts": [],
                "warnings": [],
                "_cached": True,
                "_fetched_at": "2026-08-15T17:42:00+08:00",
            },
        ),
    ):
        result = ToolRegistry().execute(
            "read_realtime_quote",
            {"symbol": "600519"},
        )

    assert result["success"] is True
    assert result["fallback_used"] is True
    assert result["quote_mode"] == "latest_completed_bar"
    assert result["items"][0]["price"] == 1490.0
    assert result["data_time"] == "2026-08-14"


def test_strict_kline_mode_does_not_read_cache_or_switch_source() -> None:
    calls: list[tuple[str, str, str]] = []

    def selected_provider(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        calls.append((symbol, start_date, end_date))
        return _kline_frame(60)

    with (
        patch.dict(
            "src.tools.kline_source_tools._SOURCES",
            {"eastmoney": ("测试来源", selected_provider)},
        ),
        patch("src.tools.kline_source_tools._kline_is_stale", return_value=False),
    ):
        result = ToolRegistry().execute(
            "read_recent_kline",
            {
                "source_id": "eastmoney",
                "symbol": "600519",
                "count": 60,
                "allow_fallback": False,
            },
        )

    assert len(calls) == 1
    assert calls[0][0] == "600519"
    assert result["success"] is True
    assert result["fallback_used"] is False
    assert result["_cached"] is False
    assert result["source_scope"] == "eastmoney_daily_qfq_kline"


def test_direct_indicator_calculation_is_one_source_one_indicator() -> None:
    calls: list[tuple[str, str, str]] = []

    def selected_provider(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        calls.append((symbol, start_date, end_date))
        return _kline_frame(150)

    with (
        patch.dict(
            "src.tools.technical_indicator_source_tools._SOURCES",
            {"eastmoney": ("测试来源", selected_provider)},
        ),
        patch("src.tools.technical_indicator_source_tools._kline_is_stale", return_value=False),
    ):
        result = ToolRegistry().execute(
            "calculate_technical_indicator",
            {
                "source_id": "eastmoney",
                "indicator": "moving_average",
                "symbol": "600519",
                "count": 120,
                "window": 20,
                "allow_fallback": False,
            },
        )

    assert len(calls) == 1
    assert result["success"] is True
    assert result["fallback_used"] is False
    assert result["indicator"] == "moving_average"
    assert result["calculation"]["window"] == 20
    assert result["calculation"]["value"] is not None
    assert result["source_scope"] == "eastmoney_daily_qfq_kline_plus_moving_average"


def test_reliable_kline_falls_back_and_records_provider_attempts() -> None:
    calls: list[str] = []

    def eastmoney(_symbol: str, _start: str, _end: str) -> pd.DataFrame:
        calls.append("eastmoney")
        raise ConnectionError("RemoteDisconnected")

    def tencent(_symbol: str, _start: str, _end: str) -> pd.DataFrame:
        calls.append("tencent")
        return _completed_kline_frame()

    with (
        patch("src.tools.kline_gateway.latest_completed_trade_day", return_value=date(2026, 8, 13)),
        patch("src.tools.kline_gateway._get_kline_from_stock_daily", return_value=None),
        patch("src.tools.kline_gateway._get_kline_from_cache", return_value=None),
        patch("src.tools.kline_gateway._save_kline_to_cache"),
        patch("src.tools.kline_gateway._save_to_stock_daily"),
    ):
        result = read_reliable_kline(
            "600519",
            preferred_source="eastmoney",
            count=20,
            sources={
                "eastmoney": ("东方财富", eastmoney),
                "tencent": ("腾讯财经", tencent),
            },
        )

    assert calls == ["eastmoney", "tencent"]
    assert result["success"] is True
    assert result["source_key"] == "tencent"
    assert result["fallback_used"] is True
    assert result["source_attempts"][0]["error_type"] == "ConnectionError"
    assert result["source_attempts"][-1]["status"] == "success"


def test_reliable_kline_range_falls_back_after_provider_failure() -> None:
    calls: list[str] = []

    def eastmoney(_symbol: str, _start: str, _end: str) -> pd.DataFrame:
        calls.append("eastmoney")
        raise ConnectionError("RemoteDisconnected")

    def tencent(_symbol: str, _start: str, _end: str) -> pd.DataFrame:
        calls.append("tencent")
        return _completed_kline_frame()

    with (
        patch("src.tools.kline_gateway.latest_completed_trade_day", return_value=date(2026, 8, 13)),
        patch("src.tools.kline_gateway._get_kline_range_from_stock_daily", return_value=None),
        patch("src.tools.kline_gateway._get_kline_from_cache", return_value=None),
        patch("src.tools.kline_gateway._save_kline_to_cache"),
        patch("src.tools.kline_gateway._save_to_stock_daily"),
    ):
        result = read_reliable_kline_range(
            "600519",
            preferred_source="eastmoney",
            start_date="20260810",
            end_date="20260813",
            sources={
                "eastmoney": ("东方财富", eastmoney),
                "tencent": ("腾讯财经", tencent),
            },
        )

    assert calls == ["eastmoney", "tencent"]
    assert result["success"] is True
    assert result["source_key"] == "tencent"
    assert result["fallback_used"] is True
    assert result["requested_start_date"] == "20260810"
    assert result["requested_end_date"] == "20260813"
    assert result["source_attempts"][0]["error_type"] == "ConnectionError"
    assert result["source_attempts"][-1]["status"] == "success"


def test_reliable_kline_range_skips_a_stale_provider_response() -> None:
    calls: list[str] = []
    stale_frame = _completed_kline_frame()
    stale_frame["日期"] = pd.date_range(end="2026-08-10", periods=len(stale_frame), freq="B").strftime("%Y-%m-%d")

    def eastmoney(_symbol: str, _start: str, _end: str) -> pd.DataFrame:
        calls.append("eastmoney")
        return stale_frame

    def tencent(_symbol: str, _start: str, _end: str) -> pd.DataFrame:
        calls.append("tencent")
        return _completed_kline_frame()

    with (
        patch("src.tools.kline_gateway.latest_completed_trade_day", return_value=date(2026, 8, 13)),
        patch("src.tools.kline_gateway._get_kline_range_from_stock_daily", return_value=None),
        patch("src.tools.kline_gateway._get_kline_from_cache", return_value=None),
        patch("src.tools.kline_gateway._save_kline_to_cache"),
        patch("src.tools.kline_gateway._save_to_stock_daily"),
    ):
        result = read_reliable_kline_range(
            "600519",
            preferred_source="eastmoney",
            start_date="20260810",
            end_date="20260813",
            sources={
                "eastmoney": ("东方财富", eastmoney),
                "tencent": ("腾讯财经", tencent),
            },
        )

    assert calls == ["eastmoney", "tencent"]
    assert result["success"] is True
    assert result["source_key"] == "tencent"
    assert result["source_attempts"][0]["status"] == "stale"
    assert result["source_attempts"][-1]["status"] == "success"


def test_fresh_stock_daily_cache_is_not_marked_as_provider_fallback() -> None:
    local_records = _normalize_kline_df(_completed_kline_frame(), "600519", "eastmoney")
    with (
        patch("src.tools.kline_gateway.latest_completed_trade_day", return_value=date(2026, 8, 13)),
        patch("src.tools.kline_gateway._get_kline_from_stock_daily", return_value=local_records),
        patch("src.tools.kline_gateway._get_kline_from_cache") as get_cache,
        patch("src.tools.kline_gateway._fetch_kline_em") as eastmoney,
    ):
        result = read_reliable_kline(
            "600519",
            preferred_source="eastmoney",
            count=20,
            sources={"eastmoney": ("东方财富", eastmoney)},
        )

    assert result["success"] is True
    assert result["partial"] is False
    assert result["is_stale"] is False
    assert result["fallback_used"] is False
    assert result["source_key"] == "stock_daily"
    get_cache.assert_not_called()
    eastmoney.assert_not_called()


def test_completed_trade_day_excludes_intraday_bar() -> None:
    calendar = [date(2026, 8, 13), date(2026, 8, 14)]

    assert latest_completed_trade_day(
        datetime(2026, 8, 14, 11, 0), calendar
    ) == date(2026, 8, 13)
    assert latest_completed_trade_day(
        datetime(2026, 8, 14, 15, 0), calendar
    ) == date(2026, 8, 14)
