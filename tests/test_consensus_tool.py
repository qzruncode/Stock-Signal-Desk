# -*- coding: utf-8 -*-
"""Contracts for normalized sell-side consensus data."""

from __future__ import annotations

from unittest.mock import patch

from src.tools.get_consensus_estimates import get_consensus_estimates


def test_consensus_distinguishes_aggregate_and_individual_forecasts() -> None:
    summaries = {
        "eps": [{"年度": "2026", "预测机构数": 46, "最小值": 66.27, "均值": 68.83, "最大值": 77.85, "行业平均数": 8.57}],
        "net_profit": [{"年度": "2026", "预测机构数": 46, "最小值": 829.86, "均值": 861.83, "最大值": 974.85, "行业平均数": 105.37}],
    }
    institution_rows = [{
        "机构名称": "国海证券", "研究员": "刘旭德", "报告日期": "2026-06-12",
        "预测年报每股收益2026预测": 67.43,
        "预测年报净利润2026预测": "842.93亿",
    }]
    financial_rows = [
        {"预测指标": "营业收入(元)", "2025-实际值": "1688.38亿", "预测2026-平均": "1805.30亿"},
        {"预测指标": "净利润增长率", "2025-实际值": "-4.53%", "预测2026-平均": "5.37%"},
        {"预测指标": "净资产收益率", "2025-实际值": "32.53%", "预测2026-平均": "31.62%"},
    ]

    def fake_forecast(_, metric):
        return summaries[metric], False

    def fake_detail(_, kind):
        return (institution_rows if kind == "institutions" else financial_rows), False

    with patch("src.tools.get_consensus_estimates._forecast", side_effect=fake_forecast), \
         patch("src.tools.get_consensus_estimates._detail", side_effect=fake_detail):
        result = get_consensus_estimates("600519")

    assert result["success"] is True
    assert result["coverage_available"] is True
    assert result["estimates"][0]["coverage_count"] == 46
    assert result["estimates"][0]["eps"]["mean"] == 68.83
    assert result["estimates"][0]["eps"]["unit"] == "元/股"
    assert result["estimates"][0]["net_profit"]["mean"] == 861.83
    assert result["estimates"][0]["net_profit"]["unit"] == "亿元"
    assert result["institutions"][0]["forecasts"][0]["net_profit_yi"] == 842.93
    assert result["data_time"] == "2026-06-12"
    assert result["actuals"][0]["revenue_yi"] == 1688.38
    assert result["financial_forecasts"][0]["net_profit_growth_pct"] == 5.37
    assert result["financial_forecasts"][0]["roe_pct"] == 31.62


def test_metric_specific_result_does_not_claim_net_profit_data() -> None:
    with patch(
        "src.tools.get_consensus_estimates._forecast",
        return_value=([{"年度": "2026", "预测机构数": 3, "最小值": 1.0, "均值": 1.2, "最大值": 1.4, "行业平均数": 0.8}], False),
    ):
        result = get_consensus_estimates("600519", metric="eps")

    assert list(result["metrics"]) == ["eps"]
    assert "net_profit" not in result["estimates"][0]
    assert result["freshness_unknown"] is True


def test_no_analyst_coverage_is_valid_negative_evidence() -> None:
    with patch(
        "src.tools.get_consensus_estimates._forecast",
        return_value=([], True),
    ), patch(
        "src.tools.get_consensus_estimates._detail",
        side_effect=IndexError("no detail table"),
    ):
        result = get_consensus_estimates("301368")

    assert result["success"] is True
    assert result["partial"] is False
    assert result["coverage_available"] is False
    assert result["coverage_status"] == "no_sell_side_coverage"
    assert result["source_query_complete"] is True
    assert result["coverage_count_latest"] == 0
    assert result["errors"] == []
    assert any("无机构一致预测覆盖" in warning for warning in result["warnings"])
