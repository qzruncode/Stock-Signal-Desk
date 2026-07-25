"""Valuation evidence must stay raw until the analysis model evaluates it."""

from __future__ import annotations

from api.v1.endpoints.financials import _build_price_overdraft_signal


def test_valuation_helper_returns_raw_comparables_without_business_label() -> None:
    result = _build_price_overdraft_signal({
        "pe_ttm": 48,
        "pe_dynamic": 46,
        "pb": 8.2,
        "peg": 2.3,
        "dividend_yield": 0.6,
        "pe_percentiles": {"1y": 92, "3y": 95, "5y": 97},
        "industry_average": {
            "industry": "白酒",
            "pe": 24,
            "pb": 4.1,
            "sample_size": 20,
        },
    })

    assert result["semantic_status"] == "model_required"
    assert result["status"] is None
    assert result["score"] is None
    assert result["signals"] == []
    assert result["metrics"]["pe_premium_vs_industry_pct"] == 100.0
    assert result["metrics"]["pb_premium_vs_industry_pct"] == 100.0
    assert result["metrics"]["forward_pe_change_vs_ttm_pct"] == -4.17


def test_forward_pe_is_preferred_as_the_comparable_expectation_metric() -> None:
    result = _build_price_overdraft_signal({
        "pe_ttm": 32,
        "forward_pe": 16,
        "pe_dynamic": 20,
        "pb": 4.0,
        "industry_average": {"pe": 28, "pb": 3.5},
    })

    assert result["metrics"]["forward_pe_change_vs_ttm_pct"] == -50.0
    assert result["valuation_expensive_score"] is None
    assert result["expectation_support_score"] is None


def test_sparse_evidence_is_reported_as_missing_fields_not_semantic_status() -> None:
    result = _build_price_overdraft_signal({
        "pe_ttm": None,
        "pe_dynamic": None,
        "pb": None,
        "peg": None,
        "dividend_yield": None,
        "pe_percentiles": {},
        "industry_average": {
            "industry": None,
            "pe": None,
            "pb": None,
            "sample_size": 0,
        },
    })

    assert result["status"] is None
    assert result["semantic_status"] == "model_required"
    assert result["coverage"]["available_fields"] == []
    assert set(result["coverage"]["missing_fields"]) == set(
        result["metrics"]
    )
