from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote
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


def test_direct_kline_does_not_read_cache_or_switch_source() -> None:
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
            {"source_id": "eastmoney", "symbol": "600519", "count": 60},
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
            },
        )

    assert len(calls) == 1
    assert result["success"] is True
    assert result["fallback_used"] is False
    assert result["indicator"] == "moving_average"
    assert result["calculation"]["window"] == 20
    assert result["calculation"]["value"] is not None
    assert result["source_scope"] == "eastmoney_daily_qfq_kline_plus_moving_average"
