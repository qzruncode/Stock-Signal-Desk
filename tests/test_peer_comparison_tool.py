# -*- coding: utf-8 -*-
"""Contracts for normalized industry peer comparison."""

from __future__ import annotations

from src.tools.get_peer_comparison import _dimension_result


def test_valuation_peer_normalizer_handles_missing_ev_ebitda() -> None:
    rows = [
        {"CORRE_SECURITY_CODE": "行业中值", "CORRE_SECURITY_NAME": "行业中值", "TOTAL_COUNT": 42, "REPORT_DATE": "2025-12-31", "PE_TTM": 6.0, "PB_MRQ": 0.55},
        {"CORRE_SECURITY_CODE": "行业平均", "CORRE_SECURITY_NAME": "行业平均", "TOTAL_COUNT": 42, "REPORT_DATE": "2025-12-31", "PE_TTM": 5.9, "PB_MRQ": 0.57},
        {"CORRE_SECURITY_CODE": "000001", "CORRE_SECURITY_NAME": "平安银行", "TOTAL_COUNT": 42, "REPORT_DATE": "2025-12-31", "PAIMING": 34, "PEG": 1.3, "PE_TTM": 4.9, "PB_MRQ": 0.45},
        {"CORRE_SECURITY_CODE": "002948", "CORRE_SECURITY_NAME": "青岛银行", "TOTAL_COUNT": 42, "REPORT_DATE": "2025-12-31", "PAIMING": 1, "PEG": 0.2, "PE_TTM": 6.1, "PB_MRQ": 0.77},
    ]

    result = _dimension_result("000001", "valuation", rows)

    assert result["success"] is True
    assert result["sample_size"] == 42
    assert result["target_rank"] == 34
    assert result["target"]["ev_ebitda_report_year"] is None
    assert result["industry_median"]["pe_ttm"] == 6.0
    assert result["ranking"]["metric"] == "provider_valuation_rank"


def test_profitability_converts_percentage_encoded_dupont_ratios() -> None:
    rows = [{
        "CORRE_SECURITY_CODE": "000001", "CORRE_SECURITY_NAME": "平安银行",
        "TOTAL_COUNT": 42, "REPORT_DATE": "2025-12-31", "PAIMING": 23,
        "ROE_AVG": 9.2, "XSJLL_AVG": 30.33, "TOAZZL_AVG": 2.62, "QYCS_AVG": 1141.29,
        "ROEPJ_L1": 8.15, "XSJLL_L1": 32.43, "TOAZZL_L1": 2.25, "QYCS_L1": 1075.10,
    }]

    result = _dimension_result("000001", "profitability", rows)
    target = result["target"]

    assert target["asset_turnover_3y_average"] == 0.0262
    assert target["equity_multiplier_3y_average"] == 11.4129
    assert target["annual_history"][-1]["asset_turnover"] == 0.0225
    assert target["annual_history"][-1]["equity_multiplier"] == 10.751


def test_scale_converts_circulating_market_cap_from_yi_to_yuan() -> None:
    rows = [
        {"CORRE_SECURITY_CODE": "600519", "CORRE_SECURITY_NAME": "贵州茅台", "REPORT_TYPE": "2026年一季报", "TOTAL_CAP": 1.56e12, "FREECAP": 15639.27, "TOTAL_CAP_RANK": 1, "TOTAL_OPERATEINCOME": 5.47e10, "NETPROFIT": 2.81e10},
        {"CORRE_SECURITY_CODE": "000858", "CORRE_SECURITY_NAME": "五粮液", "REPORT_TYPE": "2026年一季报", "TOTAL_CAP": 2.96e11, "FREECAP": 2965.48, "TOTAL_CAP_RANK": 2, "TOTAL_OPERATEINCOME": 2.28e10, "NETPROFIT": 8.32e9},
    ]

    result = _dimension_result("600519", "scale", rows)

    assert result["sample_size"] == 2
    assert result["target"]["circulating_market_cap"] == 1_563_927_000_000.0
    assert result["target_rank"] == 1
