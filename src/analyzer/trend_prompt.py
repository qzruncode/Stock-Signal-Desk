# -*- coding: utf-8 -*-
"""Normalize typed trend context before it is sent to the analysis model.

The module does not infer direction from descriptive prose. Direction is read
only from explicit structured fields produced by an upstream component.
"""

from typing import Any, Dict, List


def _normalize_prompt_reason_items(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    return [text for item in items if (text := str(item).strip())]


def _infer_trend_direction(trend: Dict[str, Any]) -> str:
    """Return an explicit structured direction, never a prose-word match."""
    direction = str(trend.get("trend_direction") or trend.get("direction") or "").strip().casefold()
    if direction in {"bullish", "bearish", "neutral"}:
        return direction

    is_bullish = trend.get("is_bullish")
    if is_bullish is True:
        return "bullish"
    if is_bullish is False:
        return "bearish"
    return "neutral"


def _sanitize_trend_analysis_for_prompt(
    trend: Any,
    *,
    volume_change_ratio: Any = None,
) -> Dict[str, Any]:
    """Create a clean prompt copy without reinterpreting business semantics."""
    trend_dict = dict(trend) if isinstance(trend, dict) else {}
    trend_dict["signal_reasons"] = _normalize_prompt_reason_items(trend_dict.get("signal_reasons"))
    trend_dict["risk_factors"] = _normalize_prompt_reason_items(trend_dict.get("risk_factors"))

    trend_dict["prompt_consistency_notes"] = []
    trend_dict["prompt_volume_change_ratio"] = volume_change_ratio
    trend_dict["prompt_trend_direction"] = _infer_trend_direction(trend_dict)
    return trend_dict
