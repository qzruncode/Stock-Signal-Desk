"""Tests for the evidence-first risk-event tool."""

from __future__ import annotations

from unittest.mock import patch

from src.tools.get_risk_events import _classify_risk_event, get_risk_events


def test_classify_inquiry_as_medium_regulatory() -> None:
    risk = _classify_risk_event("收到交易所问询函并说明相关问题", source_kind="announcement")

    assert risk is not None
    assert risk["risk_label"] == "监管执法"
    assert risk["severity"] == "medium"
    assert risk["status"] == "active"


def test_normal_internal_control_report_is_not_itself_a_risk() -> None:
    risk = _classify_risk_event("关于2025年度内部控制审计报告的公告", source_kind="announcement")

    assert risk is None


def test_release_of_pledge_is_mitigated_not_medium_active_risk() -> None:
    risk = _classify_risk_event("控股股东办理部分股份解除质押", source_kind="announcement")

    assert risk is not None
    assert risk["risk_label"] == "股东质押与减持"
    assert risk["severity"] == "low"
    assert risk["status"] == "mitigated"


def test_inquiry_response_is_mitigated_not_a_new_active_inquiry() -> None:
    risk = _classify_risk_event("发行人及保荐机构关于审核问询函的回复", source_kind="announcement")

    assert risk is not None
    assert risk["severity"] == "low"
    assert risk["status"] == "mitigated"


def test_risk_tool_prefers_formal_announcement_and_preserves_evidence_url() -> None:
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
    with patch("src.tools.search_news.search_news", return_value=news), \
         patch("src.tools.get_announcements.get_announcements", return_value=announcements):
        result = get_risk_events("600519")

    assert result["success"] is True
    assert result["item_count"] == 1
    assert result["items"][0]["source_type"] == "announcement"
    assert result["items"][0]["url"] == "https://example.com/announcement"
    assert result["items"][0]["requires_fulltext_verification"] is False


def test_no_detected_event_is_valid_negative_evidence_not_acquisition_failure() -> None:
    news = {"success": True, "items": [], "errors": [], "warnings": []}
    announcements = {"success": True, "items": [], "errors": [], "warnings": []}
    with patch("src.tools.search_news.search_news", return_value=news), \
         patch("src.tools.get_announcements.get_announcements", return_value=announcements):
        result = get_risk_events("000001")

    assert result["success"] is True
    assert result["has_risk_events"] is False
    assert result["item_count"] == 0
    assert result["warnings"]
