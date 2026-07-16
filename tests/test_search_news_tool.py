# -*- coding: utf-8 -*-
"""Precision and contract tests for the single-stock news tool."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pandas as pd

from src.tools.search_news import _entity_relevance, search_news


def test_market_table_body_mention_is_not_company_news() -> None:
    relevance, score = _entity_relevance(
        "北交所成交活跃股排行榜",
        "920149 旭杰科技 957.86 920000 安徽凤凰",
        "920000",
        "安徽凤凰",
    )
    assert relevance == "body_only_mention"
    assert score < 70


def test_company_name_in_title_is_precise_subject_match() -> None:
    assert _entity_relevance(
        "嘉益股份：控股股东拟增持",
        "增持金额不低于4000万元",
        "301004",
        "嘉益股份",
    ) == ("company_subject", 100)


def test_search_news_dedupes_sources_and_excludes_weak_mentions() -> None:
    now = datetime.now().replace(microsecond=0).isoformat()
    direct = pd.DataFrame([
        {
            "新闻标题": "嘉益股份：控股股东拟增持",
            "新闻内容": "嘉益股份公告增持计划",
            "发布时间": now,
            "文章来源": "财联社",
            "新闻链接": "https://example.com/a?from=direct",
        },
        {
            "新闻标题": "创业板成交活跃股排行榜",
            "新闻内容": "301004 嘉益股份",
            "发布时间": now,
            "文章来源": "数据榜",
            "新闻链接": "https://example.com/weak",
        },
    ])
    rss = {
        "items": [{
            "title": "嘉益股份：控股股东拟增持",
            "summary": "嘉益股份公告增持计划，增持金额不低于4000万元",
            "published": now,
            "source": "东方财富",
            "link": "https://example.com/a?from=rss",
        }],
        "errors": [],
        "_cached": False,
    }
    with patch("src.tools.search_news._stock_name", return_value="嘉益股份"), \
         patch("src.tools.search_news._fetch_direct", return_value=direct), \
         patch("src.tools.search_news._fetch_rss", return_value=rss):
        result = search_news("301004", days=30, limit=20)

    assert result["success"] is True
    assert result["item_count"] == 1
    assert result["excluded_weak_mention_count"] == 1
    assert result["items"][0]["title"].startswith("嘉益股份")
    assert result["fallback_used"] is False
