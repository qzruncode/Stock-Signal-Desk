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
    index = 6

    def collect_data(self, symbol: str, stock_info: dict[str, Any], pre_fetched_data: dict[str, Any] | None = None) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Risk events
        try:
            risk = ds.get_risk_events(symbol, days=730)
            raw["risk_events"] = {
                "items": risk.get("items", []),
                "semantic_status": "model_required",
                "coverage": risk.get("analysis", {}).get("coverage"),
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
            raw["shareholder_error"] = str(exc)

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
            raw["financial_signals_error"] = str(exc)

        # Build summary — inject actual risk event items so LLM can judge,
        # not just aggregate counts.
        lines: list[str] = []
        re_data = raw.get("risk_events", {})
        # --- Risk events (actual items) ---
        re_items = re_data.get("items") or []
        if re_items:
            lines.append("## 风险研判原始证据（近730天）")
            for item in re_items[:15]:
                title = (item.get("title") or "")[:200]
                summary = (item.get("summary") or "")[:200]
                date = item.get("date") or item.get("publish_time") or "?"
                source_type = item.get("source_type", "?")
                lines.append(
                    f"- [{date}] ({source_type}) {title}"
                    + (f"：{summary}" if summary else "")
                )
        elif raw.get("risk_events_error"):
            lines.append("## 风险事件\n- 风险事件数据获取失败，不能解释为近180天无风险")
        else:
            lines.append("## 风险证据\n- 近730天数据源没有返回可用的新闻或公告证据")

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
        lines.append(
            f"- {'数据获取失败，不能解释为无异常' if raw.get('shareholder_error') else ('；'.join(sh_parts) if sh_parts else '无异常信号')}"
        )

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
        lines.append(
            f"- {'数据获取失败，不能解释为无异常' if raw.get('financial_signals_error') else ('；'.join(fs_parts) if fs_parts else '无异常信号')}"
        )

        # --- Judgment constraints ---
        lines.extend([
            "",
            "## 判断约束",
            "- 逐条阅读标题、摘要和原文链接，自主识别可能影响投资成立条件的风险，不得依赖程序预分类。",
            "- 必须把同一事项按公告日期排序，用后续回复、审核决定、注册、发行结果、判决或终止公告覆盖早期阶段。",
            "- 不同年份、不同融资或交易方案必须按事项分别跟踪；不得把旧项目的发行完成当作新项目完成，也不得在已有后续审核进展时继续把早期问询写成当前状态。",
            "- 再融资只有出现证监会注册决定、发行情况报告或新增股份上市公告等相应正式文件时，才能写成已注册、已发行或已完成；交易所审核通过不等于发行完成。",
            "- 商誉异常、应收异常增长、现金流与利润严重背离属于财务异常信号。",
            "- 质押风险必须结合正式披露的累计比例、平仓风险、违约及减持证据研判，程序不设固定比例阈值。",
            "- 只有当实际数据中未发现上述致命风险，才可判为通过。",
            "- 风险事件、股东结构或财务异常信号任一关键来源获取失败时，不能证明无重大风险，必须判为不通过。",
        ])

        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return FATAL_RISKS

    def evidence_failure_reason(self, evidence: CriterionEvidence) -> str | None:
        raw = evidence.raw_data
        failed = []
        if raw.get("risk_events_error"):
            failed.append("风险事件")
        if raw.get("shareholder_error"):
            failed.append("股东结构")
        if raw.get("financial_signals_error"):
            failed.append("财务异常信号")
        if failed:
            return "、".join(failed) + "获取失败，不能据此证明不存在重大风险"
        return None
