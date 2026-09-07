"""Social evidence remains raw until model synthesis."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from market_data_service.providers.get_social_sentiment import (
    _post_time,
    get_social_sentiment,
)


def test_post_time_rolls_december_back_when_current_date_is_january() -> None:
    now = datetime.fromisoformat("2026-01-02T10:00:00+08:00")
    parsed = _post_time("12-31 09:30", now=now)
    assert parsed is not None
    assert parsed.year == 2025


def test_social_tool_preserves_discussion_and_syndicated_provenance() -> None:
    posts = [
        {
            "title": "用户讨论公司增长",
            "source": "东方财富股吧",
            "author": "用户甲",
            "post_kind": "public_discussion",
            "url": "https://example.com/user",
            "read_count": 100,
            "reply_count": 10,
            "publish_time": datetime.now().astimezone().isoformat(),
            "semantic_status": "model_required",
            "page_number": 1,
        }
    ]
    fallback = [
        {
            "title": "公司新闻摘要",
            "source": "东方财富新闻",
            "author": None,
            "post_kind": "syndicated_info",
            "url": "https://example.com/info",
            "read_count": None,
            "reply_count": None,
            "publish_time": datetime.now().astimezone().isoformat(),
            "semantic_status": "not_social_evidence",
            "page_number": None,
        }
    ]
    with (
        patch(
            "market_data_service.providers.get_social_sentiment._fetch_guba_sample",
            return_value=(posts, True, [], False, True),
        ),
        patch(
            "market_data_service.providers.get_social_sentiment._fetch_xueqiu_mentions",
            return_value=([], []),
        ),
        patch(
            "market_data_service.providers.get_social_sentiment._fetch_stock_news_fallback",
            return_value=(fallback, False),
        ),
        patch(
            "market_data_service.providers.get_social_sentiment._fetch_diagnose_score",
            return_value=(
                [{"date": "2026-07-15", "score": 70.0, "close": 10.0}],
                70.0,
                [],
            ),
        ),
    ):
        result = get_social_sentiment("600519", limit=10)

    assert result["success"] is True
    assert result["user_post_count"] == 1
    assert result["sentiment_score"] is None
    assert result["overall_score"] is None
    assert result["analysis"]["semantic_status"] == "model_required"
    assert result["diagnose_score"] == 70.0


def test_empty_successful_sample_is_not_converted_to_neutral_score() -> None:
    with (
        patch(
            "market_data_service.providers.get_social_sentiment._fetch_guba_sample",
            return_value=([], True, [], False, True),
        ),
        patch(
            "market_data_service.providers.get_social_sentiment._fetch_xueqiu_mentions",
            return_value=([], []),
        ),
        patch(
            "market_data_service.providers.get_social_sentiment._fetch_diagnose_score",
            return_value=([], None, []),
        ),
    ):
        result = get_social_sentiment("920000")

    assert result["success"] is True
    assert result["item_count"] == 0
    assert result["sentiment_score"] is None
    assert result["positive_count"] is None


def test_company_news_fallback_is_not_counted_as_social_discussion() -> None:
    fallback = [
        {
            "title": "贵州茅台经营情况",
            "summary": "公司新闻摘要",
            "source": "东方财富新闻",
            "author": None,
            "post_kind": "syndicated_info",
            "url": "https://example.com/news",
            "read_count": None,
            "reply_count": None,
            "publish_time": datetime.now().astimezone().isoformat(),
            "semantic_status": "not_social_evidence",
            "page_number": None,
        }
    ]
    with (
        patch(
            "market_data_service.providers.get_social_sentiment._fetch_guba_sample",
            return_value=([], False, ["股吧 unavailable"], False, False),
        ),
        patch(
            "market_data_service.providers.get_social_sentiment._fetch_stock_news_fallback",
            return_value=(fallback, False),
        ),
        patch(
            "market_data_service.providers.get_social_sentiment._fetch_xueqiu_mentions",
            return_value=([], []),
        ),
        patch(
            "market_data_service.providers.get_social_sentiment._fetch_diagnose_score",
            return_value=([], None, []),
        ),
    ):
        result = get_social_sentiment("600519", limit=10)

    assert result["success"] is True
    assert result["partial"] is True
    assert result["fallback_used"] is True
    assert result["syndicated_info_count"] == 1
    assert result["user_post_count"] == 0
    assert result["sentiment_score"] is None
