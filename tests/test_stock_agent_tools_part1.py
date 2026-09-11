# -*- coding: utf-8 -*-
"""Offline contract tests for current market-data source tools."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pandas as pd

from market_data_service.providers.get_sector_flow import read_sector_flow_eastmoney
from market_data_service.providers.get_stock_capital_flow import (
    _is_stale,
    _market_for,
    read_stock_capital_flow_history_eastmoney,
)


def test_capital_flow_history_preserves_daily_records_and_units() -> None:
    direct = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-07-14").date(),
                "main_net_inflow": 10,
                "main_net_inflow_pct": 1.0,
            },
            {
                "date": pd.Timestamp("2026-07-15").date(),
                "main_net_inflow": -3,
                "main_net_inflow_pct": -0.5,
            },
        ]
    )
    direct.attrs["history_transport"] = "curl_cffi"

    with (
        patch(
            "market_data_service.providers.get_stock_capital_flow.cached_call",
            return_value=(direct, False),
        ),
        patch(
            "market_data_service.providers.get_stock_capital_flow._is_stale",
            return_value=(False, None),
        ),
    ):
        result = read_stock_capital_flow_history_eastmoney("600519", days=20)

    assert result["success"] is True
    assert result["fallback_used"] is False
    assert result["item_count"] == 2
    assert result["items"][0]["main_net_inflow"] == 10
    assert result["items"][1]["main_net_inflow"] == -3
    assert "summary" not in result
    assert result["amount_unit"] == "元"
    assert result["ratio_unit"] == "%"


def test_capital_flow_recognizes_bse_920_codes() -> None:
    assert _market_for("920000") == "bj"
    assert _market_for("600519") == "sh"
    assert _market_for("000001") == "sz"


def test_capital_flow_history_uses_latest_completed_trade_day_intraday() -> None:
    now = datetime.fromisoformat("2026-09-04T11:10:00+08:00")
    with patch(
        "src.tools._trading_calendar.trade_dates",
        return_value=[date(2026, 9, 3), date(2026, 9, 4)],
    ):
        assert _is_stale(date(2026, 9, 3), now) == (False, None)
        stale, warning = _is_stale(date(2026, 9, 2), now)

    assert stale is True
    assert "2026-09-03" in (warning or "")


def test_agent_sector_flow_read_preserves_source_order_without_rank_or_top_lists() -> (
    None
):
    source_rows = [
        {
            "f12": "BK2",
            "f14": "来源先返回的板块",
            "f3": -1.2,
            "f62": -90,
            "f184": -3.0,
            "f66": -50,
            "f72": -40,
            "f78": 10,
            "f84": 80,
            "f124": 1784180000,
        },
        {
            "f12": "BK1",
            "f14": "来源后返回的板块",
            "f3": 1.2,
            "f62": 100,
            "f184": 2.0,
            "f66": 60,
            "f72": 40,
            "f78": -20,
            "f84": -80,
            "f124": 1784180000,
        },
    ]

    with (
        patch(
            "market_data_service.providers.get_sector_flow.cached_call",
            side_effect=lambda _key, call, **_kwargs: (call(), False),
        ),
        patch(
            "market_data_service.providers.get_sector_flow._request_page",
            return_value=(source_rows, len(source_rows)),
        ),
        patch(
            "market_data_service.providers.get_sector_flow._freshness",
            return_value=(False, None),
        ),
    ):
        result = read_sector_flow_eastmoney(
            type="industry", period="today", max_items=2
        )

    assert result["success"] is True
    assert result["source_scope"] == "sector_flow_source_records"
    assert [item["sector_code"] for item in result["items"]] == ["BK2", "BK1"]
    assert all("main_flow_rank" not in item for item in result["items"])
    assert "inflow_top" not in result and "outflow_top" not in result
