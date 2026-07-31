# -*- coding: utf-8 -*-
"""K线数据源适配测试：对照 akshare 1.18.55 源码核实各源列结构与 fallback 行为。

覆盖：
- 北交所跳过东财源（东财 market_code 只判“6”，北交所恒返回空）
- fallback_used 语义：北交所走新浪不算异常回退
- 新浪源 turnover→换手率 映射 + 首行涨跌幅 None（不误填 0）
- 腾讯源 amount（手）映射到成交量、不冒充成交额
- _save_to_stock_daily 回写链路：完整源正常落库、缺成交额源不污染、字符串日期可写
"""

import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.storage import DatabaseManager, StockDaily
from src.tools import _kline as kline


class BeijingExchangeTestCase(unittest.TestCase):
    def test_bare_code_strips_prefix(self) -> None:
        self.assertEqual(kline._bare_stock_code("sh600519"), "600519")
        self.assertEqual(kline._bare_stock_code("sz000001"), "000001")
        self.assertEqual(kline._bare_stock_code("bj830799"), "830799")
        self.assertEqual(kline._bare_stock_code("600519"), "600519")

    def test_is_beijing_exchange(self) -> None:
        self.assertTrue(kline._is_beijing_exchange("830799"))
        self.assertTrue(kline._is_beijing_exchange("bj430047"))
        self.assertTrue(kline._is_beijing_exchange("920099"))
        self.assertFalse(kline._is_beijing_exchange("600519"))
        self.assertFalse(kline._is_beijing_exchange("sz000001"))
        self.assertFalse(kline._is_beijing_exchange("300750"))


class FallbackChainTestCase(unittest.TestCase):
    def test_beijing_skips_eastmoney(self) -> None:
        """北交所股票应跳过东财源，直接从新浪起取。"""
        called_sources: list[str] = []

        def fake_retry(label, call):
            called_sources.append(label)
            if label == "新浪":
                return pd.DataFrame(
                    {
                        "date": ["20260105"],
                        "open": [10.0],
                        "close": [10.5],
                        "high": [10.6],
                        "low": [9.9],
                        "volume": [1000],
                        "amount": [10000.0],
                        "turnover": [0.01],
                    }
                )
            return None

        with (
            patch.object(kline, "akshare_rate_limiter"),
            patch.object(kline, "_fetch_with_single_source_retry", side_effect=fake_retry),
            patch.object(kline, "_kline_source_circuit_breaker") as cb,
        ):
            cb.is_available.return_value = True
            cb.record_success = lambda *a, **k: None
            cb.record_failure = lambda *a, **k: None
            records, source = kline._fetch_kline_with_fallback("830799", "20260105", "20260105")
        self.assertEqual(source, kline.KLINE_SOURCE_SINA)
        self.assertNotIn("东财", called_sources)  # 北交所不碰东财
        self.assertIn("新浪", called_sources)

    def test_non_beijing_tries_eastmoney_first(self) -> None:
        """沪深股票仍首选东财。"""
        called_sources: list[str] = []

        def fake_retry(label, call):
            called_sources.append(label)
            if label == "东财":
                return pd.DataFrame(
                    {
                        "日期": ["20260105"],
                        "开盘": [10.0],
                        "收盘": [10.5],
                        "最高": [10.6],
                        "最低": [9.9],
                        "成交量": [100],
                        "成交额": [1000.0],
                        "涨跌幅": [5.0],
                        "换手率": [0.5],
                    }
                )
            return None

        with (
            patch.object(kline, "akshare_rate_limiter"),
            patch.object(kline, "_fetch_with_single_source_retry", side_effect=fake_retry),
            patch.object(kline, "_kline_source_circuit_breaker") as cb,
        ):
            cb.is_available.return_value = True
            cb.record_success = lambda *a, **k: None
            cb.record_failure = lambda *a, **k: None
            records, source = kline._fetch_kline_with_fallback("600519", "20260105", "20260105")
        self.assertEqual(source, kline.KLINE_SOURCE_EM)
        self.assertEqual(called_sources, ["东财"])  # 东财命中即止，不试新浪


class FallbackUsedSemanticsTestCase(unittest.TestCase):
    def test_beijing_sina_is_not_fallback(self) -> None:
        """北交所走新浪（其首选源）不算 fallback。"""
        self.assertFalse(kline._fallback_used("830799", kline.KLINE_SOURCE_SINA))

    def test_beijing_tencent_is_fallback(self) -> None:
        """北交所落到腾讯（非首选）算 fallback。"""
        self.assertTrue(kline._fallback_used("830799", kline.KLINE_SOURCE_TENCENT))

    def test_sh_em_is_not_fallback(self) -> None:
        self.assertFalse(kline._fallback_used("600519", kline.KLINE_SOURCE_EM))

    def test_sh_sina_is_fallback(self) -> None:
        self.assertTrue(kline._fallback_used("600519", kline.KLINE_SOURCE_SINA))

    def test_local_is_never_fallback(self) -> None:
        self.assertFalse(kline._fallback_used("600519", "stock_daily"))
        self.assertFalse(kline._fallback_used("830799", "cache"))


