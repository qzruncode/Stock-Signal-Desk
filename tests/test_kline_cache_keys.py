# -*- coding: utf-8 -*-
"""K-line endpoint cache-key behavior tests."""

import unittest
from datetime import datetime
from unittest.mock import patch

from src.tools import _kline as kline


class KlineCacheKeyTestCase(unittest.TestCase):
    def test_latest_kline_cache_key_includes_count(self) -> None:
        cached = {
            "symbol": "600519",
            "data": [{"date": datetime.now().strftime("%Y-%m-%d")}],
            "count": 1,
        }

        with (
            patch.object(kline, "_is_trading_hours", return_value=False),
            patch.object(kline, "_get_kline_from_stock_daily", return_value=None),
            patch.object(kline, "_get_kline_from_cache", return_value=cached) as get_cache,
        ):
            result = kline.get_kline(symbol="600519", count=30, use_cache=True)

        self.assertTrue(result["_cached"])
        get_cache.assert_called_once_with("kline:latest:600519:30")

    def test_latest_kline_rejects_stale_local_and_snapshot_cache(self) -> None:
        stale = [{"date": "2020-01-01", "close": 100.0}]
        fresh = [{"date": datetime.now().strftime("%Y-%m-%d"), "close": 101.0}]
        with (
            patch.object(kline, "_is_trading_hours", return_value=False),
            patch.object(kline, "_get_kline_from_stock_daily", return_value=stale),
            patch.object(kline, "_get_kline_from_cache", return_value={"data": stale, "source": "cache"}),
            patch.object(kline, "_fetch_kline_with_fallback", return_value=(fresh, "eastmoney")) as fetch,
            patch.object(kline, "_save_kline_to_cache"),
            patch.object(kline, "_save_to_stock_daily"),
        ):
            result = kline.get_kline(symbol="600519", count=1, use_cache=True)

        self.assertEqual(result["source"], "eastmoney")
        self.assertEqual(result["data"], fresh)
        fetch.assert_called_once()

    def test_history_kline_cache_key_includes_date_range(self) -> None:
        cached = {"symbol": "600519", "data": [{"date": "20260102"}], "count": 1}

        with (
            patch.object(kline, "_is_trading_hours", return_value=False),
            patch.object(kline, "_get_kline_range_from_stock_daily", return_value=None),
            patch.object(kline, "_get_kline_from_cache", return_value=cached) as get_cache,
        ):
            result = kline.get_history_data(
                symbol="600519",
                start_date="20260101",
                end_date="20260131",
                use_cache=True,
            )

        self.assertTrue(result["_cached"])
        get_cache.assert_called_once_with("kline:history:600519:20260101:20260131")

    def test_history_kline_saves_under_date_range_key(self) -> None:
        records = [{"date": "20260102", "close": 100.0}]

        with (
            patch.object(kline, "_is_trading_hours", return_value=False),
            patch.object(kline, "_get_kline_range_from_stock_daily", return_value=None),
            patch.object(kline, "_get_kline_from_cache", return_value=None),
            patch.object(kline, "_fetch_kline_with_fallback", return_value=(records, "eastmoney")),
            patch.object(kline, "_save_kline_to_cache") as save_cache,
        ):
            result = kline.get_history_data(
                symbol="600519",
                start_date="20260101",
                end_date="20260131",
                use_cache=True,
            )

        self.assertFalse(result["_cached"])
        save_cache.assert_called_once_with(
            "kline:history:600519:20260101:20260131",
            "600519",
            records,
            "eastmoney",
        )

    def test_history_range_local_complete_does_not_refetch(self) -> None:
        """本地数据首尾日期对齐区间 → 直接返回 stock_daily，不回源外部 API。"""
        records = [
            {"date": "20260101", "close": 100.0, "_source": "stock_daily"},
            {"date": "20260131", "close": 110.0, "_source": "stock_daily"},
        ]
        with (
            patch.object(kline, "_get_kline_range_from_stock_daily", return_value=(records, True)) as get_local,
            patch.object(kline, "_get_kline_from_cache") as get_cache,
            patch.object(kline, "_fetch_kline_with_fallback") as fetch,
        ):
            result = kline.get_history_data(
                symbol="600519",
                start_date="20260101",
                end_date="20260131",
                use_cache=True,
            )
        self.assertEqual(result["source"], "stock_daily")
        self.assertTrue(result["_cached"])
        get_local.assert_called_once()
        get_cache.assert_not_called()
        fetch.assert_not_called()

    def test_history_range_local_partial_coverage_falls_back(self) -> None:
        """本地仅区间内部分日期（首尾未对齐）→ 放弃局部数据，回源取整段。"""
        partial = [{"date": "20260101", "close": 100.0}]  # 缺尾 20260131
        fetched = [{"date": "20260101", "close": 100.0}, {"date": "20260131", "close": 110.0}]
        with (
            patch.object(kline, "_is_trading_hours", return_value=False),
            patch.object(kline, "_get_kline_range_from_stock_daily", return_value=(partial, False)),
            patch.object(kline, "_get_kline_from_cache", return_value=None),
            patch.object(kline, "_fetch_kline_with_fallback", return_value=(fetched, "eastmoney")) as fetch,
            patch.object(kline, "_save_kline_to_cache"),
        ):
            result = kline.get_history_data(
                symbol="600519",
                start_date="20260101",
                end_date="20260131",
                use_cache=True,
            )
        self.assertEqual(result["source"], "eastmoney")
        self.assertFalse(result["_cached"])
        fetch.assert_called_once()

    def test_history_range_is_stale_is_none(self) -> None:
        """range 模式 freshness 不适用，is_stale 恒为 None。"""
        records = [
            {"date": "20260101", "close": 100.0, "_source": "stock_daily"},
            {"date": "20260131", "close": 110.0, "_source": "stock_daily"},
        ]
        with (
            patch.object(kline, "_get_kline_range_from_stock_daily", return_value=(records, True)),
            patch.object(kline, "_fetch_kline_with_fallback"),
        ):
            result = kline.get_history_data(
                symbol="600519",
                start_date="20260101",
                end_date="20260131",
                use_cache=True,
            )
        self.assertIsNone(result["is_stale"])


if __name__ == "__main__":
    unittest.main()
