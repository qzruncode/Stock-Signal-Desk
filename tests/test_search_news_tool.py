"""News retrieval preserves raw evidence for model-based relevance judgment."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pandas as pd

from market_data_service.providers.news import (
    _entity_mentions,
    read_company_news_akshare,
)


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


def test_company_news_source_dedupes_records_but_retains_unverified_results() -> None:
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
    with (
        patch(
            "market_data_service.providers.news._stock_name",
            return_value="嘉益股份",
        ),
        patch(
            "market_data_service.providers.news._fetch_direct",
            return_value=direct,
        ),
    ):
        result = read_company_news_akshare("301004", days=30, limit=20, use_cache=False)

    assert result["success"] is True
    assert result["item_count"] == 2
    assert result["unverified_entity_mention_count"] == 1
    assert all(item["relevance"] is None for item in result["items"])
    assert all(item["relevance_score"] is None for item in result["items"])
    assert all(item["semantic_status"] == "model_required" for item in result["items"])
