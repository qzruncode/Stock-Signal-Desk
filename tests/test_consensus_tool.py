# -*- coding: utf-8 -*-
"""Contracts for the independent sell-side consensus source reads."""

from __future__ import annotations

from unittest.mock import patch

from market_data_service.providers.get_consensus_estimates import (
    read_consensus_financial_estimates_ths,
    read_consensus_institution_forecasts_ths,
    read_consensus_metric_ths,
)


def test_consensus_source_reads_keep_metric_and_detail_queries_separate() -> None:
    summaries = {
        "eps": [
            {
                "年度": "2026",
                "预测机构数": 46,
                "最小值": 66.27,
                "均值": 68.83,
                "最大值": 77.85,
                "行业平均数": 8.57,
            }
        ],
        "net_profit": [
            {
                "年度": "2026",
                "预测机构数": 46,
                "最小值": 829.86,
                "均值": 861.83,
                "最大值": 974.85,
                "行业平均数": 105.37,
            }
        ],
    }
    institution_rows = [
        {
            "机构名称": "国海证券",
            "研究员": "刘旭德",
            "报告日期": "2026-06-12",
            "预测年报每股收益2026预测": 67.43,
            "预测年报净利润2026预测": "842.93亿",
        }
    ]
    financial_rows = [
        {
            "预测指标": "营业收入(元)",
            "2025-实际值": "1688.38亿",
            "预测2026-平均": "1805.30亿",
        },
        {
            "预测指标": "净利润增长率",
            "2025-实际值": "-4.53%",
            "预测2026-平均": "5.37%",
        },
        {
            "预测指标": "净资产收益率",
            "2025-实际值": "32.53%",
            "预测2026-平均": "31.62%",
        },
    ]

    def fake_forecast(_, metric):
        return summaries[metric], False

    def fake_detail(_, kind):
        return (institution_rows if kind == "institutions" else financial_rows), False

    with (
        patch(
            "market_data_service.providers.get_consensus_estimates._forecast",
            side_effect=fake_forecast,
        ),
        patch(
            "market_data_service.providers.get_consensus_estimates._detail",
            side_effect=fake_detail,
        ),
    ):
        eps = read_consensus_metric_ths("600519", metric="eps")
        net_profit = read_consensus_metric_ths("600519", metric="net_profit")
        institutions = read_consensus_institution_forecasts_ths("600519")
        financials = read_consensus_financial_estimates_ths("600519")

    assert eps["success"] is True
    assert eps["estimates"][0]["coverage_count"] == 46
    assert eps["estimates"][0]["mean"] == 68.83
    assert eps["estimates"][0]["unit"] == "元/股"
    assert net_profit["estimates"][0]["mean"] == 861.83
    assert net_profit["estimates"][0]["unit"] == "亿元"
    assert institutions["institutions"][0]["forecasts"][0]["net_profit_yi"] == 842.93
    assert institutions["data_time"] == "2026-06-12"
    assert financials["actuals"][0]["revenue_yi"] == 1688.38
    assert financials["financial_forecasts"][0]["net_profit_growth_pct"] == 5.37
    assert financials["financial_forecasts"][0]["roe_pct"] == 31.62


def test_metric_read_reports_no_coverage_without_claiming_other_metrics() -> None:
    with patch(
        "market_data_service.providers.get_consensus_estimates._forecast",
        return_value=([], True),
    ):
        result = read_consensus_metric_ths("301368", metric="eps")

    assert result["success"] is True
    assert result["partial"] is False
    assert result["coverage_available"] is False
    assert result["coverage_status"] == "no_sell_side_coverage"
    assert result["errors"] == []
    assert any("无机构一致预测覆盖" in warning for warning in result["warnings"])
