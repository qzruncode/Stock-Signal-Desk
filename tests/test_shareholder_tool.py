# -*- coding: utf-8 -*-
"""Business and source-contract tests for shareholder structure."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from src.tools.get_shareholder_structure import (
    _expected_latest_report_date,
    _select_completed_report_date,
    get_shareholder_structure,
)


def _profile() -> dict:
    return {
        "gdrs": [
            {
                "SECURITY_CODE": "600519",
                "END_DATE": "2026-03-31 00:00:00",
                "HOLDER_TOTAL_NUM": 243159,
                "TOTAL_NUM_RATIO": -4.9759,
                "AVG_FREE_SHARES": 5150,
                "AVG_HOLD_AMT": 7294882.5,
                "HOLD_FOCUS": "非常分散",
                "HOLD_RATIO_TOTAL": 68.52,
                "FREEHOLD_RATIO_TOTAL": 68.52,
            },
            {"SECURITY_CODE": "600519", "END_DATE": "2025-12-31 00:00:00", "HOLDER_TOTAL_NUM": 255892},
        ],
        "sdgd": [
            {
                "SECURITY_CODE": "600519",
                "END_DATE": "2026-03-31 00:00:00",
                "HOLDER_RANK": 1,
                "HOLDER_NAME": "茅台集团",
                "SHARES_TYPE": "流通A股",
                "HOLD_NUM": 681282935,
                "HOLD_NUM_RATIO": 54.4,
                "HOLD_NUM_CHANGE": "3684225",
                "CHANGE_RATIO": 0.54,
            }
        ],
        "sdltgd": [{"HOLDER_NAME": "茅台集团", "HOLDER_TYPE": "其它"}],
        "sjkzr": [{"SECURITY_CODE": "600519", "HOLDER_NAME": "贵州省国资委", "HOLD_RATIO": None}],
        "jgcc_date": [
            {"REPORT_DATE": "2026-06-30 00:00:00"},
            {"REPORT_DATE": "2026-03-31 00:00:00"},
        ],
    }


def _institution() -> dict:
    return {
        "jgcc": [
            {
                "ORG_TYPE": "00",
                "REPORT_DATE": "2026-03-31 00:00:00",
                "TOTAL_ORG_NUM": 1379,
                "TOTAL_FREE_SHARES": 908574417,
                "TOTAL_SHARES_RATIO": 72.55418236,
                "ALL_SHARES_RATIO": 72.55418236,
            },
            {
                "ORG_TYPE": "01",
                "REPORT_DATE": "2026-03-31 00:00:00",
                "TOTAL_ORG_NUM": 1352,
                "TOTAL_FREE_SHARES": 65129723,
                "TOTAL_SHARES_RATIO": 5.2009,
                "ALL_SHARES_RATIO": 5.2009,
            },
        ]
    }


def test_institution_period_skips_incomplete_interim_reporting() -> None:
    selected, newest = _select_completed_report_date(
        ["2026-06-30", "2026-03-31", "2025-12-31"],
        date(2026, 7, 16),
    )
    assert newest == "2026-06-30"
    assert selected == "2026-03-31"
    assert _expected_latest_report_date(date(2026, 7, 16)) == date(2026, 3, 31)


def test_tool_uses_true_institution_aggregate_and_dated_single_stock_profile() -> None:
    changes = pd.DataFrame(
        [
            {
                "公告日期": "2026-06-01",
                "变动股东": "茅台集团",
                "变动数量": "减持1.5万",
                "交易均价": "1500",
                "剩余股份总数": "6.81亿",
                "变动期间": "2026.05.01-2026.05.31",
                "变动途径": "二级市场",
            }
        ]
    )
    with (
        patch("src.tools.get_shareholder_structure._fetch_f10_profile", return_value=_profile()),
        patch(
            "src.tools.get_shareholder_structure._fetch_institution_report", return_value=_institution()
        ) as institution,
        patch("src.tools.get_shareholder_structure._fetch_holder_change_frame", return_value=changes),
        patch("src.tools.get_shareholder_structure._today", return_value=date(2026, 7, 16)),
    ):
        result = get_shareholder_structure("600519", use_cache=False)

    institution.assert_called_once_with("600519", "2026-03-31")
    assert result["success"] is True
    assert result["is_stale"] is False
    assert result["holder_count"]["holder_count"] == 243159
    assert result["holder_count"]["change_count"] == -12733
    assert result["top_holders"][0]["holding_shares"] == 681282935
    assert result["top_holders"][0]["change_direction"] == "增持"
    assert result["institution_holding_ratio"] == 72.55418236
    assert result["institution_holding_ratio_basis"] == "percent_of_total_shares"
    assert result["institution_holding"]["institution_count"] == 1379
    assert result["institution_holding"]["breakdown"][0]["institution_type"] == "基金"
    assert result["actual_controller"]["name"] == "贵州省国资委"
    assert result["holder_changes"][0]["change_direction"] == "减持"
    assert result["holder_changes"][0]["change_shares"] == 15000
    assert result["holder_changes"][0]["signed_change_shares"] == -15000
    assert result["fallback_used"] is False
    assert any("跳过仍在披露中" in warning for warning in result["warnings"])


def test_no_controller_is_available_without_using_historical_market_wide_fallback() -> None:
    profile = _profile()
    profile["sjkzr"] = [{"SECURITY_CODE": "600519", "HOLDER_NAME": None, "HOLD_RATIO": None}]
    with (
        patch("src.tools.get_shareholder_structure._fetch_f10_profile", return_value=profile),
        patch("src.tools.get_shareholder_structure._fetch_institution_report", return_value=_institution()),
        patch("src.tools.get_shareholder_structure._fetch_holder_change_frame", return_value=pd.DataFrame()),
        patch("src.tools.get_shareholder_structure._today", return_value=date(2026, 7, 16)),
    ):
        result = get_shareholder_structure("600519", use_cache=False)

    assert result["actual_controller"]["available"] is False
    assert result["actual_controller"]["name"] is None
    assert result["partial"] is False
    assert not any("stock_hold_control_cninfo" in source for source in result["sources"])
