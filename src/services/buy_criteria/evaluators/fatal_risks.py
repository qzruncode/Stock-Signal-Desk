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

    def collect_data(self, symbol: str, stock_info: dict[str, Any], pre_fetched_data: dict[str, Any] | None = None) -> CriterionEvidence:
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

        # Build summary — inject actual risk event items so LLM can judge,
        # not just aggregate counts.
        lines: list[str] = []
        re_data = raw.get("risk_events", {})
        severity = re_data.get("severity", {})

        # --- Risk events (actual items) ---
        re_items = re_data.get("items") or []
        if re_items:
            lines.append("## 风险事件（近180天）")
            for item in re_items[:15]:
                sev = item.get("severity", "?")
                label = item.get("risk_label") or item.get("event_label") or "?"
                summary = (item.get("risk_summary") or item.get("summary") or "")[:200]
                date = item.get("date") or item.get("publish_time") or "?"
                source_type = item.get("source_type", "?")
                lines.append(
                    f"- [{date}] [{sev}] [{label}] ({source_type})"
                    f"：{summary}"
                )
        else:
            lines.append("## 风险事件\n- 近180天无已识别的风险事件")

        # --- Severity overview ---
        if severity:
            lines.extend([
                "",
                "## 风险统计",
                f"- 高危：{severity.get('high', 0)} 条；中危：{severity.get('medium', 0)} 条",
            ])

        # --- Shareholder signals ---
        sh = raw.get("shareholder", {})
        lines.extend(["", "## 股东信号"])
        sh_parts = []
        if sh.get("pledge_ratio") is not None:
            sh_parts.append(f"大股东质押比例：{sh['pledge_ratio']}%")
        reduction = sh.get("reduction_signals")
        if reduction:
            sh_parts.append(f"减持信号：{reduction}")
        holder_changes = sh.get("top_holder_changes")
        if holder_changes:
            sh_parts.append(f"股东变化：{holder_changes}")
        lines.append(f"- {'；'.join(sh_parts) if sh_parts else '无异常信号'}")

        # --- Financial anomaly signals ---
        fs = raw.get("financial_signals", {})
        lines.extend(["", "## 财务异常信号"])
        fs_parts = []
        if fs.get("goodwill") is not None:
            fs_parts.append(f"商誉：{fs['goodwill']}")
        if fs.get("receivables_ratio") is not None:
            fs_parts.append(f"应收占比：{fs['receivables_ratio']}")
        if fs.get("cashflow_to_profit") is not None:
            fs_parts.append(f"现金流/利润比：{fs['cashflow_to_profit']}")
        lines.append(f"- {'；'.join(fs_parts) if fs_parts else '无异常信号'}")

        # --- Judgment constraints ---
        lines.extend([
            "",
            "## 判断约束",
            "- 重点关注高危风险事件的 risk_summary 内容，判断是否触及「财务造假」「重大监管变化」「大股东高比例质押+减持」「核心技术颠覆」「重大诉讼/违规」等致命风险标准。",
            "- 商誉异常、应收异常增长、现金流与利润严重背离属于财务异常信号。",
            "- 质押比例超过50%且伴随减持信号才构成致命风险。",
            "- 只有当实际数据中未发现上述致命风险，才可判为通过。",
        ])

        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return FATAL_RISKS
