# -*- coding: utf-8 -*-
"""Raw valuation evidence used by model-based price-expectation analysis."""

from __future__ import annotations

from api.v1.endpoints.financials._symbol import _safe_float


def _relative_change(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline in (None, 0):
        return None
    return round((value - baseline) / baseline * 100, 2)


def _build_price_overdraft_signal(payload: dict) -> dict:
    """Return comparable metrics without assigning valuation labels or scores.

    The historical endpoint name is retained for API compatibility.  Semantic
    conclusions such as expensive, cheap, supportive or overdrafted must be
    made by the analysis model from this evidence and its provenance.
    """
    pe_ttm = _safe_float(payload.get("pe_ttm"))
    forward_pe = _safe_float(payload.get("forward_pe"))
    pe_dynamic = _safe_float(payload.get("pe_dynamic"))
    expectation_pe = forward_pe if forward_pe is not None else pe_dynamic
    pb = _safe_float(payload.get("pb"))
    peg = _safe_float(payload.get("peg"))
    dividend_yield = _safe_float(payload.get("dividend_yield"))
    pe_percentiles = payload.get("pe_percentiles") or {}
    industry_average = payload.get("industry_average") or {}
    industry_pe = _safe_float(industry_average.get("pe"))
    industry_pb = _safe_float(industry_average.get("pb"))

    metrics = {
        "pe_ttm": pe_ttm,
        "pe_dynamic": pe_dynamic,
        "forward_pe": forward_pe,
        "pb": pb,
        "peg": peg,
        "dividend_yield": dividend_yield,
        "pe_percentile_1y": _safe_float(pe_percentiles.get("1y")),
        "pe_percentile_3y": _safe_float(pe_percentiles.get("3y")),
        "pe_percentile_5y": _safe_float(pe_percentiles.get("5y")),
        "industry_pe": industry_pe,
        "industry_pb": industry_pb,
        "pe_premium_vs_industry_pct": _relative_change(
            pe_ttm,
            industry_pe,
        ),
        "pb_premium_vs_industry_pct": _relative_change(
            pb,
            industry_pb,
        ),
        "forward_pe_change_vs_ttm_pct": _relative_change(
            expectation_pe,
            pe_ttm,
        ),
    }
    available = [key for key, value in metrics.items() if value is not None]
    missing = [key for key, value in metrics.items() if value is None]
    return {
        "semantic_status": "model_required",
        "status": None,
        "score": None,
        "confidence": None,
        "valuation_expensive_score": None,
        "expectation_support_score": None,
        "signals": [],
        "metrics": metrics,
        "coverage": {
            "available_fields": available,
            "missing_fields": missing,
        },
        "reasoning": [],
        "limitations": [],
    }
