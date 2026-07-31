# src/services/buy_criteria/evaluators/growth_drivers.py
"""Evaluator ⑤: 驱动因素 — Does the industry have policy/tech/demand drivers?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import GROWTH_DRIVERS

logger = logging.getLogger(__name__)


def _list_of_dicts(value: Any) -> list[dict]:
    """Ensure value is a list of dicts."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


class GrowthDriversEvaluator(BaseCriterionEvaluator):
    criterion_id = "growth_drivers"
    criterion_name = "驱动因素"
    index = 4

    def collect_data(
        self, symbol: str, stock_info: dict[str, Any], pre_fetched_data: dict[str, Any] | None = None
    ) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Fetch news once — reuse for both policy and demand evidence
        news_items: list[dict] = []
        try:
            news = ds.search_news(symbol, days=180)
            news_items = _list_of_dicts(news.get("items"))[:15]
        except Exception as exc:
            logger.warning("[drivers] news failed: %s", exc)

        # Policy drivers — raw news for LLM to judge policy relevance
        raw["policy_news"] = [
            {
                "title": n.get("title"),
                "summary": (n.get("summary") or "")[:200],
                "source": n.get("source"),
                "time": n.get("publish_time"),
            }
            for n in news_items
        ]

        # Tech drivers — raw research reports for LLM to judge tech relevance
        try:
            research = ds.get_research_report(symbol, days=365)
            research_items = _list_of_dicts(research.get("items"))[:10]
            raw["tech_research"] = [
                {
                    "title": r.get("title"),
                    "org": r.get("org"),
                    "date": r.get("publish_date"),
                    "summary": (r.get("summary") or "")[:300],
                }
                for r in research_items
            ]
        except Exception as exc:
            logger.warning("[drivers] tech research failed: %s", exc)

        # Demand drivers — same news items as policy
        raw["demand_news"] = [
            {
                "title": n.get("title"),
                "summary": (n.get("summary") or "")[:200],
                "source": n.get("source"),
                "time": n.get("publish_time"),
            }
            for n in news_items
        ]

        # Build summary — show raw data for LLM to judge
        lines = [
            "## 政策驱动证据（近6个月新闻，请自行判断是否涉及国家级/部委级产业政策）",
        ]
        pn = raw.get("policy_news", [])
        if pn:
            for item in pn[:10]:
                lines.append(
                    f"- [{item.get('time', '?')}] {item.get('source', '?')}：{item.get('title', '')[:160]}；{item.get('summary', '')}"
                )
        else:
            lines.append("- 无新闻数据")
        lines.extend(
            [
                "",
                "## 技术驱动证据（近1年研报，请自行判断是否涉及技术突破/迭代）",
            ]
        )
        tr = raw.get("tech_research", [])
        if tr:
            for item in tr[:8]:
                lines.append(
                    f"- [{item.get('date', '?')}] {item.get('org', '?')}：{item.get('title', '')[:140]}；{item.get('summary', '')}"
                )
        else:
            lines.append("- 无研报数据")
        lines.extend(
            [
                "",
                "## 需求驱动证据（近6个月新闻，请自行判断是否涉及订单/出货/需求增长）",
            ]
        )
        dn = raw.get("demand_news", [])
        if dn:
            for item in dn[:10]:
                lines.append(
                    f"- [{item.get('time', '?')}] {item.get('source', '?')}：{item.get('title', '')[:160]}；{item.get('summary', '')}"
                )
        else:
            lines.append("- 无新闻数据")
        lines.extend(
            [
                "",
                "## 判断约束",
                "- 请基于上方原始新闻和研报内容，自行判断是否存在政策/技术/需求驱动。",
                "- 不要因为新闻标题不含关键词就忽略实际内容中的驱动信号。",
                "- 三类驱动至少有一种明确成立才判为通过。",
                "- 纯概念炒作（有题材但无实质政策/技术/需求落地）不算驱动成立。",
            ]
        )
        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return GROWTH_DRIVERS
