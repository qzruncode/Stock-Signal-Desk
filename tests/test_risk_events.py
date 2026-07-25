"""Risk-event retrieval must not classify raw disclosures with phrase lists."""

from __future__ import annotations

from unittest.mock import patch

from src.tools.get_risk_events import _evidence_item, get_risk_events


def test_evidence_item_preserves_source_fields_without_risk_label() -> None:
    item = _evidence_item(
        {
            "title": "控股股东办理部分股份解除质押",
            "source_notice_type": "股权质押公告",
            "publish_date": "2026-07-15",
            "url": "https://example.com/announcement",
        },
        source_type="announcement",
    )

    assert item is not None
    assert item["title"] == "控股股东办理部分股份解除质押"
    assert item["summary"] == "股权质押公告"
    assert item["semantic_status"] == "model_required"
    assert "risk_label" not in item
    assert "severity" not in item
    assert "status" not in item


def test_risk_tool_prefers_formal_announcement_when_title_and_date_match() -> None:
    news = {
        "success": True,
        "name": "测试公司",
        "source": "AKShare stock_news_em",
        "items": [{
            "title": "测试公司收到监管问询函",
            "summary": "交易所要求说明相关事项",
            "published": "2026-07-15T10:00:00+08:00",
            "source": "媒体",
            "url": "https://example.com/news",
        }],
        "errors": [],
        "warnings": [],
    }
    announcements = {
        "success": True,
        "name": "测试公司",
        "source_chain": ["AKShare/东方财富公司公告"],
        "items": [{
            "title": "测试公司收到监管问询函",
            "source_notice_type": "交易所问询函",
            "publish_date": "2026-07-15",
            "source": "AKShare/东方财富公司公告",
            "url": "https://example.com/announcement",
        }],
        "errors": [],
        "warnings": [],
    }
    with patch(
        "src.tools.search_news.search_news",
        return_value=news,
    ), patch(
        "src.tools.get_announcements.get_announcements",
        return_value=announcements,
    ):
        result = get_risk_events("600519")

    assert result["success"] is True
    assert result["item_count"] == 1
    assert result["items"][0]["source_type"] == "announcement"
    assert result["items"][0]["url"] == "https://example.com/announcement"
    assert result["items"][0]["requires_fulltext_verification"] is True
    assert result["has_risk_events"] is None
    assert result["analysis"]["semantic_status"] == "model_required"


def test_empty_successful_sources_are_not_converted_to_no_risk_conclusion() -> None:
    news = {"success": True, "items": [], "errors": [], "warnings": []}
    announcements = {
        "success": True,
        "items": [],
        "errors": [],
        "warnings": [],
    }
    with patch(
        "src.tools.search_news.search_news",
        return_value=news,
    ), patch(
        "src.tools.get_announcements.get_announcements",
        return_value=announcements,
    ):
        result = get_risk_events("000001")

    assert result["success"] is True
    assert result["has_risk_events"] is None
    assert result["item_count"] == 0
    assert result["warnings"]
