# -*- coding: utf-8 -*-
"""Tests for the batch financial sync (业绩快报 via akshare)."""

from __future__ import annotations

import importlib
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.fixture
def fin_sync():
    """Reload _financials_sync module to clear module-level state."""
    import api.v1.endpoints.stocks._financials_sync as mod

    importlib.reload(mod)
    return mod


def test_latest_report_period_uses_most_recent_quarter(fin_sync):
    """距 2025-08-15 25 天前的最近报告期应该是 2025-06-30。"""
    assert fin_sync.latest_report_period(date(2025, 8, 15)) == "20250630"


def test_latest_report_period_buffer_skips_recent(fin_sync):
    """距 2025-05-01 不到 25 天的 2025-03-31 还没披露，应该回退到 2024-12-31。"""
    # 2025-05-01 - 2025-03-31 = 31 days, buffer passes
    # 2025-05-01 - 2024-12-31 = 121 days, also passes; both candidates;
    # max wins -> 2025-03-31
    assert fin_sync.latest_report_period(date(2025, 5, 1)) == "20250331"
    # 2025-04-15 - 2025-03-31 = 15 days < 25, skip -> 2024-12-31
    assert fin_sync.latest_report_period(date(2025, 4, 15)) == "20241231"


def test_safe_float_handles_percent_strings(fin_sync):
    assert fin_sync._safe_float("38.71") == 38.71
    assert fin_sync._safe_float("38.71%") == 38.71
    assert fin_sync._safe_float(None) is None
    assert fin_sync._safe_float("abc") is None
    assert fin_sync._safe_float(0) == 0.0
    assert fin_sync._safe_float("") is None


def test_build_updates_joins_three_sources(fin_sync, monkeypatch):
    """验证 stock_lrb_em + zcfz_em + xjll_em 三个接口结果被正确合并。"""
    lrb_df = pd.DataFrame(
        {
            "股票代码": ["000001", "000002", "000003"],
            "营业总收入": [1_000_000_000.0, 200_000_000.0, 50_000_000.0],
            "净利润": [100_000_000.0, 30_000_000.0, 5_000_000.0],
        }
    )
    zcfz_df = pd.DataFrame(
        {
            "股票代码": ["000003", "000001", "000002"],
            "资产负债率": ["30.1", "45.5", "60.2"],
        }
    )
    xjll_df = pd.DataFrame(
        {
            "股票代码": ["000002", "000003", "000001"],
            "经营性现金流-现金流量净额": [10_000_000.0, 1_000_000.0, 80_000_000.0],
        }
    )

    monkeypatch.setattr(
        fin_sync,
        "_fetch_market_dataframe",
        lambda period, kind: {
            "lrb": lrb_df,
            "zcfz": zcfz_df,
            "xjll": xjll_df,
        }[kind],
    )

    updates, total = fin_sync._build_updates("20241231")

    assert total == 3
    by_code = {code: fields for code, fields in updates}
    assert by_code["000001"]["revenue_latest"] == 1_000_000_000.0
    assert by_code["000001"]["net_profit_latest"] == 100_000_000.0
    assert by_code["000001"]["debt_ratio"] == 45.5
    assert by_code["000001"]["operating_cf_latest"] == 80_000_000.0
    assert by_code["000001"]["report_date"] == "2024-12-31"
    assert by_code["000002"]["debt_ratio"] == 60.2
    assert "financial_fetched_at" in by_code["000003"]


def test_build_updates_skips_null_fields(fin_sync, monkeypatch):
    """None 字段不写入 dict（避免覆盖 DB 已有非空值）。"""
    lrb_df = pd.DataFrame(
        {
            "股票代码": ["000001"],
            "营业总收入": [None],
            "净利润": [None],
        }
    )
    zcfz_df = pd.DataFrame({"股票代码": ["000001"], "资产负债率": [None]})
    xjll_df = pd.DataFrame({"股票代码": ["000001"], "经营性现金流-现金流量净额": [None]})

    monkeypatch.setattr(
        fin_sync,
        "_fetch_market_dataframe",
        lambda period, kind: {
            "lrb": lrb_df,
            "zcfz": zcfz_df,
            "xjll": xjll_df,
        }[kind],
    )

    updates, total = fin_sync._build_updates("20241231")
    assert total == 1
    code, fields = updates[0]
    assert code == "000001"
    assert "revenue_latest" not in fields
    assert "net_profit_latest" not in fields
    assert "debt_ratio" not in fields
    assert "operating_cf_latest" not in fields
    # report_date 和 financial_fetched_at 总会被写入
    assert fields["report_date"] == "2024-12-31"
    assert "financial_fetched_at" in fields


def test_build_updates_falls_back_to_alt_code_column(fin_sync, monkeypatch):
    """有些 akshare 返回的列名是"代码"而非"股票代码"。"""
    lrb_df = pd.DataFrame(
        {
            "代码": ["000001"],
            "营业总收入": [1_000.0],
            "净利润": [100.0],
        }
    )
    zcfz_df = pd.DataFrame({"代码": ["000001"], "资产负债率": ["50.0"]})
    xjll_df = pd.DataFrame({"代码": ["000001"], "经营性现金流-现金流量净额": [50.0]})

    monkeypatch.setattr(
        fin_sync,
        "_fetch_market_dataframe",
        lambda period, kind: {
            "lrb": lrb_df,
            "zcfz": zcfz_df,
            "xjll": xjll_df,
        }[kind],
    )

    updates, total = fin_sync._build_updates("20241231")
    assert total == 1
    code, fields = updates[0]
    assert code == "000001"
    assert fields["revenue_latest"] == 1_000.0
