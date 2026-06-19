"""Evaluator ②: 景气上行周期 — Is the industry in an upward cycle?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import PROSPERITY_CYCLE

logger = logging.getLogger(__name__)


class ProsperityCycleEvaluator(BaseCriterionEvaluator):
    criterion_id = "prosperity_cycle"
    criterion_name = "景气上行周期"
    index = 1

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Industry cycle report (has prosperity score, cycle phase)
        try:
            report = ds.get_industry_cycle_report(symbol)
            raw["industry_cycle"] = {
                "prosperity_score": report.get("prosperity_score"),
                "cycle_phase": report.get("cycle_phase"),
                "core_logic": report.get("core_logic"),
                "analysis_status": report.get("analysis_status"),
            }
        except Exception as exc:
            logger.warning("[prosperity] industry_cycle failed: %s", exc)
            raw["industry_cycle_error"] = str(exc)

        # Valuation ratios (has revenue trend data)
        try:
            valuation = ds.get_valuation_ratios(symbol)
            raw["valuation"] = {
                "pe_ttm": valuation.get("pe_ttm"),
                "pb": valuation.get("pb"),
                "industry_average": valuation.get("industry_average"),
                "revenue_growth": valuation.get("revenue_growth") or valuation.get("revenue_yoy"),
            }
        except Exception as exc:
            logger.warning("[prosperity] valuation failed: %s", exc)

        # Build summary
        parts = []
        ic = raw.get("industry_cycle", {})
        if ic.get("prosperity_score") is not None:
            parts.append(f"行业景气度评分{ic['prosperity_score']}分")
        if ic.get("cycle_phase"):
            parts.append(f"周期阶段：{ic['cycle_phase']}")
        if ic.get("core_logic"):
            parts.append(f"核心逻辑：{ic['core_logic']}")
        v = raw.get("valuation", {})
        if v.get("revenue_growth") is not None:
            parts.append(f"营收增速：{v['revenue_growth']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return PROSPERITY_CYCLE
