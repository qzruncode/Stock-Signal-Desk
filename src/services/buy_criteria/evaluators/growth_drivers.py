# src/services/buy_criteria/evaluators/growth_drivers.py
"""Evaluator ⑤: 驱动因素 — Does the industry have policy/tech/demand drivers?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import GROWTH_DRIVERS

logger = logging.getLogger(__name__)


class GrowthDriversEvaluator(BaseCriterionEvaluator):
    criterion_id = "growth_drivers"
    criterion_name = "驱动因素"
    index = 4

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Industry cycle report (has driver analysis)
        try:
            report = ds.get_industry_cycle_report(symbol)
            raw["industry_cycle"] = {
                "policy_drivers": report.get("policy_drivers") or report.get("policy_factors"),
                "tech_drivers": report.get("tech_drivers") or report.get("technology_factors"),
                "demand_drivers": report.get("demand_drivers") or report.get("demand_factors"),
                "growth_drivers": report.get("growth_drivers"),
                "core_logic": report.get("core_logic"),
            }
        except Exception as exc:
            logger.warning("[drivers] industry_cycle failed: %s", exc)
            raw["industry_cycle_error"] = str(exc)

        # Sentiment (for market attention to drivers)
        try:
            sentiment = ds.get_sentiment(symbol)
            items = sentiment.get("items") or []
            raw["sentiment"] = {
                "top_items": [{"title": i.get("title"), "score": i.get("score")} for i in items[:5]],
                "research_items_count": len(sentiment.get("research_items") or []),
            }
        except Exception as exc:
            logger.warning("[drivers] sentiment failed: %s", exc)

        # Build summary
        parts = []
        ic = raw.get("industry_cycle", {})
        if ic.get("policy_drivers"):
            parts.append(f"政策驱动：{ic['policy_drivers']}")
        if ic.get("tech_drivers"):
            parts.append(f"技术驱动：{ic['tech_drivers']}")
        if ic.get("demand_drivers"):
            parts.append(f"需求驱动：{ic['demand_drivers']}")
        if ic.get("growth_drivers"):
            parts.append(f"增长驱动：{ic['growth_drivers']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return GROWTH_DRIVERS
