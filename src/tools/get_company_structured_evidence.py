# -*- coding: utf-8 -*-
"""Standalone compatibility helper for shared AKShare evidence domains."""

from __future__ import annotations

from typing import Any

from src.services.akshare_evidence import get_company_evidence

_SCOPES = {
    "all": None,
    "ownership": ("ownership",),
    "financial_events": ("financial_events",),
    "corporate_events": ("corporate_events",),
    "trading_evidence": ("trading_evidence",),
    "risk_and_catalyst": ("ownership", "financial_events", "corporate_events"),
}


def get_company_structured_evidence(
    symbol: str,
    scope: str = "all",
    days: int = 730,
    report_period_count: int = 4,
) -> dict[str, Any]:
    if scope not in _SCOPES:
        raise ValueError("scope 必须是 all/ownership/financial_events/corporate_events/trading_evidence/risk_and_catalyst")
    if not 30 <= int(days) <= 1460:
        raise ValueError("days 必须在 30 到 1460 之间")
    if not 1 <= int(report_period_count) <= 8:
        raise ValueError("report_period_count 必须在 1 到 8 之间")
    return get_company_evidence(
        symbol,
        sections=_SCOPES[scope],
        days=int(days),
        report_period_count=int(report_period_count),
    )


__all__ = ["get_company_structured_evidence"]
