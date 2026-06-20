# src/services/buy_criteria/evaluators/valuation_level.py
"""Evaluator ⑦: 估值水位 — Is the valuation not透支 (overpriced)?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import VALUATION_LEVEL

logger = logging.getLogger(__name__)


class ValuationLevelEvaluator(BaseCriterionEvaluator):
    criterion_id = "valuation_level"
    criterion_name = "估值水位"
    index = 6

    def collect_data(
        self,
        symbol: str,
        stock_info: dict[str, Any],
        pre_fetched_data: dict[str, Any] | None = None,
    ) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Prefer injected valuation data from frontend; fall back to DataService
        if pre_fetched_data and "valuation" in pre_fetched_data:
            valuation = pre_fetched_data["valuation"]
        else:
            try:
                valuation = ds.get_valuation_ratios(symbol)
            except Exception as exc:
                logger.warning("[valuation] valuation_ratios failed: %s", exc)
                raw["valuation_error"] = str(exc)
                return CriterionEvidence(raw_data=raw, data_summary="数据获取失败")

        pe_pctiles = valuation.get("pe_percentiles") or {}
        raw["valuation"] = {
            "pe_ttm": valuation.get("pe_ttm"),
            "pb": valuation.get("pb"),
            "peg": valuation.get("peg"),
            # pe_percentiles is a dict {"5y": ..., "3y": ..., "1y": ...}; use 5y as primary
            "pe_percentile": pe_pctiles.get("5y"),
            "industry_average": valuation.get("industry_average"),
        }
        # Overdraft signal is already embedded in the valuation response
        od = valuation.get("price_overdraft_signal") or {}
        raw["overdraft"] = {
            "status": od.get("status"),
            "score": od.get("score"),
            "reasoning": od.get("reasoning"),
        }

        # Build summary
        parts = []
        v = raw.get("valuation", {})
        if v.get("pe_ttm") is not None:
            parts.append(f"PE(TTM): {v['pe_ttm']}")
        if v.get("pe_percentile") is not None:
            parts.append(f"PE历史分位：{v['pe_percentile']}%")
        if v.get("peg") is not None:
            parts.append(f"PEG: {v['peg']}")
        if v.get("industry_average"):
            parts.append(f"行业均值：PE {v['industry_average'].get('pe', '?')}, PB {v['industry_average'].get('pb', '?')}")
        od = raw.get("overdraft", {})
        if od.get("status"):
            parts.append(f"透支信号：{od['status']}（{od.get('reasoning', '')}）")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return VALUATION_LEVEL
