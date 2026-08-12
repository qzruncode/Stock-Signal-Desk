# -*- coding: utf-8 -*-
"""Unit checks for the financial-only indicator executor."""

from __future__ import annotations

from unittest.mock import patch

from src.services.stock_screening import financial_screening as screener


class _Result:
    def mappings(self):
        return self

    def all(self):
        return [{"code": "000001", "name": "样本一", "ipo_date": "2010-01-01"}]


class _Session:
    def execute(self, *_args, **_kwargs):
        return _Result()


class _Database:
    def session_scope(self):
        class Context:
            def __enter__(self):
                return _Session()

            def __exit__(self, *_args):
                return False

        return Context()


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
        "financial_filters": [{
            "field": "parent_net_profit_ttm",
            "operator": "gt",
            "value": 100,
        }],
        "sort": {"field": "parent_net_profit_ttm", "order": "desc"},
        "output_fields": ["parent_net_profit_ttm", "financial_report_period", "financial_source"],
        "preview_limit": 20,
    }


def test_financial_screen_does_not_fetch_or_apply_atr_data() -> None:
    with (
        patch.object(screener.DatabaseManager, "get_instance", return_value=_Database()),
        patch.object(screener, "ensure_stock_universe", return_value={"maintenance_status": "ready"}),
        patch.object(
            screener,
            "_build_ttm_financials",
            return_value=(
                {
                    "000001": {
                        "parent_net_profit_ttm": 250,
                        "financial_report_period": "2025-12-31",
                        "financial_source": "测试财务源",
                    }
                },
                "2025-12-31",
            ),
        ),
        patch.object(screener, "_persist_financials"),
    ):
        result = screener.run_financial_screen(screen_spec=_screen_spec(), include_all_items=True)

    assert result["success"] is True
    assert result["matched_codes"] == ["000001"]
    assert result["items"][0]["parent_net_profit_ttm"] == 250
    assert "technical_rule" not in result["screen_spec"]
    assert result["coverage"]["complete"] is True


def test_financial_screen_rejects_metadata_only_spec() -> None:
    spec = _screen_spec()
    spec["financial_filters"] = []
    spec["sort"] = {"field": "code", "order": "asc"}
    spec["output_fields"] = ["financial_report_period", "financial_source"]

    result = screener.run_financial_screen(screen_spec=spec)

    assert result["success"] is False
    assert result["failure_stage"] == "spec_validation"
