"""News retrieval preserves raw evidence for model-based relevance judgment."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pandas as pd

from src.tools.search_news import _entity_mentions, search_news


def test_entity_mentions_reports_exact_occurrence_without_relevance_score() -> None:
    mentions = _entity_mentions(
        "北交所成交活跃股排行榜",
        "920149 旭杰科技 957.86 920000 安徽凤凰",
        "920000",
        "安徽凤凰",
    )
    assert mentions == {
        "name_in_title": False,
        "name_in_summary": True,
        "code_in_title": False,
        "code_in_summary": True,
    }


def test_search_news_dedupes_sources_but_retains_unverified_results() -> None:
    now = datetime.now().replace(microsecond=0).isoformat()
    direct = pd.DataFrame(
        [
            {
                "新闻标题": "嘉益股份：控股股东拟增持",
                "新闻内容": "嘉益股份公告增持计划",
                "发布时间": now,
                "文章来源": "财联社",
                "新闻链接": "https://example.com/a?from=direct",
            },
            {
                "新闻标题": "创业板成交活跃股排行榜",
                "新闻内容": "市场成交信息",
                "发布时间": now,
                "文章来源": "数据榜",
                "新闻链接": "https://example.com/weak",
            },
        ]
    )
    rss = {
        "items": [
            {
                "title": "嘉益股份：控股股东拟增持",
                "summary": "嘉益股份公告增持计划，增持金额不低于4000万元",
                "published": now,
                "source": "东方财富",
                "link": "https://example.com/a?from=rss",
            }
        ],
        "errors": [],
        "_cached": False,
    }
    with (
        patch(
            "src.tools.search_news._stock_name",
            return_value="嘉益股份",
        ),
        patch(
            "src.tools.search_news._fetch_direct",
            return_value=direct,
        ),
        patch(
            "src.tools.search_news._fetch_rss",
            return_value=rss,
        ),
    ):
        result = search_news("301004", days=30, limit=20)

    assert result["success"] is True
    assert result["item_count"] == 2
    assert result["unverified_entity_mention_count"] == 1
    assert all(item["relevance"] is None for item in result["items"])
    assert all(item["relevance_score"] is None for item in result["items"])
    assert all(item["semantic_status"] == "model_required" for item in result["items"])
