# -*- coding: utf-8 -*-
"""Unit checks for the financial-only indicator executor."""

from __future__ import annotations

from unittest.mock import patch

from src.services.stock_screening import financial_screening as screener


def _screen_spec() -> dict:
    return {
        "version": "1.0",
        "universe": {
            "status": "active",
            "markets": ["sz"],
            "include_st": False,
            "min_listing_trading_days": 1,
            "price_adjustment": "qfq",
        },
        "financial_filters": [
            {
                "field": "parent_net_profit_ttm",
                "operator": "gt",
                "value": 100,
            }
        ],
        "sort": {"field": "parent_net_profit_ttm", "order": "desc"},
        "output_fields": [
            "parent_net_profit_ttm",
            "financial_report_period",
            "financial_source",
        ],
        "preview_limit": 20,
    }


def test_financial_screen_does_not_fetch_or_apply_atr_data():
    from unittest.mock import Mock

    client = Mock()
    client.securities.return_value = {
        "items": [{"code": "000001", "name": "样本一", "ipo_date": "2010-01-01"}]
    }
    with (
        patch(
            "src.services.market_data_client.get_market_data_client",
            return_value=client,
        ),
        patch.object(
            screener,
            "ensure_stock_universe",
            return_value={"maintenance_status": "ready"},
        ),
        patch(
            "src.services.stock_screening.data.financial_rows",
            return_value=(
                {
                    "000001": {
                        "parent_net_profit_ttm": 250,
                        "financial_report_period": "2025-12-31",
                        "financial_source": "market-data-service",
                    }
                },
                "2025-12-31",
            ),
        ),
        patch(
            "src.services.stock_screening.data.daily_rows",
            side_effect=AssertionError("no ATR acquisition"),
        ),
    ):
        result = screener.run_financial_screen(
            screen_spec=_screen_spec(), include_all_items=True
        )
    assert result["success"] and result["matched_codes"] == ["000001"]
    assert result["items"][0]["parent_net_profit_ttm"] == 250
    assert (
        "technical_rule" not in result["screen_spec"] and result["coverage"]["complete"]
    )


def test_financial_screen_rejects_metadata_only_spec() -> None:
    spec = _screen_spec()
    spec["financial_filters"] = []
    spec["sort"] = {"field": "code", "order": "asc"}
    spec["output_fields"] = ["financial_report_period", "financial_source"]

    result = screener.run_financial_screen(screen_spec=spec)

    assert result["success"] is False
    assert result["failure_stage"] == "spec_validation"
