# -*- coding: utf-8 -*-
"""K-line endpoint cache-key behavior tests."""

import unittest
from unittest.mock import patch

from src.tools import _kline as kline


class KlineCacheKeyTestCase(unittest.TestCase):
    def test_latest_kline_cache_key_includes_count(self) -> None:
        cached = {"symbol": "600519", "data": [{"date": "20260101"}], "count": 1}

        with patch.object(kline, "_is_trading_hours", return_value=False), \
             patch.object(kline, "_get_kline_from_stock_daily", return_value=None), \
             patch.object(kline, "_get_kline_from_cache", return_value=cached) as get_cache:
            result = kline.get_kline(symbol="600519", count=30, use_cache=True)

        self.assertTrue(result["_cached"])
        get_cache.assert_called_once_with("kline:latest:600519:30")

    def test_history_kline_cache_key_includes_date_range(self) -> None:
        cached = {"symbol": "600519", "data": [{"date": "20260102"}], "count": 1}

        with patch.object(kline, "_is_trading_hours", return_value=False), \
             patch.object(kline, "_get_kline_range_from_stock_daily", return_value=None), \
             patch.object(kline, "_get_kline_from_cache", return_value=cached) as get_cache:
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

        with patch.object(kline, "_is_trading_hours", return_value=False), \
             patch.object(kline, "_get_kline_range_from_stock_daily", return_value=None), \
             patch.object(kline, "_get_kline_from_cache", return_value=None), \
             patch.object(kline, "_fetch_kline_with_fallback", return_value=(records, "eastmoney")), \
             patch.object(kline, "_save_kline_to_cache") as save_cache:
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


if __name__ == "__main__":
    unittest.main()
