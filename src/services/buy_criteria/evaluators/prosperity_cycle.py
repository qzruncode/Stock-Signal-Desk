"""Evaluator ②: 景气上行周期 — Is the industry in an upward cycle?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
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

        # ---- Industry news: search by specific business/product keywords ----
        # Priority: industry-level supply-demand signals > company-specific news
        industry = stock_info.get("industry", "")
        main_business = stock_info.get("main_business", "")
        product_type = stock_info.get("product_type", "")
        # Clean keywords: strip punctuation, keep core terms
        def _clean_kw(text: str) -> list[str]:
            if not text:
                return []
            cleaned = text.strip().rstrip("。，、；：")
            parts = [p.strip().rstrip("。，、；：") for p in cleaned.replace("的研发", "").replace("的生产和销售", "").replace("生产和销售", "").replace("和", ",").split(",") if p.strip()]
            return [p for p in parts if len(p) >= 2]
        # Search order: core business/product terms (industry-level) first, then industry, then company name
        core_terms = _clean_kw(main_business) + _clean_kw(product_type)
        search_keywords = core_terms[:]
        if industry:
            search_keywords.append(industry)
        # Deduplicate
        seen = set()
        search_keywords = [k for k in search_keywords if not (k in seen or seen.add(k))]
        for keyword in search_keywords:
            try:
                industry_news = ds.search_industry_news(keyword, days=90, limit=10)
                news_items = _list_of_dicts(industry_news.get("items"))[:10]
                if news_items:
                    raw["industry_news"] = {
                        "keyword": keyword,
                        "items": news_items,
                    }
                    break  # Found relevant news with specific keyword
            except Exception as exc:
                logger.warning("[prosperity] news search '%s' failed: %s", keyword, exc)
        if "industry_news" not in raw:
            raw["industry_news"] = {"keyword": search_keywords[0] if search_keywords else "", "items": []}

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
        news_items = _list_of_dicts(_as_dict(raw.get("industry_news")).get("items"))
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
        lines.append("## 行业新闻/研报（近90天）")
        if news_items:
            for n in news_items:
                title = n.get("title", "")
                content = n.get("summary") or n.get("content") or n.get("event_label", "")
                tags = n.get("tags", [])
                pub_time = n.get("publish_time", "")
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                lines.append(f"- {_fmt_raw(title)}{tag_str}")
                if content:
                    lines.append(f"  内容: {_fmt_raw(content)}")
                if pub_time:
                    lines.append(f"  时间: {_fmt_raw(pub_time)}")
        else:
            lines.append(f"- 行业「{industry}」无相关新闻")
        lines.append("")

        # ---- PMI：只取最新值和趋势 ----
        lines.append("## 宏观PMI（辅助参考）")
        pmi_val = pmi_latest.get("value") or pmi_latest.get("current") or pmi_latest.get("pmi")
        lines.append(f"- 最新值: {_fmt_raw(pmi_val)}, 趋势: {_fmt_raw(pmi.get('trend'))}, 日期: {_fmt_raw(pmi.get('data_time'))}")
        lines.append("")

        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return PROSPERITY_CYCLE

    def evidence_failure_reason(self, evidence: CriterionEvidence) -> str | None:
        items = ((evidence.raw_data.get("financials") or {}).get("items") or [])
        if evidence.raw_data.get("financials_error") or len(items) < 3:
            return "连续财务期数不足或获取失败，无法验证细分行业景气是否持续上行"
        return None
