from __future__ import annotations

from unittest.mock import patch

from src.tools.get_company_theme_evidence import get_company_theme_evidence


def test_company_theme_evidence_collects_one_stock_from_project_tools() -> None:
    profile = {
        "success": True,
        "company_name": "测试公司",
        "short_name": "测试公司",
        "industry": "专用设备",
        "main_business": "研发和销售精密执行部件",
        "business_scope": "精密传动部件制造",
        "company_profile": "面向先进制造客户提供核心部件",
        "source": "巨潮资讯/AKShare",
        "data_time": "2026-07-26",
        "errors": [],
    }
    segments = {
        "success": True,
        "items": [
            {
                "report_date": "2025-12-31",
                "category": "product",
                "segment_name": "精密执行部件",
                "revenue": 100_000_000,
                "revenue_share_pct": 35.0,
            }
        ],
        "source": "东方财富主营构成",
        "source_url": "https://example.com/segments",
        "data_time": "2025-12-31",
        "errors": [],
    }
    announcements = {
        "success": True,
        "items": [
            {
                "title": "关于扩建精密部件产线的公告",
                "publish_date": "2026-06-01",
                "url": "https://example.com/notice",
                "source": "交易所公告",
            }
        ],
        "errors": [],
    }
    news = {
        "success": True,
        "items": [
            {
                "title": "测试公司推进新产品客户验证",
                "summary": "测试公司相关产品已进入客户验证。",
                "published": "2026-07-01",
                "url": "https://example.com/news",
                "source": "公司新闻",
            }
        ],
        "errors": [],
    }
    reports = {
        "success": True,
        "items": [
            {
                "title": "执行部件业务进入放量阶段",
                "publish_date": "2026-07-02",
                "url": "https://example.com/report",
                "source": "个股研报",
            }
        ],
        "errors": [],
    }

    with (
        patch(
            "src.data.stock_index_loader.get_index_stock_name",
            return_value="测试公司",
        ),
        patch("src.tools.get_stock_info.get_stock_info", return_value=profile),
        patch(
            "src.tools.get_business_segments.get_business_segments",
            return_value=segments,
        ),
        patch(
            "src.tools.get_announcements.get_announcements",
            return_value=announcements,
        ),
        patch("src.tools.search_news.search_news", return_value=news),
        patch(
            "src.tools.get_research_report.get_research_report",
            return_value=reports,
        ),
        patch("src.tools.websearch.websearch") as websearch,
    ):
        result = get_company_theme_evidence(
            "300001",
            target_topics=["目标产业"],
            domains=["核心部件"],
            objective="找正在大力发展的公司",
        )

    assert result["success"] is True
    assert result["analysis_unit"] == "single_security"
    assert result["symbol"] == "300001"
    assert result["project_source_success_count"] == 5
    assert result["project_source_coverage_complete"] is True
    assert result["fallback_attempted"] is False
    assert result["fallback_used"] is False
    assert {document["source_type"] for document in result["evidence_documents"]} == {
        "company_profile",
        "business_segments",
        "announcement",
        "company_news",
        "stock_research",
    }
    websearch.assert_not_called()


def test_company_theme_evidence_uses_stock_scoped_web_fallback_only_when_empty() -> None:
    empty = {
        "success": False,
        "items": [],
        "errors": ["项目来源不可用"],
    }
    fallback = {
        "success": True,
        "provider": "test",
        "results": [
            {
                "title": "测试公司目标产业项目进展",
                "snippet": "测试公司披露目标产业项目已进入客户验证。",
                "url": "https://example.com/fallback",
                "published_date": "2026-07-20",
                "source": "测试公开源",
            }
        ],
        "errors": [],
    }
    with (
        patch(
            "src.data.stock_index_loader.get_index_stock_name",
            return_value="测试公司",
        ),
        patch("src.tools.get_stock_info.get_stock_info", return_value=empty),
        patch(
            "src.tools.get_business_segments.get_business_segments",
            return_value=empty,
        ),
        patch(
            "src.tools.get_announcements.get_announcements",
            return_value=empty,
        ),
        patch("src.tools.search_news.search_news", return_value=empty),
        patch(
            "src.tools.get_research_report.get_research_report",
            return_value=empty,
        ),
        patch(
            "src.tools.websearch.websearch",
            return_value=fallback,
        ) as websearch,
    ):
        result = get_company_theme_evidence(
            "300001",
            target_topics=["目标产业"],
            domains=["核心部件"],
            objective="核验是否正在持续投入",
        )

    assert result["success"] is True
    assert result["fallback_attempted"] is True
    assert result["fallback_used"] is True
    assert result["evidence_document_count"] == 1
    assert result["evidence_documents"][0]["source_type"] == "public_web_fallback"
    query = websearch.call_args.kwargs["query"]
    assert "测试公司" in query
    assert "目标产业" in query
    assert "核心部件" in query
    assert "持续投入" in query
