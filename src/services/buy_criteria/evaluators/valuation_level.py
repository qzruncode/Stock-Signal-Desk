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

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Valuation ratios (PE, PB, percentiles, PEG)
        try:
            valuation = ds.get_valuation_ratios(symbol)
            raw["valuation"] = {
                "pe_ttm": valuation.get("pe_ttm"),
                "pb": valuation.get("pb"),
                "peg": valuation.get("peg"),
                "pe_percentile": valuation.get("pe_percentile") or valuation.get("pe_history_percentile"),
                "pb_percentile": valuation.get("pb_percentile") or valuation.get("pb_history_percentile"),
                "industry_average": valuation.get("industry_average"),
                "valuation_status": valuation.get("valuation_status"),
            }
        except Exception as exc:
            logger.warning("[valuation] valuation_ratios failed: %s", exc)
            raw["valuation_error"] = str(exc)

        # Price overdraft signal
        try:
            overdraft = ds.get_price_overdraft_signal(symbol)
            raw["overdraft"] = {
                "status": overdraft.get("status"),
                "score": overdraft.get("score"),
                "reasoning": overdraft.get("reasoning"),
            }
        except Exception as exc:
            logger.warning("[valuation] overdraft failed: %s", exc)

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
