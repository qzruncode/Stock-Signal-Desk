# src/services/buy_criteria/evaluators/competition_landscape.py
"""Evaluator ④: 竞争格局 — Is the industry free from price wars/involution?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import COMPETITION_LANDSCAPE

logger = logging.getLogger(__name__)

PRICE_WAR_KEYWORDS = [
    "降价", "价格战", "促销", "内卷", "毛利率下滑", "降价促销",
    "恶性竞争", "价格下探", "让利", "价格竞争",
]


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    """Ensure value is a list of dicts."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


class CompetitionLandscapeEvaluator(BaseCriterionEvaluator):
    criterion_id = "competition_landscape"
    criterion_name = "竞争格局"
    index = 3

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Margin data — current vs industry average
        try:
            valuation = ds.get_valuation_ratios(symbol)
            industry_avg = valuation.get("industry_average") or {}
            raw["margin_data"] = {
                "gross_margin": valuation.get("gross_margin"),
                "net_margin": valuation.get("net_margin"),
                "industry_avg_gross_margin": industry_avg.get("gross_margin"),
                "industry_avg_net_margin": industry_avg.get("net_margin"),
            }
        except Exception as exc:
            logger.warning("[competition] valuation failed: %s", exc)

        # Margin trend — last 4 quarters from financials
        try:
            financials = ds.get_financials(symbol, periods=4, force=True)
            items = _list_of_dicts(financials.get("items"))[:4]
            raw["margin_trend"] = {
                "items": [
                    {"date": item.get("report_date"), "gross_margin": item.get("gross_margin")}
                    for item in items
                ],
            }
        except Exception as exc:
            logger.warning("[competition] financials failed: %s", exc)

        # Sector ranking — industry concentration signals
        try:
            sectors = ds.get_sector_list("industry")
            raw["sector_ranking"] = {
                "items": [
                    {"name": s["name"], "rank": i + 1, "change_pct": s.get("change_pct")}
                    for i, s in enumerate(_list_of_dicts(sectors.get("items"))[:30])
                ],
            }
        except Exception as exc:
            logger.warning("[competition] sectors failed: %s", exc)

        # Price war signals — news keyword search from last 180 days
        try:
            news = ds.search_news(symbol, days=180)
            news_items = _list_of_dicts(news.get("items"))[:20]
            price_war_items = [
                {"title": n.get("title"), "source": n.get("source"), "time": n.get("publish_time")}
                for n in news_items
                if any(
                    kw in (n.get("title") or "") + (n.get("summary") or "")
                    for kw in PRICE_WAR_KEYWORDS
                )
            ]
            raw["price_war_signals"] = {
                "items": price_war_items[:5],
                "count": len(price_war_items),
            }
        except Exception as exc:
            logger.warning("[competition] price war search failed: %s", exc)

        # Build summary
        lines = ["## 毛利率数据"]
        md = raw.get("margin_data", {})
        if md.get("gross_margin") is not None:
            lines.append(f"- 当前毛利率：{md['gross_margin']}%")
        if md.get("industry_avg_gross_margin") is not None:
            lines.append(f"- 行业平均毛利率：{md['industry_avg_gross_margin']}%")

        mt = raw.get("margin_trend", {})
        if mt.get("items"):
            trend_parts = []
            for item in mt["items"]:
                gm = item.get("gross_margin")
                val = f"{gm}%" if gm is not None else "?"
                trend_parts.append(val)
            lines.append(f"- 最近4季度毛利率趋势：{' → '.join(trend_parts)}")

        lines.extend(["", "## 行业板块竞争格局"])
        sr = raw.get("sector_ranking", {})
        if sr.get("items"):
            lines.append(f"- 板块共{len(sr['items'])}个行业参与排名")
        else:
            lines.append("- 板块排名数据缺失")

        lines.extend(["", "## 价格战信号"])
        pw = raw.get("price_war_signals", {})
        if pw.get("count", 0) > 0:
            lines.append(f"- 发现{pw['count']}条价格战/内卷相关报道：")
            for item in pw.get("items", [])[:5]:
                title = (item.get("title") or "")[:160]
                lines.append(f"  - [{item.get('time', '?')}] {item.get('source', '?')}：{title}")
        else:
            lines.append("- 近6个月未发现明显价格战/内卷信号")

        lines.extend([
            "",
            "## 判断约束",
            "- 毛利率连续下滑 + 价格战信号 = 内卷风险高。",
            "- 毛利率稳定/提升 + 无明显价格战信号 = 竞争格局健康。",
        ])

        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return COMPETITION_LANDSCAPE
