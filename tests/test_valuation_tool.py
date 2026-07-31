# -*- coding: utf-8 -*-
"""Business-contract tests for the valuation Agent tool."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from src.tools.get_valuation_ratios import (
    _history_statistics,
    _ttm_dividend,
    get_valuation_ratios,
)


def test_pe_percentile_excludes_loss_making_negative_pe_periods() -> None:
    frame = pd.DataFrame(
        {
            "数据日期": ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"],
            "PE(TTM)": [-10.0, 10.0, 20.0, 30.0],
        }
    )

    percentiles, stats = _history_statistics(frame, current_pe=20.0, as_of=date(2026, 7, 16))

    assert percentiles["1y"] == 66.67
    assert stats["1y"]["observations"] == 3
    assert stats["1y"]["min"] == 10.0


def test_ttm_dividend_sums_all_implemented_cash_distributions() -> None:
    frame = pd.DataFrame(
        [
            {"报告期": "2025-06-30", "除权除息日": "2025-10-15", "现金分红-现金分红比例": 2.0, "方案进度": "实施分配"},
            {"报告期": "2025-12-31", "除权除息日": "2026-06-12", "现金分红-现金分红比例": 3.0, "方案进度": "实施分配"},
            {"报告期": "2026-06-30", "除权除息日": None, "现金分红-现金分红比例": 4.0, "方案进度": "预披露"},
            {"报告期": "2024-12-31", "除权除息日": "2025-06-01", "现金分红-现金分红比例": 9.0, "方案进度": "实施分配"},
        ]
    )

    result = _ttm_dividend(frame, current_price=10.0, as_of=date(2026, 7, 16))

    assert result["cash_dividend_per_share_ttm"] == 0.5
    assert result["dividend_yield_ttm_pct"] == 5.0
    assert result["dividend_count_ttm"] == 2


def _history_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "数据日期": "2026-07-15",
                "当日收盘价": 100.0,
                "总市值": 1_000.0,
                "流通市值": 900.0,
                "总股本": 10.0,
                "流通股本": 9.0,
                "PE(TTM)": 20.0,
                "PE(静)": 22.0,
                "市净率": 4.0,
                "PEG值": -2.0,
                "市现率": 12.0,
                "市销率": 6.0,
            },
        ]
    )


def _dividend_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "报告期": "2025-12-31",
                "除权除息日": "2026-06-12",
                "现金分红-现金分红比例": 10.0,
                "方案进度": "实施分配",
            },
        ]
    )


def test_valuation_tool_separates_dynamic_forward_and_trailing_peg() -> None:
    comparison = {
        "report_date": "2025-12-31",
        "forward_pe": [{"year": 2025, "value": 18.0}, {"year": 2026, "value": 16.0}, {"year": 2027, "value": 14.0}],
        "forward_ps": [],
        "peg_forward": 1.2,
        "rank": 10,
        "total": 30,
        "percentile_rank_pct": 33.3333,
        "average": {"pe_ttm": 50.0, "pb_mrq": 3.0},
        "median": {"pe_ttm": 18.0, "pb_mrq": 2.5},
    }
    quote = {
        "name": "测试公司",
        "price": 110.0,
        "pe_dynamic": 15.0,
        "pe_static": 24.0,
        "pe_ttm": 22.0,
        "pb_annual": 5.0,
        "total_market_cap": 1_100.0,
        "circulating_market_cap": 990.0,
        "quote_time": None,
    }
    with (
        patch("src.tools.get_valuation_ratios._fetch_history", return_value=_history_frame()),
        patch("src.tools.get_valuation_ratios._fetch_quote", return_value=quote),
        patch("src.tools.get_valuation_ratios._fetch_comparison", return_value=comparison),
        patch("src.tools.get_valuation_ratios._fetch_dividends", return_value=_dividend_frame()),
        patch("src.tools.get_valuation_ratios._expected_completed_trade_day", return_value=date(2026, 7, 15)),
    ):
        result = get_valuation_ratios("600519", use_cache=False)

    assert result["success"] is True
    assert result["pe_dynamic"] == 15.0
    assert result["forward_pe"] == [
        {"year": 2026, "value": 16.0},
        {"year": 2027, "value": 14.0},
    ]
    assert result["excluded_expired_forward_years"] == [2025]
    assert result["forward_pe_current_year"] == 16.0
    assert result["peg_trailing"] == -2.2
    assert result["peg_forward"] == 1.2
    assert result["peg_basis"] == "forward_growth"
    assert result["pb_mrq"] == 4.4
    assert result["dividend_yield_ttm_pct"] == round(1.0 / 110.0 * 100, 4)
    assert result["industry_benchmark"]["preferred_for_comparison"] == "median"
    assert result["industry_average"]["pe"] == 18.0
