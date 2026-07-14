# -*- coding: utf-8 -*-
"""``_get_kline_range_from_stock_daily`` 完整性校验测试。

校验逻辑：本地数据是否完整覆盖请求区间。
- 优先用交易日历（上证指数 000001 日线 date 集合）做精确判断：
  本地 date 集合 ⊇ 区间内全部交易日 → complete=True
- 交易日历不可用（区间内上证指数无数据）→ 回退首尾对齐。
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.storage import DatabaseManager
from src.tools import _kline as kline


def _seed_index_days(db, days: list[date]) -> None:
    """灌入上证指数日线（仅 date 有意义，作为交易日历来源）。

    save_macro_index_daily 的 _normalize_daily_date 只接受 date/datetime 对象
    （字符串原样透传会被 SQLite Date 列拒绝），故这里传 date 对象。
    """
    records = [{"date": d, "close": 3000.0} for d in days]
    db.save_macro_index_daily("000001", records, data_source="test")


def _seed_stock_daily(db, symbol: str, days: list[date], closes: list[float]) -> None:
    """灌入个股 StockDaily（date + close）。"""
    rows = [
        {"date": d, "open": c, "close": c, "high": c, "low": c,
         "volume": 1000.0, "amount": 100000.0, "pct_chg": 1.0}
        for d, c in zip(days, closes)
    ]
    import pandas as pd
    db.save_daily_data(pd.DataFrame(rows), symbol, data_source="test")


class KlineRangeCompletenessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_url="sqlite:///:memory:")

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()

    def test_complete_when_all_trading_days_present(self) -> None:
        """本地覆盖区间内全部交易日 → complete=True，可直接命中。"""
        trading_days = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
        _seed_index_days(self.db, trading_days)
        _seed_stock_daily(self.db, "600519", trading_days, [100.0, 101.0, 102.0])

        result = kline._get_kline_range_from_stock_daily(
            "600519", "20260105", "20260107"
        )
        self.assertIsNotNone(result)
        records, complete = result  # type: ignore[misc]
        self.assertTrue(complete)
        self.assertEqual(len(records), 3)

    def test_incomplete_when_missing_a_trading_day(self) -> None:
        """区间内缺一个交易日（停牌/缺数据）→ complete=False，应回源。"""
        trading_days = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
        _seed_index_days(self.db, trading_days)
        # 个股缺 1-6 那天
        _seed_stock_daily(
            self.db, "600519",
            [date(2026, 1, 5), date(2026, 1, 7)],
            [100.0, 102.0],
        )

        result = kline._get_kline_range_from_stock_daily(
            "600519", "20260105", "20260107"
        )
        self.assertIsNotNone(result)
        records, complete = result  # type: ignore[misc]
        self.assertFalse(complete)  # 缺交易日 → 不完整 → 回源
        self.assertEqual(len(records), 2)  # 但局部数据仍返回供日志/调试

    def test_falls_back_to_head_tail_when_calendar_unavailable(self) -> None:
        """交易日历不可用（区间内上证指数无数据）→ 回退首尾对齐校验。"""
        # 不灌上证指数 → get_trading_days 返回 None
        trading_days = [date(2026, 1, 5), date(2026, 1, 7)]  # 注意首尾恰好对齐区间
        _seed_stock_daily(self.db, "600519", trading_days, [100.0, 102.0])

        result = kline._get_kline_range_from_stock_daily(
            "600519", "20260105", "20260107"
        )
        self.assertIsNotNone(result)
        records, complete = result  # type: ignore[misc]
        # 回退首尾对齐：首=20260105 尾=20260107，恰好对齐 → complete=True
        self.assertTrue(complete)

    def test_returns_none_when_no_local_data(self) -> None:
        """本地完全无该股数据 → None。"""
        result = kline._get_kline_range_from_stock_daily(
            "999999", "20260105", "20260107"
        )
        self.assertIsNone(result)

    def test_get_trading_days_returns_set_or_none(self) -> None:
        """get_trading_days：有数据返回交易日集合，无数据返回 None。"""
        _seed_index_days(self.db, [date(2026, 1, 5), date(2026, 1, 6)])
        days = self.db.get_trading_days(date(2026, 1, 5), date(2026, 1, 7))
        self.assertEqual(days, {date(2026, 1, 5), date(2026, 1, 6)})

        DatabaseManager.reset_instance()
        empty_db = DatabaseManager(db_url="sqlite:///:memory:")
        self.assertIsNone(empty_db.get_trading_days(date(2026, 1, 5), date(2026, 1, 7)))


if __name__ == "__main__":
    unittest.main()
