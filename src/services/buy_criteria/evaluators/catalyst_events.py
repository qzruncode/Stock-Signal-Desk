# src/services/buy_criteria/evaluators/catalyst_events.py
"""Evaluator: Catalyst Events — Are there catalysts in the next 6-12 months?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import CATALYST_EVENTS

logger = logging.getLogger(__name__)


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


class CatalystEventsEvaluator(BaseCriterionEvaluator):
    criterion_id = "catalyst_events"
    criterion_name = "催化事件"
    index = 5

    def collect_data(self, symbol: str, stock_info: dict[str, Any], pre_fetched_data: dict[str, Any] | None = None) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Announcements — raw data for LLM to judge catalyst potential
        try:
            announcements = ds.get_risk_events(symbol, days=90)
            ann_items = _list_of_dicts(announcements.get("items"))[:10]
            raw["announcement_events"] = [
                {
                    "title": a.get("title", ""),
                    "date": a.get("date") or a.get("publish_time"),
                    "label": a.get("risk_label") or a.get("event_label"),
                    "severity": a.get("severity"),
                }
                for a in ann_items
            ]
        except Exception as exc:
            logger.warning("[catalyst] announcements failed: %s", exc)
            raw["announcement_events"] = []

        # News — raw data for LLM to judge catalyst clues
        try:
            news = ds.search_news(symbol, days=180)
            news_items = _list_of_dicts(news.get("items"))[:15]
            raw["news_events"] = [
                {
                    "title": n.get("title", ""),
                    "source": n.get("source"),
                    "time": n.get("publish_time"),
                    "summary": (n.get("summary") or "")[:200],
                }
                for n in news_items
            ]
        except Exception as exc:
            logger.warning("[catalyst] news failed: %s", exc)
            raw["news_events"] = []

        # Research reports — raw data for LLM to judge catalyst clues
        try:
            research = ds.get_research_report(symbol, days=365)
            research_items = _list_of_dicts(research.get("items"))[:10]
            raw["research_events"] = [
                {
                    "title": r.get("title", ""),
                    "org": r.get("org"),
                    "date": r.get("publish_date"),
                    "rating": r.get("rating"),
                    "summary": (r.get("summary") or "")[:200],
                }
                for r in research_items
            ]
        except Exception as exc:
            logger.warning("[catalyst] research failed: %s", exc)
            raw["research_events"] = []

        # Build summary for LLM prompt — show raw data
        lines = [
            "## 公告事件（请自行判断是否涉及可预见的催化事件，如业绩发布、投产、扩产等）",
        ]
        ae = raw.get("announcement_events", [])
        if ae:
            for item in ae[:8]:
                lines.append(
                    f"- [{item.get('date', '?')}] [{item.get('label', '?')}] {item.get('title', '')[:180]}"
                )
        else:
            lines.append("- 无公告数据")

        lines.extend(["", "## 新闻线索（请自行判断是否涉及展会、签约、战略合作、政策窗口等催化）"])
        ne = raw.get("news_events", [])
        if ne:
            for item in ne[:10]:
                lines.append(
                    f"- [{item.get('time', '?')}] {item.get('source', '?')}："
                    f"{item.get('title', '')[:160]}；{item.get('summary', '')}"
                )
        else:
            lines.append("- 无新闻数据")

        lines.extend(["", "## 研报表述（请自行判断是否涉及业绩拐点、技术迭代、产品发布等催化）"])
        re_ = raw.get("research_events", [])
        if re_:
            for item in re_[:8]:
                lines.append(
                    f"- [{item.get('date', '?')}] {item.get('org', '?')} [{item.get('rating', '?')}]"
                    f"：{item.get('title', '')[:140]}；{item.get('summary', '')}"
                )
        else:
            lines.append("- 无研报数据")

        lines.extend([
            "",
            "## 判断约束",
            "- 关注未来 6-12 个月内可预见的催化事件（如已知展会、政策窗口、业绩拐点、技术迭代、产品发布）。",
            "- 已完全消化的事件（利好出尽）不算有效催化。",
            "- 请基于公告/新闻/研报的实际内容判断，不要因为标题不含关键词就忽略催化信号。",
            "- 至少一个具体催化才判为通过。",
        ])
        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return CATALYST_EVENTS
