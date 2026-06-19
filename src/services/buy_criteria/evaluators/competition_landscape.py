# src/services/buy_criteria/evaluators/competition_landscape.py
"""Evaluator ④: 竞争格局 — Is the industry free from price wars/involution?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import COMPETITION_LANDSCAPE

logger = logging.getLogger(__name__)


class CompetitionLandscapeEvaluator(BaseCriterionEvaluator):
    criterion_id = "competition_landscape"
    criterion_name = "竞争格局"
    index = 3

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Industry cycle report (has concentration, margin data)
        try:
            report = ds.get_industry_cycle_report(symbol)
            raw["industry_cycle"] = {
                "concentration_cr5": report.get("concentration_cr5") or report.get("cr5"),
                "concentration_hhi": report.get("hhi"),
                "gross_margin_trend": report.get("gross_margin_trend") or report.get("margin_trend"),
                "competition_intensity": report.get("competition_intensity"),
                "price_war_signals": report.get("price_war_signals"),
                "peer_comparison": report.get("peer_comparison"),
            }
        except Exception as exc:
            logger.warning("[competition] industry_cycle failed: %s", exc)
            raw["industry_cycle_error"] = str(exc)

        # Valuation (for margin data)
        try:
            valuation = ds.get_valuation_ratios(symbol)
            raw["valuation"] = {
                "gross_margin": valuation.get("gross_margin"),
                "net_margin": valuation.get("net_margin"),
                "industry_average": valuation.get("industry_average", {}),
            }
        except Exception as exc:
            logger.warning("[competition] valuation failed: %s", exc)

        # Build summary
        parts = []
        ic = raw.get("industry_cycle", {})
        if ic.get("concentration_cr5") is not None:
            parts.append(f"行业CR5：{ic['concentration_cr5']}%")
        if ic.get("gross_margin_trend"):
            parts.append(f"毛利率趋势：{ic['gross_margin_trend']}")
        if ic.get("competition_intensity"):
            parts.append(f"竞争烈度：{ic['competition_intensity']}")
        if ic.get("price_war_signals"):
            parts.append(f"价格战信号：{ic['price_war_signals']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return COMPETITION_LANDSCAPE
