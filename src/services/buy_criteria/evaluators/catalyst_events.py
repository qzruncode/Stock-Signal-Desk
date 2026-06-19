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

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Extract catalyst events from announcements (risk events endpoint aggregates announcements)
        try:
            announcements = ds.get_risk_events(symbol, days=90)
            ann_items = _list_of_dicts(announcements.get("items"))[:10]
            catalyst_keywords = [
                "发布", "召开", "投产", "量产", "签约", "中标",
                "股东大会", "财报", "业绩", "扩产", "投产仪式",
            ]
            catalyst_items = [
                {
                    "title": a.get("title", ""),
                    "date": a.get("publish_time"),
                    "label": a.get("event_label"),
                    "severity": a.get("severity"),
                }
                for a in ann_items
                if any(kw in (a.get("title") or "") for kw in catalyst_keywords)
            ]
            raw["announcement_catalysts"] = {
                "items": catalyst_items[:5],
                "count": len(catalyst_items),
            }
        except Exception as exc:
            logger.warning("[catalyst] announcements failed: %s", exc)
            raw["announcement_catalysts"] = {"items": [], "count": 0, "error": str(exc)}

        # Extract catalyst clues from news
        try:
            news = ds.search_news(symbol, days=180)
            news_items = _list_of_dicts(news.get("items"))[:20]
            news_catalyst_keywords = [
                "展会", "峰会", "发布会", "论坛", "大会", "投产",
                "量产", "签约", "中标", "战略合作", "订单", "招标",
            ]
            news_catalyst_items = [
                {
                    "title": n.get("title", ""),
                    "source": n.get("source"),
                    "time": n.get("publish_time"),
                    "summary": (n.get("summary") or "")[:150],
                }
                for n in news_items
                if any(kw in (n.get("title") or "") for kw in news_catalyst_keywords)
            ]
            raw["news_catalysts"] = {
                "items": news_catalyst_items[:8],
                "count": len(news_catalyst_items),
            }
        except Exception as exc:
            logger.warning("[catalyst] news failed: %s", exc)
            raw["news_catalysts"] = {"items": [], "count": 0, "error": str(exc)}

        # Extract业绩拐点/技术迭代 catalyst clues from research reports
        try:
            research = ds.get_research_report(symbol, days=365)
            research_items = _list_of_dicts(research.get("items"))[:10]
            research_catalyst_keywords = [
                "拐点", "超预期", "量产", "突破", "新一代",
                "发布", "投产", "业绩", "目标价",
            ]
            research_catalyst_items = [
                {
                    "title": r.get("title", ""),
                    "org": r.get("org"),
                    "date": r.get("publish_date"),
                    "rating": r.get("rating"),
                    "summary": (r.get("summary") or "")[:150],
                }
                for r in research_items
                if any(
                    kw in ((r.get("title") or "") + (r.get("summary") or ""))
                    for kw in research_catalyst_keywords
                )
            ]
            raw["research_catalysts"] = {
                "items": research_catalyst_items[:5],
                "count": len(research_catalyst_items),
            }
        except Exception as exc:
            logger.warning("[catalyst] research failed: %s", exc)
            raw["research_catalysts"] = {"items": [], "count": 0, "error": str(exc)}

        # Build summary for LLM prompt
        lines = [
            "## 公告催化事件",
        ]
        ac = raw.get("announcement_catalysts", {})
        if ac.get("items"):
            for item in ac["items"][:5]:
                lines.append(
                    f"- [{item.get('date', '?')}] [{item.get('label', '?')}] {item.get('title', '')[:160]}"
                )
        else:
            lines.append("- 近90天公告中未找到催化事件")

        lines.extend(["", "## 新闻催化线索"])
        nc = raw.get("news_catalysts", {})
        if nc.get("items"):
            for item in nc["items"][:8]:
                lines.append(
                    f"- [{item.get('time', '?')}] {item.get('source', '?')}："
                    f"{item.get('title', '')[:140]}；{item.get('summary', '')}"
                )
        else:
            lines.append("- 近180天新闻中未找到催化线索")

        lines.extend(["", "## 研报催化线索"])
        rc = raw.get("research_catalysts", {})
        if rc.get("items"):
            for item in rc["items"][:5]:
                lines.append(
                    f"- [{item.get('date', '?')}] {item.get('org', '?')} [{item.get('rating', '?')}]"
                    f"：{item.get('title', '')[:120]}；{item.get('summary', '')}"
                )
        else:
            lines.append("- 研报中未找到催化线索")

        lines.extend([
            "",
            "## 判断约束",
            "- 关注未来 6-12 个月内可预见的催化事件（如已知展会、政策窗口、业绩拐点、技术迭代）。",
            "- 已完全消化的事件（利好出尽）不算有效催化。",
            "- 至少一个具体催化才判为通过。",
        ])
        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return CATALYST_EVENTS
