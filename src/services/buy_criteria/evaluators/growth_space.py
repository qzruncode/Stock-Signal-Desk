# src/services/buy_criteria/evaluators/growth_space.py
"""Evaluator ③: 未来3年空间 — Is there clear 3-year growth space (not zero-sum)?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import GROWTH_SPACE

logger = logging.getLogger(__name__)


class GrowthSpaceEvaluator(BaseCriterionEvaluator):
    criterion_id = "growth_space"
    criterion_name = "未来3年空间"
    index = 2

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Industry cycle report (has market size, penetration, CAGR data)
        try:
            report = ds.get_industry_cycle_report(symbol)
            raw["industry_cycle"] = {
                "market_size_forecast": report.get("market_size_forecast") or report.get("market_size"),
                "penetration_rate": report.get("penetration_rate") or report.get("penetration"),
                "cagr_forecast": report.get("cagr") or report.get("cagr_forecast"),
                "growth_drivers": report.get("growth_drivers"),
                "new_demand_sources": report.get("new_demand_sources"),
                "core_logic": report.get("core_logic"),
            }
        except Exception as exc:
            logger.warning("[growth_space] industry_cycle failed: %s", exc)
            raw["industry_cycle_error"] = str(exc)

        # Stock info (for industry context)
        raw["stock_info"] = {
            "industry": stock_info.get("industry", ""),
            "main_business": stock_info.get("main_business", ""),
        }

        # Build summary
        parts = []
        ic = raw.get("industry_cycle", {})
        if ic.get("cagr_forecast"):
            parts.append(f"行业CAGR预测：{ic['cagr_forecast']}")
        if ic.get("penetration_rate") is not None:
            parts.append(f"当前渗透率：{ic['penetration_rate']}")
        if ic.get("market_size_forecast"):
            parts.append(f"市场规模预测：{ic['market_size_forecast']}")
        if ic.get("new_demand_sources"):
            parts.append(f"新增需求来源：{ic['new_demand_sources']}")
        if ic.get("core_logic"):
            parts.append(f"核心逻辑：{ic['core_logic']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return GROWTH_SPACE
