# -*- coding: utf-8 -*-
"""Contracts for normalized主营构成 Agent data."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from src.tools.get_business_segments import get_business_segments


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "股票代码": "600519",
                "报告日期": pd.Timestamp("2025-12-31").date(),
                "分类类型": "按产品分类",
                "主营构成": "茅台酒",
                "主营收入": 80.0,
                "收入比例": 0.8,
                "主营成本": 10.0,
                "成本比例": 0.5,
                "主营利润": 70.0,
                "利润比例": 0.875,
                "毛利率": 0.875,
            },
            {
                "股票代码": "600519",
                "报告日期": pd.Timestamp("2025-12-31").date(),
                "分类类型": "按产品分类",
                "主营构成": "系列酒",
                "主营收入": 20.0,
                "收入比例": 0.2,
                "主营成本": 10.0,
                "成本比例": 0.5,
                "主营利润": 10.0,
                "利润比例": 0.125,
                "毛利率": 0.5,
            },
            {
                "股票代码": "600519",
                "报告日期": pd.Timestamp("2025-06-30").date(),
                "分类类型": "按产品分类",
                "主营构成": "茅台酒",
                "主营收入": 40.0,
                "收入比例": 1.0,
                "主营成本": 5.0,
                "成本比例": 1.0,
                "主营利润": 35.0,
                "利润比例": 1.0,
                "毛利率": 0.875,
            },
            {
                "股票代码": "600519",
                "报告日期": pd.Timestamp("2025-12-31").date(),
                "分类类型": "按地区分类",
                "主营构成": "国内",
                "主营收入": 100.0,
                "收入比例": 1.0,
                "主营成本": 20.0,
                "成本比例": 1.0,
                "主营利润": 80.0,
                "利润比例": 1.0,
                "毛利率": 0.8,
            },
        ]
    )


def test_business_segments_normalizes_units_basis_and_concentration() -> None:
    with patch("src.tools.get_business_segments.cached_call", return_value=(_frame(), False)):
        result = get_business_segments("600519", category="product", periods=2)

    assert result["success"] is True
    assert result["periods"] == ["2025-12-31", "2025-06-30"]
    assert result["amount_unit"] == "元"
    assert result["ratio_unit"] == "%"
    latest = next(
        item for item in result["items"] if item["segment_name"] == "茅台酒" and item["report_date"] == "2025-12-31"
    )
    assert latest["flow_basis"] == "full_year"
    assert latest["revenue_share_pct"] == 80.0
    assert latest["gross_margin_pct"] == 87.5
    half_year = next(item for item in result["items"] if item["report_date"] == "2025-06-30")
    assert half_year["flow_basis"] == "year_to_date"
    latest_summary = next(item for item in result["summaries"] if item["report_date"] == "2025-12-31")
    assert latest_summary["largest_revenue_segment"] == "茅台酒"
    assert latest_summary["top3_revenue_share_pct"] == 100.0


def test_business_segments_filters_category_before_selecting_periods() -> None:
    frame = _frame()
    frame = pd.concat(
        [
            frame,
            pd.DataFrame(
                [
                    {
                        "股票代码": "600519",
                        "报告日期": pd.Timestamp("2026-03-31").date(),
                        "分类类型": "按地区分类",
                        "主营构成": "国内",
                        "主营收入": 30.0,
                        "收入比例": 1.0,
                        "主营成本": 5.0,
                        "成本比例": 1.0,
                        "主营利润": 25.0,
                        "利润比例": 1.0,
                        "毛利率": 0.8333,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    with patch("src.tools.get_business_segments.cached_call", return_value=(frame, False)):
        result = get_business_segments("600519", category="product", periods=1)

    assert result["periods"] == ["2025-12-31"]
    assert all(item["category"] == "product" for item in result["items"])


def test_business_segments_uses_bse_prefix_and_reports_empty_category() -> None:
    captured: list[str] = []

    def fake_fetch(symbol: str):
        captured.append(symbol)
        return _frame().iloc[0:0]

    with (
        patch("src.tools.get_business_segments.ak.stock_zygc_em", side_effect=fake_fetch),
        patch("src.tools.get_business_segments.cached_call", side_effect=lambda _, fn, **__: (fn(), False)),
    ):
        result = get_business_segments("920000", category="industry")

    assert captured == ["BJ920000"]
    assert result["success"] is False
    assert result["is_stale"] is None
    assert result["items"] == []
