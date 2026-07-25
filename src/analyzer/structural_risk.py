# -*- coding: utf-8 -*-
"""Read structural-risk state from the model's typed dashboard fields."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.analyzer.result import AnalysisResult


def _is_significant_structural_risk(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, dict):
        if value.get("has_structural_risk") is True:
            return True
        value = value.get("risk_level")
    return str(value or "").strip().casefold() in {"high", "critical"}


def _has_structural_risk_alert(result: "AnalysisResult") -> bool:
    dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}

    intelligence = dashboard.get("intelligence") if isinstance(dashboard, dict) else None
    if isinstance(intelligence, dict):
        if intelligence.get("has_structural_risk") is True:
            return True
        if _is_significant_structural_risk(intelligence.get("risk_level")):
            return True
    return False