class SinaTurnoverMappingTestCase(unittest.TestCase):
    def test_sina_maps_turnover_and_pct_chg_first_row_none(self) -> None:
        """新浪 turnover 应映射到换手率；首行涨跌幅为 None 而非 0。"""
        sina_df = pd.DataFrame(
            {
                "date": ["20260105", "20260106"],
                "open": [10.0, 10.5],
                "high": [10.6, 10.7],
                "low": [9.9, 10.4],
                "close": [10.5, 10.4],
                "volume": [1000, 1200],
                "amount": [10000.0, 12000.0],
                "turnover": [0.01, 0.012],
            }
        )
        with (
            patch.object(kline, "akshare_rate_limiter"),
            patch.object(kline, "_fetch_with_single_source_retry", return_value=sina_df),
        ):
            df = kline._fetch_kline_sina("sz000001", "20260105", "20260106")
        self.assertIn("换手率", df.columns)  # turnover 已映射
        self.assertEqual(df.iloc[0]["换手率"], 0.01)
        # 首行涨跌幅无前一日基准 → None（NaN），不再误填 0
        self.assertTrue(pd.isna(df.iloc[0]["涨跌幅"]))
        # 第二行有正常涨跌幅
        self.assertAlmostEqual(df.iloc[1]["涨跌幅"], (10.4 / 10.5 - 1) * 100, places=4)


class TencentAmountIsVolumeTestCase(unittest.TestCase):
    def test_tencent_amount_maps_to_volume_not_amount(self) -> None:
        """腾讯源 amount（单位：手）应映射到成交量，且不产生假的成交额列。

        对照 akshare 源码 stock_zh_a_hist_tx：返回仅 date/open/close/high/low/amount
        六列，末列 amount 单位是“手”（成交量），不是“元”（成交额）。若误当成成交额，
        会以百万级手数冒充亿元级成交额回写 StockDaily，污染下游。故映射到成交量，
        成交额列缺失（留 None）。
        """
        tx_df = pd.DataFrame(
            {
                "date": ["20260105", "20260106"],
                "open": [10.0, 10.5],
                "close": [10.5, 10.4],
                "high": [10.6, 10.7],
                "low": [9.9, 10.4],
                "amount": [1000.0, 1200.0],
            }
        )
        with (
            patch.object(kline, "akshare_rate_limiter"),
            patch.object(kline, "_fetch_with_single_source_retry", return_value=tx_df),
        ):
            df = kline._fetch_kline_tencent("sz000001", "20260105", "20260106")
        # amount（手）映射到成交量
        self.assertIn("成交量", df.columns)
        self.assertEqual(df.iloc[0]["成交量"], 1000.0)
        # 不产生假的成交额列
        self.assertNotIn("成交额", df.columns)
        # 首行涨跌幅 None
        self.assertTrue(pd.isna(df.iloc[0]["涨跌幅"]))

    def test_tencent_normalize_drops_amount_field(self) -> None:
        """腾讯源经 normalize 后统一为股，无虚假的成交额。"""
        # 用 date 对象模拟 akshare 真实输出（akshare 内部 .dt.date）
        tx_df = pd.DataFrame(
            {
                "date": [date(2026, 1, 5)],
                "open": [10.0],
                "close": [10.5],
                "high": [10.6],
                "low": [9.9],
                "amount": [1000.0],
            }
        )
        with (
            patch.object(kline, "akshare_rate_limiter"),
            patch.object(kline, "_fetch_with_single_source_retry", return_value=tx_df),
        ):
            df = kline._fetch_kline_tencent("sz000001", "20260105", "20260105")
        records = kline._normalize_kline_df(df, "000001", kline.KLINE_SOURCE_TENCENT)
        self.assertEqual(len(records), 1)
        rec = records[0]
        # 腾讯 amount=1000 手，统一输出为 100000 股
        self.assertEqual(rec["volume"], 100000.0)
        self.assertEqual(rec["volume_unit"], "股")
        # 成交额缺失：不应出现 amount 键，防止回写错值
        self.assertNotIn("amount", rec)
        self.assertEqual(rec["_source"], kline.KLINE_SOURCE_TENCENT)

    def test_tencent_persist_does_not_pollute_amount(self) -> None:
        """腾讯源回写 StockDaily：不覆盖已有真实成交额，volume 用腾讯手数更新。

        复现污染场景：先用东财口径回写 amount=1e8（亿元级真实成交额），再让腾讯源
        兜底回写同 (code,date)。修复前回写因参数名错（source≠data_source）+ 字符串
        日期而静默失败；即便写成功，amount=None 也会经 UPSERT 覆盖真实值。修复后：
        amount 缺失行用库中已有值回填，volume 正常更新。
        """
        DatabaseManager.reset_instance()
        try:
            db = DatabaseManager(db_url="sqlite:///:memory:")
            # 先写入东财口径的真实成交额
            db.save_daily_data(
                pd.DataFrame(
                    [
                        {
                            "date": date(2026, 1, 5),
                            "open": 10.0,
                            "close": 10.5,
                            "high": 10.6,
                            "low": 9.9,
                            "volume": 1e6,
                            "amount": 1e8,
                            "pct_chg": 5.0,
                        }
                    ]
                ),
                "000001",
                data_source="eastmoney",
            )
            # 腾讯统一层已经把 1000 手换算为 100000 股；成交额仍缺失。
            kline._save_to_stock_daily(
                "000001",
                [
                    {
                        "date": "2026-01-05",
                        "open": 10.0,
                        "close": 10.5,
                        "high": 10.6,
                        "low": 9.9,
                        "volume": 100000.0,
                        "volume_unit": "股",
                        "pct_chg": None,
                        "_source": kline.KLINE_SOURCE_TENCENT,
                    }
                ],
                source=kline.KLINE_SOURCE_TENCENT,
            )
            from sqlalchemy import select

            with db.get_session() as session:
                row = session.execute(select(StockDaily).where(StockDaily.code == "000001")).scalars().one()
            # amount 不被 None 覆盖，保持东财真实值
            self.assertEqual(row.amount, 1e8)
            # volume 被腾讯兜底更新，数据库统一保存为股。
            self.assertEqual(row.volume, 100000.0)
            self.assertEqual(row.data_source, f"{kline.KLINE_SOURCE_TENCENT}_shares")
        finally:
            DatabaseManager.reset_instance()


