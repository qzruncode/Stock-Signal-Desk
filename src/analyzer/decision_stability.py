# -*- coding: utf-8 -*-
"""Preserve the analysis model's typed decision without rule-based rewrites.

Price, capital-flow and risk observations are evidence for the analysis model.
This boundary validates the structured decision enum and mirrors it into the
dashboard; it never converts numeric signs, price distances or prose into a
different trading decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from src.analyzer.result import AnalysisResult


def _sync_decision_dashboard(result: "AnalysisResult") -> None:
    dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
    result.dashboard = dashboard
    dashboard["sentiment_score"] = getattr(result, "sentiment_score", None)
    dashboard["operation_advice"] = getattr(result, "operation_advice", None)
    dashboard["decision_type"] = getattr(result, "decision_type", None)
    dashboard["decision_validation"] = {
        "source": "model_structured_output",
        "rule_based_rewrite": False,
    }


def stabilize_decision_with_structure(
    result: "AnalysisResult",
    trend_result: Any = None,
    fundamental_context: Optional[Dict[str, Any]] = None,
) -> None:
    """Validate the explicit enum and leave all business judgment to the model."""
    del trend_result, fundamental_context
    if not result:
        return

    decision_type = str(getattr(result, "decision_type", "") or "").strip().casefold()
    if decision_type not in {"buy", "hold", "sell"}:
        decision_type = "hold"
    result.decision_type = decision_type
    _sync_decision_dashboard(result)


__all__ = ["_sync_decision_dashboard", "stabilize_decision_with_structure"]
