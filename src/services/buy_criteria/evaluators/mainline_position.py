"""Evaluator ①: 市场主线属性 — Is the stock's industry a market mainline?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import MAINLINE_POSITION

logger = logging.getLogger(__name__)


class MainlinePositionEvaluator(BaseCriterionEvaluator):
    criterion_id = "mainline_position"
    criterion_name = "市场主线属性"
    index = 0

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Sector fund flow ranking
        try:
            sectors = ds.get_sector_list("industry")
            raw["sector_list"] = {
                "top_10": [
                    {"name": s["name"], "change_pct": s.get("change_pct"), "rank": s.get("rank"), "total_amount": s.get("total_amount")}
                    for s in (sectors.get("items") or [])[:10]
                ],
                "data_time": sectors.get("data_time"),
            }
            # Find this stock's industry in the ranking
            industry = stock_info.get("industry", "")
            for s in (sectors.get("items") or []):
                if industry and s.get("name") == industry:
                    raw["target_industry_rank"] = {"name": s["name"], "rank": s.get("rank"), "change_pct": s.get("change_pct"), "total_amount": s.get("total_amount")}
                    break
        except Exception as exc:
            logger.warning("[mainline] sector_list failed: %s", exc)
            raw["sector_list_error"] = str(exc)

        # Sentiment
        try:
            sentiment = ds.get_sentiment(symbol)
            raw["sentiment"] = {
                "score": sentiment.get("sentiment_score"),
                "total_discussion": sentiment.get("total_discussion"),
            }
        except Exception as exc:
            logger.warning("[mainline] sentiment failed: %s", exc)
            raw["sentiment_error"] = str(exc)

        # Social sentiment
        try:
            social = ds.get_social_sentiment(symbol)
            raw["social_sentiment"] = {
                "score": social.get("score") or social.get("social_score"),
                "trend": social.get("trend"),
            }
        except Exception as exc:
            logger.warning("[mainline] social_sentiment failed: %s", exc)

        # Build summary
        parts = []
        if "target_industry_rank" in raw:
            r = raw["target_industry_rank"]
            parts.append(f"行业[{r['name']}]在板块排名中位列第{r.get('rank', '?')}名，涨跌幅{r.get('change_pct', '?')}%")
        if raw.get("sentiment", {}).get("score") is not None:
            parts.append(f"舆情情绪评分{raw['sentiment']['score']}，总讨论量{raw.get('sentiment', {}).get('total_discussion', '?')}")
        if raw.get("social_sentiment", {}).get("score") is not None:
            parts.append(f"社交情绪评分{raw['social_sentiment']['score']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return MAINLINE_POSITION
