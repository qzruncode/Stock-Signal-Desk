# -*- coding: utf-8 -*-
"""Route-level checks for the extensible settings-page indicator screener."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.auth as auth
from api.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
    ):
        yield
    auth._auth_enabled = None


def test_indicator_catalog_exposes_atr_preset(client: TestClient) -> None:
    response = client.get("/api/v1/indicator-screening/indicators")

    assert response.status_code == 200
    assert response.json()["plan_schema"]["properties"]["conditions"]["maxItems"] == 8
    indicators = response.json()["indicators"]
    assert [item["id"] for item in indicators] == [
        "atr_relative_volatility",
        "parent_net_profit",
        "debt_ratio",
        "annual_revenue",
    ]
    item = indicators[0]
    assert item["id"] == "atr_relative_volatility"
    assert item["default_spec"]["technical_rule"]["strategy"] == "atr_relative_frequency"
    assert item["default_spec"]["technical_rule"]["lookback_days"] == 250
    assert item["default_spec"]["technical_rule"]["min_qualified_days"] == 175
    assert item["default_spec"]["technical_rule"]["min_qualified_ratio_pct"] == 70
    assert item["default_spec"]["technical_rule"]["volatility_threshold_pct"] == 2.8
    assert len(item["default_spec"]["financial_filters"]) == 3
    assert item["parameter_schema"] == [{
        "key": "volatility_threshold_pct",
        "label": "ATR 相对波动率阈值 (%)",
        "control": "number",
        "min": 0.1,
        "max": 100,
        "step": 0.1,
        "default_value": 2.8,
    }]
    financial_items = {item["id"]: item for item in indicators[1:]}
    assert financial_items["parent_net_profit"]["label"] == "归母净利润"
    assert financial_items["debt_ratio"]["label"] == "负债率"
    assert financial_items["annual_revenue"]["label"] == "年营业收入"
    for financial_item in financial_items.values():
        assert [definition["key"] for definition in financial_item["parameter_schema"]] == ["operator", "value"]
        assert financial_item["parameter_schema"][0]["control"] == "select"
        assert financial_item["parameter_schema"][1]["control"] == "number"


@pytest.mark.parametrize(
    ("indicator", "field", "operator", "value"),
    [
        ("parent_net_profit", "parent_net_profit_ttm", "gte", 100_000_000),
        ("debt_ratio", "debt_ratio", "lte", 55),
        ("annual_revenue", "revenue_ttm", "gt", 800_000_000),
    ],
)
def test_indicator_plan_adapts_financial_conditions(
    client: TestClient,
    indicator: str,
    field: str,
    operator: str,
    value: float,
) -> None:
    mocked_result = {
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [],
        "items": [],
        "matched_codes": [],
        "total": 0,
    }
    plan = {
        "version": "1.0",
        "combination": "all",
        "conditions": [{
            "id": "condition-financial",
            "indicator": indicator,
            "parameters": {"operator": operator, "value": value},
        }],
        "universe": {
            "status": "active",
            "markets": ["sh", "sz", "bj"],
            "include_st": False,
            "min_listing_trading_days": 250,
            "price_adjustment": "qfq",
        },
        "financial_filters": [],
        "sort": {"field": "qualified_ratio_pct", "order": "desc"},
        "output_fields": ["qualified_ratio_pct"],
        "preview_limit": 20,
    }
    with patch(
        "api.v1.endpoints.indicator_screening.run_atr_volatility_screen",
        return_value=mocked_result,
    ) as runner:
        response = client.post("/api/v1/indicator-screening/run", json={"plan": plan})

    assert response.status_code == 200
    screen_spec = runner.call_args.kwargs["screen_spec"]
    matching_filters = [item for item in screen_spec["financial_filters"] if item["field"] == field]
    assert matching_filters == [{"field": field, "operator": operator, "value": value}]


def test_indicator_plan_uses_fixed_atr_defaults_and_surfaces_threshold(client: TestClient) -> None:
    mocked_result = {
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [],
        "items": [],
        "matched_codes": [],
        "total": 0,
    }
    plan = {
        "version": "1.0",
        "combination": "all",
        "conditions": [{
            "id": "condition-1",
            "indicator": "atr_relative_volatility",
            "parameters": {
                "atr_period": 2,
                "threshold_value": 99,
                "volatility_threshold_pct": 3.2,
            },
        }],
        "universe": {
            "status": "active",
            "markets": ["sh", "sz", "bj"],
            "include_st": False,
            "min_listing_trading_days": 250,
            "price_adjustment": "qfq",
        },
        "financial_filters": [],
        "sort": {"field": "qualified_ratio_pct", "order": "desc"},
        "output_fields": ["qualified_ratio_pct"],
        "preview_limit": 20,
    }
    with patch(
        "api.v1.endpoints.indicator_screening.run_atr_volatility_screen",
        return_value=mocked_result,
    ) as runner:
        response = client.post("/api/v1/indicator-screening/run", json={"plan": plan})

    assert response.status_code == 200
    assert response.json()["plan"]["combination"] == "all"
    runner.assert_called_once_with(
        screen_spec={
            "version": "1.0",
            "universe": {
                "status": "active",
                "markets": ["sh", "sz", "bj"],
                "include_st": False,
                "min_listing_trading_days": 250,
                "price_adjustment": "qfq",
            },
            "technical_rule": {
                "strategy": "atr_relative_frequency",
                "atr_period": 14,
                "atr_average": "sma",
                "baseline_period": 60,
                "baseline_average": "sma",
                "threshold_operator": "divide",
                "threshold_value": 1.27,
                "volatility_threshold_pct": 3.2,
                "daily_comparison": "gt",
                "lookback_days": 250,
                "min_qualified_days": 175,
                "min_qualified_ratio_pct": 70,
            },
            "financial_filters": [
                {"field": "revenue_ttm", "operator": "gt", "value": 500_000_000},
                {"field": "deducted_net_profit_ttm", "operator": "gt", "value": 0},
                {"field": "debt_ratio", "operator": "lt", "value": 70},
            ],
            "sort": {"field": "qualified_ratio_pct", "order": "desc"},
            "output_fields": [
                "current_atr_pct",
                "long_term_mean_pct",
                "dynamic_warning_pct",
                "qualified_days",
                "qualified_ratio_pct",
                "latest_trade_date",
            ],
            "preview_limit": 20,
        },
        refresh_if_stale=True,
        include_matched_codes=True,
    )


def test_indicator_plan_limits_runner_to_selected_watchlist_group(client: TestClient) -> None:
    mocked_result = {
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [],
        "items": [],
        "matched_codes": [],
        "total": 0,
    }
    plan = {
        "version": "1.0",
        "combination": "all",
        "scope": {"type": "watchlist_group", "group_id": "7"},
        "conditions": [{
            "id": "condition-1",
            "indicator": "atr_relative_volatility",
            "parameters": {"volatility_threshold_pct": 2.8},
        }],
        "universe": {
            "status": "active",
            "markets": ["sh", "sz", "bj"],
            "include_st": False,
            "min_listing_trading_days": 250,
            "price_adjustment": "qfq",
        },
        "financial_filters": [],
        "sort": {"field": "qualified_ratio_pct", "order": "desc"},
        "output_fields": ["qualified_ratio_pct"],
        "preview_limit": 20,
    }
    with (
        patch("api.v1.endpoints.indicator_screening.DatabaseManager.get_instance") as get_database,
        patch(
            "api.v1.endpoints.indicator_screening.run_atr_volatility_screen",
            return_value=mocked_result,
        ) as runner,
    ):
        get_database.return_value.list_watchlist_groups.return_value = [{
            "id": "7",
            "name": "已有分组",
            "codes": ["600000", "000001"],
        }]
        response = client.post("/api/v1/indicator-screening/run", json={"plan": plan})

    assert response.status_code == 200
    screen_spec = runner.call_args.kwargs["screen_spec"]
    assert screen_spec["universe"]["codes"] == ["600000", "000001"]
    assert response.json()["plan"]["scope"] == {"type": "watchlist_group", "group_id": "7"}


def test_indicator_plan_composes_condition_match_sets(client: TestClient) -> None:
    condition_parameters = {
        "atr_period": 14,
        "atr_average": "wilder",
        "baseline_period": 60,
        "baseline_average": "sma",
        "threshold_operator": "multiply",
        "threshold_value": 1.2,
        "daily_comparison": "gte",
        "lookback_days": 20,
        "min_qualified_days": None,
        "min_qualified_ratio_pct": 60,
    }
    plan = {
        "version": "1.0",
        "combination": "all",
        "conditions": [
            {"id": "one", "indicator": "atr_relative_volatility", "parameters": condition_parameters},
            {"id": "two", "indicator": "atr_relative_volatility", "parameters": condition_parameters},
        ],
        "universe": {
            "status": "active",
            "markets": ["sh"],
            "include_st": False,
            "min_listing_trading_days": 250,
            "price_adjustment": "qfq",
        },
        "financial_filters": [],
        "sort": {"field": "code", "order": "asc"},
        "output_fields": ["current_atr_pct"],
        "preview_limit": 20,
    }
    results = [
        {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "items": [{"code": "600000"}, {"code": "600001"}],
            "matched_codes": ["600000", "600001"],
            "total": 2,
            "applied_rules": ["第一条件"],
        },
        {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "items": [{"code": "600000"}, {"code": "600002"}],
            "matched_codes": ["600000", "600002"],
            "total": 2,
            "applied_rules": ["第二条件"],
        },
    ]
    with patch(
        "api.v1.endpoints.indicator_screening.run_atr_volatility_screen",
        side_effect=results,
    ) as runner:
        response = client.post("/api/v1/indicator-screening/run", json={"plan": plan})

    assert response.status_code == 200
    assert response.json()["matched_codes"] == ["600000"]
    assert response.json()["total"] == 1
    assert runner.call_count == 2


def test_indicator_run_returns_runner_result_and_indicator_id(client: TestClient) -> None:
    mocked_result = {
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [],
        "items": [{"code": "600519"}],
        "matched_codes": ["600519"],
        "total": 1,
    }
    with patch(
        "api.v1.endpoints.indicator_screening.run_atr_volatility_screen",
        return_value=mocked_result,
    ) as runner:
        response = client.post(
            "/api/v1/indicator-screening/run",
            json={"indicator": "atr_relative_volatility", "screen_spec": {"version": "1.0"}},
        )

    assert response.status_code == 200
    assert response.json()["indicator"] == "atr_relative_volatility"
    assert response.json()["matched_codes"] == ["600519"]
    runner.assert_called_once_with(
        screen_spec={"version": "1.0"},
        refresh_if_stale=True,
        include_matched_codes=True,
    )


def test_indicator_run_rejects_unknown_indicator(client: TestClient) -> None:
    response = client.post(
        "/api/v1/indicator-screening/run",
        json={"indicator": "unknown", "screen_spec": {}},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_indicator"
