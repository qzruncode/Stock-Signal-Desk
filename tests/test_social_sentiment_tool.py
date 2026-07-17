from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from src.tools.get_social_sentiment import _classify_sentiment, _post_time, get_social_sentiment


def test_social_sentiment_phrase_classifier_is_explicit_and_bounded() -> None:
    positive, positive_hits, _ = _classify_sentiment("公司业绩超预期并宣布回购")
    negative, _, negative_hits = _classify_sentiment("公司预亏并收到行政处罚")

    assert positive == 1.0
    assert "超预期" in positive_hits
    assert negative == -1.0
    assert "预亏" in negative_hits


def test_post_time_rolls_december_back_when_current_date_is_january() -> None:
    now = datetime.fromisoformat("2026-01-02T10:00:00+08:00")

    parsed = _post_time("12-31 09:30", now=now)

    assert parsed is not None
    assert parsed.year == 2025


def test_social_sentiment_scores_user_posts_separately_from_syndicated_info() -> None:
    posts = [
        {
            "title": "用户看好公司增长",
            "source": "东方财富股吧",
            "author": "用户甲",
            "post_kind": "user_post",
            "url": "https://example.com/user-positive",
            "read_count": 100,
            "reply_count": 10,
            "sentiment_score": 1.0,
            "label": "positive",
            "positive_hits": ["看好", "增长"],
            "negative_hits": [],
            "publish_time": datetime.now().astimezone().isoformat(),
            "classification_method": "deterministic_title_phrase_rules",
            "page_number": 1,
        },
        {
            "title": "资讯号称公司下跌",
            "source": "东方财富股吧",
            "author": "测试公司资讯",
            "post_kind": "syndicated_info",
            "url": "https://example.com/info-negative",
            "read_count": 1000,
            "reply_count": 1,
            "sentiment_score": -1.0,
            "label": "negative",
            "positive_hits": [],
            "negative_hits": ["下跌"],
            "publish_time": datetime.now().astimezone().isoformat(),
            "classification_method": "deterministic_title_phrase_rules",
            "page_number": 1,
        },
    ]
    with patch(
        "src.tools.get_social_sentiment._fetch_guba_sample",
        return_value=(posts, True, [], False, False),
    ), patch(
        "src.tools.get_social_sentiment._fetch_xueqiu_mentions",
        return_value=([], []),
    ), patch(
        "src.tools.get_social_sentiment._fetch_diagnose_score",
        return_value=([{"date": "2026-07-15", "score": 70.0, "close": 10.0}], 70.0, []),
    ):
        result = get_social_sentiment("600519", limit=10)

    assert result["success"] is True
    assert result["user_post_count"] == 1
    assert result["syndicated_info_count"] == 1
    assert result["sentiment_score"] == 100.0
    assert result["overall_score"] == 100.0
    assert result["diagnose_score_semantics"].startswith("东方财富千股千评")
    assert result["coverage_complete"] is False
    assert result["warnings"]


def test_social_sentiment_empty_successful_page_is_valid_sample_result() -> None:
    with patch(
        "src.tools.get_social_sentiment._fetch_guba_sample",
        return_value=([], True, [], False, True),
    ), patch(
        "src.tools.get_social_sentiment._fetch_xueqiu_mentions",
        return_value=([], []),
    ), patch(
        "src.tools.get_social_sentiment._fetch_diagnose_score",
        return_value=([], None, []),
    ):
        result = get_social_sentiment("920000")

    assert result["success"] is True
    assert result["item_count"] == 0
    assert result["sentiment_score"] == 0.0


def test_social_sentiment_uses_stock_news_as_non_social_fallback_when_guba_fails() -> None:
    fallback = [{
        "title": "贵州茅台经营稳健",
        "summary": "公司新闻摘要",
        "source": "东方财富新闻",
        "author": None,
        "post_kind": "syndicated_info",
        "url": "https://example.com/news",
        "read_count": None,
        "reply_count": None,
        "sentiment_score": 1.0,
        "label": "positive",
        "positive_hits": ["稳健"],
        "negative_hits": [],
        "publish_time": datetime.now().astimezone().isoformat(),
        "classification_method": "non_social_fallback",
        "page_number": None,
    }]
    with patch(
        "src.tools.get_social_sentiment._fetch_guba_sample",
        return_value=([], False, ["股吧 unavailable"], False, False),
    ), patch(
        "src.tools.get_social_sentiment._fetch_stock_news_fallback",
        return_value=(fallback, False),
    ), patch(
        "src.tools.get_social_sentiment._fetch_xueqiu_mentions",
        return_value=([], []),
    ), patch(
        "src.tools.get_social_sentiment._fetch_diagnose_score",
        return_value=([], None, []),
    ):
        result = get_social_sentiment("600519", limit=10)

    assert result["success"] is True
    assert result["partial"] is True
    assert result["fallback_used"] is True
    assert result["syndicated_info_count"] == 1
    assert result["user_post_count"] == 0
    assert result["sentiment_score"] == 0.0
    assert any("不计入用户情绪" in warning for warning in result["warnings"])
