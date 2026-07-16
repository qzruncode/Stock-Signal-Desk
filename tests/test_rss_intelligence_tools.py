# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
from unittest.mock import patch

from src.tools.get_monetary_policy_operations import (
    _operation_item,
    get_monetary_policy_operations,
)
from src.tools.get_regulatory_updates import (
    _parse_szse_listing_page,
    _specs,
    get_regulatory_updates,
)
from src.tools.search_research_library import search_research_library
from src.tools.rss_sources import RSS_ROUTE_CAPABILITIES


def _feed(items=None, errors=None):
    return {
        "feed_title": "test", "feed_link": "https://example.test",
        "items": items or [], "item_count": len(items or []),
        "errors": errors or [], "_cached": False,
    }


def test_all_47_infos_routes_have_an_explicit_business_capability_map():
    assert len(RSS_ROUTE_CAPABILITIES) == 47
    assert all(path.startswith("/") and capabilities for path, capabilities in RSS_ROUTE_CAPABILITIES.items())


def test_regulatory_disclosure_specs_use_each_exchange_official_query_contract():
    sse = _specs(
        "disclosure", "sse", code="600519", keyword="600519", days=30,
        project_type="all", project_stage="all", project_status="all",
    )
    szse = _specs(
        "disclosure", "szse", code="300850", keyword="300850", days=30,
        project_type="all", project_stage="all", project_status="all",
    )

    assert sse[0]["path"] == "/sse/disclosure/:query?"
    assert "productId=600519" in sse[0]["params"]["query"]
    assert szse[0]["path"] == "/szse/disclosure/listed/notice/:query?"
    assert "stock=300850" in szse[0]["params"]["query"]
    assert "beginDate=" in sse[0]["params"]["query"]
    assert "endDate=" in szse[0]["params"]["query"]


def test_regulatory_all_projects_fans_out_szse_ipo_refinancing_and_restructuring():
    specs = _specs(
        "project", "szse", code=None, keyword="", days=90,
        project_type="all", project_stage="all", project_status="inquired",
    )

    assert {row["params"]["type"] for row in specs} == {"1", "2", "3"}
    assert all(row["params"]["status"] == "30" for row in specs)


def test_szse_notice_route_is_exposed_as_company_listing_notice_not_convertible_bond():
    specs = _specs(
        "listing_notice", "szse", code=None, keyword="", days=90,
        project_type="all", project_stage="all", project_status="all",
    )

    assert specs == [{
        "path": "/szse/notice", "params": {}, "exchange": "SZSE",
        "kind": "listing_notice",
    }]


def test_szse_listing_page_parser_reads_the_actual_official_page_contract():
    html = f"""
    <div class="article-list"><ul class="newslist"><li>
      <script>
        var curHref = './t{datetime.now():%Y%m%d}_1.html';
        var curTitle = '关于测试股份有限公司股票上市交易的公告';
      </script>
      <span class="time">{datetime.now():%Y-%m-%d}</span>
    </li></ul></div>
    """

    items = _parse_szse_listing_page(html, days=30, limit=5)

    assert items[0]["event_type"] == "listing_notice"
    assert items[0]["title"] == "关于测试股份有限公司股票上市交易的公告"
    assert items[0]["link"].endswith(f"/t{datetime.now():%Y%m%d}_1.html")


def test_regulatory_successful_empty_official_feed_is_valid_zero_without_web_search():
    with patch("src.tools.get_regulatory_updates._resolve_subject", return_value=("300850", "新强联")), \
         patch("api.v1.endpoints._rss_reader.read_feed", return_value=_feed()), \
         patch("src.tools.websearch.websearch") as websearch:
        result = get_regulatory_updates(
            "300850", event_type="inquiry", market="szse", days=730,
            fallback_to_web=True,
        )

    assert result["success"] is True
    assert result["item_count"] == 0
    assert result["source"] == "交易所官方披露/RSSHub"
    assert result["fallback_attempted"] is False
    assert any("没有匹配记录" in warning for warning in result["warnings"])
    websearch.assert_not_called()