class PersistStockDailyTestCase(unittest.TestCase):
    """_save_to_stock_daily 回写链路：修好后完整源应真正写入 StockDaily。

    历史坑：_save_to_stock_daily 曾用 source=（参数名错，应为 data_source=）+
    字符串日期（SQLite Date 列拒绝），导致回写长期静默失败。这里覆盖完整源
    （东财：有成交额）的正常回写，确保死代码已复活。
    """

    def setUp(self) -> None:
        DatabaseManager.reset_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()

    def test_em_source_persists_amount_and_volume(self) -> None:
        """东财源 records（含 amount）回写：amount/volume 正确落库。"""
        db = DatabaseManager(db_url="sqlite:///:memory:")
        records = [
            {
                "date": "2026-01-05",
                "open": 10.0,
                "close": 10.5,
                "high": 10.6,
                "low": 9.9,
                "volume": 1e6,
                "amount": 1e8,
                "pct_chg": 5.0,
                "_source": kline.KLINE_SOURCE_EM,
            }
        ]
        kline._save_to_stock_daily("000001", records, source=kline.KLINE_SOURCE_EM)
        from sqlalchemy import select

        with db.get_session() as session:
            row = session.execute(select(StockDaily).where(StockDaily.code == "000001")).scalars().one()
        self.assertEqual(row.amount, 1e8)
        self.assertEqual(row.volume, 1e6)
        self.assertEqual(row.data_source, f"{kline.KLINE_SOURCE_EM}_shares")

    def test_string_date_is_accepted(self) -> None:
        """_normalize_kline_df 产出的字符串日期（YYYY-MM-DD）应能正常回写。"""
        db = DatabaseManager(db_url="sqlite:///:memory:")
        # 模拟 normalize 后的 records：date 为字符串
        records = [
            {
                "date": "2026-01-05",
                "open": 10.0,
                "close": 10.5,
                "volume": 1000.0,
                "pct_chg": None,
                "_source": kline.KLINE_SOURCE_SINA,
            }
        ]
        # 不应抛异常（曾经因 SQLite Date 列拒字符串而失败）
        kline._save_to_stock_daily("000001", records, source=kline.KLINE_SOURCE_SINA)
        from sqlalchemy import select

        with db.get_session() as session:
            rows = session.execute(select(StockDaily).where(StockDaily.code == "000001")).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].date, date(2026, 1, 5))


if __name__ == "__main__":
    unittest.main()
