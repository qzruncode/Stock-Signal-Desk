# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
import threading
from unittest.mock import patch

import pytest

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
from src.tools.search_financial_news import search_financial_news
from src.tools.rss_sources import RSS_ROUTE_CAPABILITIES


def _feed(items=None, errors=None):
    return {
        "feed_title": "test", "feed_link": "https://example.test",
        "items": items or [], "item_count": len(items or []),
        "errors": errors or [], "_cached": False,
    }


def test_financial_news_successful_empty_feed_is_not_a_tool_failure():
    catalog = {
        "count": 1,
        "routes": [{"path": "/cls/telegraph", "name": "财联社电报"}],
    }
    with patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=catalog), \
         patch("api.v1.endpoints._rss_reader.read_feed", return_value=_feed()), \
         patch("src.tools.search_financial_news._select_specs", return_value=[
             ("/cls/telegraph", {}, "财联社电报"),
         ]), \
         patch("src.tools.websearch.websearch", return_value={
             "success": True, "provider": "test", "results": [], "errors": [],
         }):
        result = search_financial_news(
            "不存在的主题",
            topic="industry",
            subjects=["不存在的主题"],
            fallback_to_web=True,
        )

    assert result["success"] is True
    assert result["item_count"] == 0
    assert result["source"] == "RSSHub"
    assert result["rss_routes"][0]["success"] is True


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


def test_regulatory_content_details_are_read_in_parallel():
    now = datetime.now().isoformat()
    items = [
        {
            "title": f"测试公司第{index}号公告",
            "published": now,
            "link": f"https://www.sse.com.cn/{index}",
            "summary": "公告摘要",
        }
        for index in (1, 2)
    ]
    barrier = threading.Barrier(2)

    def read_item(**kwargs):
        barrier.wait(timeout=2)
        return {"content_text": f"{kwargs['title']}正文", "_fallback": False}

    with patch("src.tools.get_regulatory_updates._resolve_subject", return_value=(None, None)), \
         patch("api.v1.endpoints._rss_reader.read_feed", return_value=_feed(items=items)), \
         patch("api.v1.endpoints._rss_reader.read_item", side_effect=read_item):
        result = get_regulatory_updates(
            event_type="disclosure", market="sse", days=30, limit=2,
            include_content=True, fallback_to_web=False,
        )

    assert result["success"] is True
    assert result["item_count"] == 2
    assert all(item.get("content_text", "").endswith("正文") for item in result["items"])


def test_research_library_rejects_stock_category_in_favor_of_dedicated_workflow():
    with pytest.raises(ValueError, match="category"):
        search_research_library(
            "600519", category="stock", subjects=["600519"], days=365, limit=5,
        )


def test_research_library_generic_dimension_words_do_not_admit_unrelated_industries():
    now = datetime.now().isoformat()
    feed = _feed(items=[
        {
            "title": "2026年中国风机价值链分析",
            "published": now,
            "link": "https://example.test/fan",
            "summary": "风机产业链市场空间与竞争格局",
        },
        {
            "title": "人形机器人核心零部件研究",
            "published": now,
            "link": "https://example.test/humanoid",
            "summary": "人形机器人产业链的价值量、市场空间与竞争格局",
        },
    ])
    with patch("api.v1.endpoints._rss_reader.read_feed", return_value=feed):
        result = search_research_library(
            "人形机器人 产业链 价值量 市场空间 竞争格局",
            category="industry",
            subjects=["人形机器人"],
            days=365,
            limit=10,
            fallback_to_web=False,
        )

    assert [item["title"] for item in result["items"]] == ["人形机器人核心零部件研究"]