def test_regulatory_web_fallback_only_keeps_official_exchange_domains():
    search_result = {
        "provider": "exa", "success": True,
        "results": [
            {
                "title": "贵州茅台股份有限公司临时公告",
                "url": f"https://static.sse.com.cn/disclosure/{datetime.now():%Y-%m-%d}/a.pdf",
                "snippet": "贵州茅台官方披露",
            },
            {
                "title": "贵州茅台伪造公告",
                "url": f"https://evil.example/{datetime.now():%Y-%m-%d}/a.pdf",
                "snippet": "非官方",
            },
        ],
    }
    with patch("src.tools.get_regulatory_updates._resolve_subject", return_value=("600519", "贵州茅台")), \
         patch("api.v1.endpoints._rss_reader.read_feed", return_value=_feed(errors=["upstream down"])), \
         patch("src.tools.websearch.websearch", return_value=search_result):
        result = get_regulatory_updates(
            "600519", event_type="disclosure", market="sse", days=30,
            fallback_to_web=True,
        )

    assert result["success"] is True
    assert result["fallback_used"] is True
    assert result["source"] == "交易所官网/websearch"
    assert [item["link"] for item in result["items"]] == [
        f"https://static.sse.com.cn/disclosure/{datetime.now():%Y-%m-%d}/a.pdf"
    ]


def test_stock_research_category_delegates_to_structured_report_tool():
    report = {
        "success": True, "partial": False, "source": "AKShare/东方财富券商研报",
        "data_time": "2026-07-15", "retrieved_at": "2026-07-16T10:00:00+08:00",
        "is_stale": False, "freshness_unknown": False, "errors": [], "warnings": [],
        "items": [{
            "title": "贵州茅台深度报告", "publish_date": "2026-07-15",
            "url": "https://data.eastmoney.com/report/1", "org": "测试证券",
            "rating": "买入", "industry": "白酒", "profit_forecasts": [{"year": 2026, "eps": 75.0}],
            "source_type": "broker_research",
        }],
    }
    with patch("src.tools.get_research_report.get_research_report", return_value=report) as delegated:
        result = search_research_library("600519", category="stock", days=365, limit=5)

    delegated.assert_called_once_with("600519", days=365, limit=5)
    assert result["research_category"] == "stock"
    assert result["items"][0]["rating"] == "买入"
    assert result["items"][0]["profit_forecasts"][0]["eps"] == 75.0


def test_pbc_operation_parser_does_not_read_calendar_month_as_term():
    operation = _operation_item(
        {"title": "公开市场业务交易公告 [2026]第136号", "published": "2026-07-16", "link": "https://pbc.gov.cn/a"},
        "2026年7月16日人民银行以固定利率、数量招标方式开展了6260亿元逆回购操作。"
        "期限 7天 1.40% 6260亿元 6260亿元，中标量全额满足。",
    )

    assert operation["instrument_code"] == "reverse_repo"
    assert operation["term_days"] == 7.0
    assert operation["term_months"] is None
    assert operation["amount_yi"] == 6260.0
    assert operation["rate_pct"] == 1.4
    assert operation["bulletin_number"] == 136


def test_pbc_feed_reports_incomplete_window_instead_of_claiming_full_coverage():
    now = datetime.now()
    items = [
        {"title": "公开市场业务交易公告 [2026]第1号", "published": now.isoformat(), "link": "https://pbc.gov.cn/1", "summary": "开展100亿元7天期逆回购操作，操作利率为1.40%"},
        {"title": "公开市场业务交易公告 [2026]第2号", "published": (now - timedelta(days=24)).isoformat(), "link": "https://pbc.gov.cn/2", "summary": "开展200亿元7天期逆回购操作，操作利率为1.40%"},
    ]
    with patch("api.v1.endpoints._rss_reader.read_feed", return_value=_feed(items=items)):
        result = get_monetary_policy_operations(days=30, fallback_to_web=False)

    assert result["success"] is True
    assert result["coverage_complete"] is False
    assert result["source"] == "中国人民银行/RSSHub"
    assert any("尚未完整覆盖" in warning for warning in result["warnings"])


def test_pbc_complete_acquisition_failure_has_no_fake_rss_source():
    with patch("api.v1.endpoints._rss_reader.read_feed", return_value=_feed(errors=["503"])):
        result = get_monetary_policy_operations(fallback_to_web=False)

    assert result["success"] is False
    assert result["source"] == "none"
    assert result["fallback_recommended"] is True
