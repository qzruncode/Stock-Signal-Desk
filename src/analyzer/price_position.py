# -*- coding: utf-8 -*-
"""price_position 回填：从 trend_result / 实时行情补全均线、支撑、压力等。"""

import logging
from typing import TYPE_CHECKING, Any, Dict

from src.analyzer._common import _is_value_placeholder

if TYPE_CHECKING:
    from src.analyzer.result import AnalysisResult

logger = logging.getLogger(__name__)

_PRICE_POS_KEYS = (
    "ma5",
    "ma10",
    "ma20",
    "bias_ma5",
    "bias_status",
    "current_price",
    "support_level",
    "resistance_level",
)


def fill_price_position_if_needed(
    result: "AnalysisResult",
    trend_result: Any = None,
    realtime_quote: Any = None,
) -> None:
    """Fill missing price_position fields from trend_result / realtime data (in-place)."""
    if not result:
        return
    try:
        if not result.dashboard:
            result.dashboard = {}
        dash = result.dashboard
        dp = dash.get("data_perspective") or {}
        dash["data_perspective"] = dp
        pp = dp.get("price_position") or {}

        computed: Dict[str, Any] = {}
        if trend_result:
            tr = (
                trend_result
                if isinstance(trend_result, dict)
                else (trend_result.__dict__ if hasattr(trend_result, "__dict__") else {})
            )
            computed["ma5"] = tr.get("ma5")
            computed["ma10"] = tr.get("ma10")
            computed["ma20"] = tr.get("ma20")
            computed["bias_ma5"] = tr.get("bias_ma5")
            computed["current_price"] = tr.get("current_price")
            support_levels = tr.get("support_levels") or []
            resistance_levels = tr.get("resistance_levels") or []
            if support_levels:
                computed["support_level"] = support_levels[0]
            if resistance_levels:
                computed["resistance_level"] = resistance_levels[0]
        if realtime_quote:
            rq = (
                realtime_quote
                if isinstance(realtime_quote, dict)
                else (realtime_quote.to_dict() if hasattr(realtime_quote, "to_dict") else {})
            )
            if _is_value_placeholder(computed.get("current_price")):
                computed["current_price"] = rq.get("price")

        filled = False
        for k in _PRICE_POS_KEYS:
            if _is_value_placeholder(pp.get(k)) and not _is_value_placeholder(computed.get(k)):
                pp[k] = computed[k]
                filled = True
        if filled:
            dp["price_position"] = pp
            logger.info("[price_position] Filled placeholder fields from computed data")
    except Exception as e:
        logger.warning("[price_position] Fill failed, skipping: %s", e)
