"""Evaluator ②: 景气上行周期 — Is the industry in an upward cycle?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.evidence_queries import structured_thesis_queries
from src.services.buy_criteria.prompts.rubrics import PROSPERITY_CYCLE

logger = logging.getLogger(__name__)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


FIELD_LABELS = {
    "report_date": "报告期",
    "revenue_yoy": "营收同比",
    "revenue_qoq": "营收环比",
    "gross_margin": "毛利率",
    "net_profit_yoy": "净利润同比",
}


def _fmt_raw(value: Any) -> str:
    """Return raw value as string, or 'null' if None/empty. No calculation, no formatting."""
    if value is None:
        return "null"
    return str(value)


class ProsperityCycleEvaluator(BaseCriterionEvaluator):
    criterion_id = "prosperity_cycle"
    criterion_name = "景气上行周期"
    index = 3

    def collect_data(self, symbol: str, stock_info: dict[str, Any], pre_fetched_data: dict[str, Any] | None = None) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        raw["stock_profile"] = {
            "symbol": stock_info.get("symbol") or symbol,
            "name": stock_info.get("name") or stock_info.get("short_name"),
            "industry": stock_info.get("industry"),
            "main_business": stock_info.get("main_business"),
            "product_type": stock_info.get("product_type"),
            "product_name": stock_info.get("product_name"),
        }

        # ---- Company financials: 8 periods for cycle trend analysis ----
        try:
            financials = ds.get_financials(symbol, periods=8, force=True)
            raw["financials"] = {
                "items": _list_of_dicts(financials.get("items"))[:8],
                "_cached": financials.get("_cached"),
            }
        except Exception as exc:
            logger.warning("[prosperity] financials failed: %s", exc)
            raw["financials_error"] = str(exc)

        # ---- Industry evidence: preserve the planner's structured domains ----
        industry = stock_info.get("industry", "")
        search_queries = structured_thesis_queries(stock_info, limit=4)
        industry_evidence: list[dict[str, Any]] = []
        search_errors: list[str] = []
        for query in search_queries:
            try:
                result = ds.search_industry_news(query, days=180, limit=8)
                for item in _list_of_dicts(result.get("items"))[:8]:
                    industry_evidence.append({**item, "query": query})
            except Exception as exc:
                logger.warning("[prosperity] news search '%s' failed: %s", query, exc)
                search_errors.append(f"{query}: {exc}")
        raw["industry_evidence"] = {
            "queries": search_queries,
            "items": industry_evidence[:24],
            "errors": search_errors,
        }

        # ---- Macro PMI — demoted to economic context ----
        try:
            pmi = ds.get_macro_indicator("PMI", months=6)
            raw["macro_pmi"] = pmi
        except Exception as exc:
            logger.warning("[prosperity] PMI failed: %s", exc)
            raw["macro_pmi_error"] = str(exc)

        # ---- Build summary: only fields the rubric needs, no noise ----
        profile = raw["stock_profile"]
        fin_items = _list_of_dicts(_as_dict(raw.get("financials")).get("items"))
        news_payload = _as_dict(raw.get("industry_evidence"))
        news_items = _list_of_dicts(news_payload.get("items"))
        pmi = _as_dict(raw.get("macro_pmi"))
        pmi_latest = _as_dict(pmi.get("latest"))

        lines = [
            "## 股票信息",
            f"股票: {_fmt_raw(profile.get('name') or symbol)} ({_fmt_raw(profile.get('symbol') or symbol)})",
            f"行业: {_fmt_raw(profile.get('industry'))}",
            f"主营业务: {_fmt_raw(profile.get('main_business'))}",
            f"产品: {_fmt_raw(profile.get('product_type'))} / {_fmt_raw(profile.get('product_name'))}",
            "",
        ]

        # ---- 财务数据：只取 rubric 判断需要的字段 ----
        FIN_FIELDS = ["report_date", "revenue_yoy", "revenue_qoq", "gross_margin", "net_profit_yoy"]
        lines.append("## 财务数据（最近8个季度）")
        if fin_items:
            for item in fin_items:
                kvs = ", ".join(
                    f"{FIELD_LABELS.get(k, k)}: {_fmt_raw(item.get(k))}" for k in FIN_FIELDS
                )
                lines.append(f"- {kvs}")
        else:
            lines.append("- null")
        lines.append("")

        # ---- 新闻数据：只取标题、内容、时间、标签 ----
        lines.append("## 细分产业供需证据（近180天）")
        lines.append(f"- 结构化查询方向：{'、'.join(news_payload.get('queries') or []) or '缺失'}")
        if news_items:
            for n in news_items:
                title = n.get("title", "")
                content = n.get("summary") or n.get("content") or n.get("event_label", "")
                tags = n.get("tags", [])
                pub_time = n.get("publish_time", "")
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                lines.append(f"- [{_fmt_raw(n.get('query'))}] {_fmt_raw(title)}{tag_str}")
                if content:
                    lines.append(f"  内容: {_fmt_raw(content)}")
                if pub_time:
                    lines.append(f"  时间: {_fmt_raw(pub_time)}")
        else:
            lines.append(f"- 未取得与本轮结构化方向相符的供需证据；退化行业={industry or '缺失'}")
        lines.append("")

        # ---- PMI：只取最新值和趋势 ----
        lines.append("## 宏观PMI（辅助参考）")
        pmi_val = pmi_latest.get("value") or pmi_latest.get("current") or pmi_latest.get("pmi")
        lines.append(f"- 最新值: {_fmt_raw(pmi_val)}, 趋势: {_fmt_raw(pmi.get('trend'))}, 日期: {_fmt_raw(pmi.get('data_time'))}")
        lines.extend([
            "",
            "## 判断约束",
            "- 只使用本轮结构化产业方向检索结果；不得把法定大行业、无关公司的供需信息套到真实细分业务。",
            "- 公司总营收可用于验证兑现，但新业务尚未形成单独报表时，不能仅因公司总营收受旧业务拖累就否定细分行业景气。",
            "- 股价、板块资金和宏观PMI都只是辅助信息，不能代替订单、出货、价格、库存、产能利用率或资本开支等供需证据。",
        ])
        lines.append("")

        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return PROSPERITY_CYCLE

    def evidence_failure_reason(self, evidence: CriterionEvidence) -> str | None:
        items = ((evidence.raw_data.get("financials") or {}).get("items") or [])
        industry_items = ((evidence.raw_data.get("industry_evidence") or {}).get("items") or [])
        if len(items) < 3 and not industry_items:
            return "连续财务与细分产业供需证据均不足，无法验证景气趋势"
        return None
