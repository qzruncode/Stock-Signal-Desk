# src/services/buy_criteria/evaluators/catalyst_events.py
"""Evaluator ⑥: 催化事件 — Are there catalysts in the next 6-12 months?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import CATALYST_EVENTS

logger = logging.getLogger(__name__)


class CatalystEventsEvaluator(BaseCriterionEvaluator):
    criterion_id = "catalyst_events"
    criterion_name = "催化事件"
    index = 5

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Catalyst analysis data
        try:
            catalyst = ds.get_catalyst_data(symbol)
            raw["catalyst"] = {
                "events": catalyst.get("events") or catalyst.get("catalysts") or [],
                "summary": catalyst.get("summary") or catalyst.get("catalyst_summary"),
                "timeframe": catalyst.get("timeframe"),
            }
        except Exception as exc:
            logger.warning("[catalyst] catalyst_data failed: %s", exc)
            raw["catalyst_error"] = str(exc)

        # Risk events (to cross-reference)
        try:
            risk = ds.get_risk_events(symbol, days=180)
            raw["risk_events"] = {
                "items": risk.get("items", [])[:5],
                "severity": risk.get("analysis", {}).get("severity_distribution"),
            }
        except Exception as exc:
            logger.warning("[catalyst] risk_events failed: %s", exc)

        # Build summary
        parts = []
        cat = raw.get("catalyst", {})
        if cat.get("summary"):
            parts.append(f"催化总结：{cat['summary']}")
        events = cat.get("events", [])
        if events:
            event_names = [e.get("name") or e.get("title") or str(e) for e in events[:5]]
            parts.append(f"催化事件：{', '.join(event_names)}")
        if cat.get("timeframe"):
            parts.append(f"时间窗口：{cat['timeframe']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return CATALYST_EVENTS
