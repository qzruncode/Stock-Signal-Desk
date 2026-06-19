# src/services/buy_criteria/evaluators/growth_drivers.py
"""Evaluator ⑤: 驱动因素 — Does the industry have policy/tech/demand drivers?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import GROWTH_DRIVERS

logger = logging.getLogger(__name__)

POLICY_KEYWORDS = [
    "政策", "规划", "部委", "发改委", "工信部", "国务院", "中央",
    "补贴", "支持", "指导意见", "行动方案", "十四五", "专项",
]

TECH_KEYWORDS = [
    "技术突破", "技术迭代", "新一代", "量产", "商用", "升级", "创新",
]

DEMAND_KEYWORDS = [
    "订单", "出货", "装机", "销量", "需求", "产能", "扩产", "供不应求",
]


def _list_of_dicts(value: Any) -> list[dict]:
    """Ensure value is a list of dicts."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


class GrowthDriversEvaluator(BaseCriterionEvaluator):
    criterion_id = "growth_drivers"
    criterion_name = "驱动因素"
    index = 4

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Policy drivers — search news from the last 6 months for policy keywords
        try:
            news = ds.search_news(symbol, days=180)
            news_items = _list_of_dicts(news.get("items"))[:20]
            policy_items = [
                {
                    "title": n.get("title"),
                    "source": n.get("source"),
                    "time": n.get("publish_time"),
                }
                for n in news_items
                if any(
                    kw in (n.get("title") or "") + (n.get("summary") or "")
                    for kw in POLICY_KEYWORDS
                )
            ]
            raw["policy_evidence"] = {
                "items": policy_items[:5],
                "count": len(policy_items),
            }
        except Exception as exc:
            logger.warning("[drivers] news failed: %s", exc)

        # Tech drivers — extract tech-related descriptions from research reports
        try:
            research = ds.get_research_report(symbol, days=365)
            research_items = _list_of_dicts(research.get("items"))[:10]
            tech_items = [
                {
                    "title": r.get("title"),
                    "org": r.get("org"),
                    "date": r.get("publish_date"),
                    "summary": (r.get("summary") or "")[:200],
                }
                for r in research_items
                if any(
                    kw in (r.get("title") or "") + (r.get("summary") or "")
                    for kw in TECH_KEYWORDS
                )
            ]
            raw["tech_evidence"] = {
                "items": tech_items[:5],
                "count": len(tech_items),
            }
        except Exception as exc:
            logger.warning("[drivers] research failed: %s", exc)

        # Demand drivers — extract demand/order clues from news
        try:
            demand_items = [
                {
                    "title": n.get("title"),
                    "source": n.get("source"),
                    "time": n.get("publish_time"),
                }
                for n in news_items
                if any(
                    kw in (n.get("title") or "") + (n.get("summary") or "")
                    for kw in DEMAND_KEYWORDS
                )
            ]
            raw["demand_evidence"] = {
                "items": demand_items[:5],
                "count": len(demand_items),
            }
        except Exception as exc:
            logger.warning("[drivers] demand extraction failed: %s", exc)

        # Build summary
        lines = [
            "## 政策驱动证据",
        ]
        pe = raw.get("policy_evidence", {})
        if pe.get("items"):
            for item in pe["items"][:5]:
                lines.append(
                    f"- [{item.get('time', '?')}] {item.get('source', '?')}：{item.get('title', '')[:180]}"
                )
        else:
            lines.append("- 近6个月新闻中未发现政策相关报道")
        lines.extend([
            "",
            "## 技术驱动证据",
        ])
        te = raw.get("tech_evidence", {})
        if te.get("items"):
            for item in te["items"][:5]:
                lines.append(
                    f"- [{item.get('date', '?')}] {item.get('org', '?')}：{item.get('title', '')[:120]}；{item.get('summary', '')}"
                )
        else:
            lines.append("- 研报中未发现技术突破相关描述")
        lines.extend([
            "",
            "## 需求驱动证据",
        ])
        de = raw.get("demand_evidence", {})
        if de.get("items"):
            for item in de["items"][:5]:
                lines.append(
                    f"- [{item.get('time', '?')}] {item.get('source', '?')}：{item.get('title', '')[:180]}"
                )
        else:
            lines.append("- 新闻中未发现需求/订单增长线索")
        lines.extend([
            "",
            "## 判断约束",
            "- 仅基于上方实际证据判断，不得编造政策/技术/需求线索。",
            "- 如果某一类驱动力证据充足（有具体政策文件/技术突破报道/订单增长数据），则该类驱动成立。",
            "- 三类驱动至少有一种明确成立才判为通过。",
        ])
        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return GROWTH_DRIVERS
