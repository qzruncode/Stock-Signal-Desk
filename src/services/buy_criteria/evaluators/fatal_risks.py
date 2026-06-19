# src/services/buy_criteria/evaluators/fatal_risks.py
"""Evaluator ⑧: 致命风险 — No unexploded fatal risks?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import FATAL_RISKS

logger = logging.getLogger(__name__)


class FatalRisksEvaluator(BaseCriterionEvaluator):
    criterion_id = "fatal_risks"
    criterion_name = "致命风险"
    index = 7

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Risk events
        try:
            risk = ds.get_risk_events(symbol, days=180)
            raw["risk_events"] = {
                "items": risk.get("items", []),
                "severity": risk.get("analysis", {}).get("severity_distribution"),
                "top_labels": risk.get("analysis", {}).get("top_risk_labels"),
            }
        except Exception as exc:
            logger.warning("[fatal_risks] risk_events failed: %s", exc)
            raw["risk_events_error"] = str(exc)

        # Shareholder structure (pledge, reduction signals)
        try:
            shareholder = ds.get_shareholder_structure(symbol)
            raw["shareholder"] = {
                "pledge_ratio": shareholder.get("pledge_ratio") or shareholder.get("total_pledge_ratio"),
                "top_holder_changes": shareholder.get("top_holder_changes") or shareholder.get("changes"),
                "reduction_signals": shareholder.get("reduction_signals"),
            }
        except Exception as exc:
            logger.warning("[fatal_risks] shareholder failed: %s", exc)

        # Valuation (for financial anomaly signals)
        try:
            valuation = ds.get_valuation_ratios(symbol)
            raw["financial_signals"] = {
                "goodwill": valuation.get("goodwill"),
                "receivables_ratio": valuation.get("receivables_ratio"),
                "cashflow_to_profit": valuation.get("cashflow_to_profit"),
            }
        except Exception as exc:
            logger.warning("[fatal_risks] valuation failed: %s", exc)

        # Build summary
        parts = []
        re_data = raw.get("risk_events", {})
        severity = re_data.get("severity", {})
        if severity:
            parts.append(f"风险事件分布：高危{severity.get('high', 0)}个，中危{severity.get('medium', 0)}个")
        top_labels = re_data.get("top_labels")
        if top_labels:
            parts.append(f"主要风险类型：{', '.join(top_labels[:3])}")
        sh = raw.get("shareholder", {})
        if sh.get("pledge_ratio") is not None:
            parts.append(f"大股东质押比例：{sh['pledge_ratio']}%")
        if sh.get("reduction_signals"):
            parts.append(f"减持信号：{sh['reduction_signals']}")
        fs = raw.get("financial_signals", {})
        if fs.get("goodwill") is not None:
            parts.append(f"商誉：{fs['goodwill']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return FATAL_RISKS