def test_research_library_keeps_topic_anchor_with_playbook_query_prefixes():
    now = datetime.now().isoformat()
    feed = _feed(items=[
        {
            "title": "纺织服装ESG专题报告：技术与供应链重构",
            "published": now,
            "link": "https://example.test/textile",
            "summary": "技术创新重构价值链，关注上市公司标的。",
        },
        {
            "title": "人形机器人核心零部件产业研究",
            "published": now,
            "link": "https://example.test/humanoid",
            "summary": "覆盖丝杠、减速器、伺服、传感器与灵巧手。",
        },
        {
            "title": "微特电机行业政策汇总",
            "published": now,
            "link": "https://example.test/motor",
            "summary": "覆盖电机行业政策和主要上市公司。",
        },
    ])
    with patch("api.v1.endpoints._rss_reader.read_feed", return_value=feed):
        result = search_research_library(
            "检索人形机器人 产业链 A股 标的 丝杠 减速器 伺服 电机 传感器 灵巧手 机器视觉",
            category="industry",
            subjects=["人形机器人"],
            days=365,
            limit=10,
            fallback_to_web=False,
        )

    assert [item["title"] for item in result["items"]] == ["人形机器人核心零部件产业研究"]


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
    with patch("src.tools.get_monetary_policy_operations._fetch_official_listing", return_value=([], [], False)), \
         patch("api.v1.endpoints._rss_reader.read_feed", return_value=_feed(items=items)):
        result = get_monetary_policy_operations(days=30, fallback_to_web=False)

    assert result["success"] is True
    assert result["coverage_complete"] is False
    assert result["source"] == "中国人民银行/RSSHub"
    assert any("尚未完整覆盖" in warning for warning in result["warnings"])


def test_pbc_complete_acquisition_failure_has_no_fake_rss_source():
    with patch("src.tools.get_monetary_policy_operations._fetch_official_listing", return_value=([], [], False)), \
         patch("api.v1.endpoints._rss_reader.read_feed", return_value=_feed(errors=["503"])):
        result = get_monetary_policy_operations(fallback_to_web=False)

    assert result["success"] is False
    assert result["source"] == "none"
    assert result["fallback_recommended"] is True


def test_pbc_content_details_are_read_in_parallel():
    now = datetime.now()
    items = [
        {
            "id": str(index),
            "title": f"公开市场业务交易公告 [2026]第{index}号",
            "published": now.isoformat(),
            "link": f"https://pbc.gov.cn/{index}",
            "summary": "公告摘要",
        }
        for index in (1, 2)
    ]
    barrier = threading.Barrier(2)

    def read_item(**kwargs):
        barrier.wait(timeout=2)
        amount = "100" if kwargs["item_id"] == "1" else "200"
        return {"content_text": f"开展了{amount}亿元7天期逆回购操作，操作利率为1.40%", "errors": []}

    with patch("src.tools.get_monetary_policy_operations._fetch_official_listing", return_value=([], [], False)), \
         patch("api.v1.endpoints._rss_reader.read_feed", return_value=_feed(items=items)), \
         patch("api.v1.endpoints._rss_reader.read_item", side_effect=read_item):
        result = get_monetary_policy_operations(
            days=30,
            limit=2,
            include_content=True,
            fallback_to_web=False,
        )

    assert result["success"] is True
    assert result["item_count"] == 2
    assert result["total_operation_amount_yi"] == 300.0


def test_pbc_official_directory_is_primary_and_complete():
    now = datetime.now()
    items = [
        {
            "id": str(index),
            "title": f"公开市场业务交易公告 [2026]第{index}号",
            "published": (now - timedelta(days=index)).date().isoformat(),
            "link": f"https://www.pbc.gov.cn/{index}",
            "summary": "",
            "source_type": "official_directory",
        }
        for index in (1, 2)
    ]

    def detail(url: str) -> str:
        amount = "100" if url.endswith("/1") else "200"
        return f"开展了{amount}亿元7天期逆回购操作，操作利率为1.40%"

    with patch(
        "src.tools.get_monetary_policy_operations._fetch_official_listing",
        return_value=(items, [], True),
    ), patch(
        "src.tools.get_monetary_policy_operations._fetch_official_detail",
        side_effect=detail,
    ), patch("api.v1.endpoints._rss_reader.read_feed") as feed_mock:
        result = get_monetary_policy_operations(
            days=30, limit=20, include_content=True, fallback_to_web=False
        )

    assert result["success"] is True
    assert result["coverage_complete"] is True
    assert result["source"] == "中国人民银行官网公告目录"
    assert result["item_count"] == 2
    assert result["total_operation_amount_yi"] == 300.0
    feed_mock.assert_not_called()
