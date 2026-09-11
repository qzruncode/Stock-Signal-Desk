# -*- coding: utf-8 -*-
"""Contracts for normalized主营构成 Agent data."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from market_data_service.providers.get_business_segments import (
    read_business_segments_eastmoney,
)


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


def test_agent_business_segment_read_returns_rows_without_local_concentration() -> None:
    with (
        patch(
            "market_data_service.providers.get_business_segments.cached_call",
            return_value=(_frame(), False),
        ),
    ):
        result = read_business_segments_eastmoney("600519", category="product", periods=2)

    assert result["success"] is True
    assert result["source_scope"] == "reported_business_segments"
    assert "summaries" not in result
    assert [item["segment_name"] for item in result["items"]] == ["茅台酒", "系列酒", "茅台酒"]


def test_business_segments_uses_bse_prefix_and_reports_empty_category() -> None:
    captured: list[str] = []

    def fake_fetch(symbol: str):
        captured.append(symbol)
        return _frame().iloc[0:0]

    with (
        patch(
            "market_data_service.providers.get_business_segments.ak.stock_zygc_em",
            side_effect=fake_fetch,
        ),
        patch(
            "market_data_service.providers.get_business_segments.cached_call",
            side_effect=lambda _, fn, **__: (fn(), False),
        ),
    ):
        result = read_business_segments_eastmoney("920000", category="industry")

    assert captured == ["BJ920000"]
    assert result["success"] is False
    assert result["is_stale"] is None
    assert result["items"] == []
