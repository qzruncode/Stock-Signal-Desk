# -*- coding: utf-8 -*-
"""Offline contract tests for the Stock Agent's semantic and fallback tools."""

from __future__ import annotations

import os
import inspect
from datetime import datetime
from unittest.mock import Mock, patch

import pandas as pd
import pytest

import src.tools.search_financial_news as financial_news_module
import src.tools.search_research_library as research_library_module

from src.tools.get_consensus_estimates import get_consensus_estimates
from src.tools.get_peer_comparison import get_peer_comparison
from src.tools.get_sector_flow import _fetch_all as fetch_all_sector_flow, get_sector_flow
from src.tools.get_stock_capital_flow import _market_for, get_stock_capital_flow
from src.tools.get_monetary_policy_operations import _operation_item
from src.tools.rss_sources import RSS_ROUTE_CAPABILITIES
from src.tools.search_financial_news import (
    _select_specs,
    _subject_terms,
    search_financial_news,
)
from src.tools.webfetch import (
    MAX_RESPONSE_SIZE,
    _accept_header_for,
    _challenge_reason,
    _extract_html,
    _http_fetch,
    fetch_url,
)
from src.tools.websearch import (
    _engine_query,
    _exa_search,
    _firecrawl_search,
    _mcp_text,
    _provider_order,
    websearch,
)



"""Focused test slice 1; shared fixtures remain local to this slice."""

def _catalog_route(
    route_path: str,
    name: str,
    *,
    namespace: str = "test",
    params: list[dict] | None = None,
) -> dict:
    return {
        "route_path": route_path,
        "name": name,
        "namespace": namespace,
        "namespace_name": name,
        "description": name,
        "params": params or [],
    }
def test_research_search_tools_contain_no_query_intent_router() -> None:
    financial_source = inspect.getsource(financial_news_module)
    research_source = inspect.getsource(research_library_module)

    for source in (financial_source, research_source):
        assert "_infer_topic" not in source
        assert "_infer_category" not in source
        assert "_INTENT_WORDS" not in source
        assert "_HIGH_PRECISION_SUBJECTS" not in source
    assert {"query", "topic"} <= set(financial_news_module.TOOL.parameters["required"])
    assert {"query", "category", "subjects"} <= set(research_library_module.TOOL.parameters["required"])

def test_semantic_rss_selector_is_driven_by_structured_topic_not_query_wording() -> None:
    routes = [
        _catalog_route(
            "/eastmoney/report/:category",
            "研究报告",
            params=[
                {
                    "name": "category",
                    "required": True,
                    "options": [{"value": "stock", "label": "个股研报"}],
                }
            ],
        ),
        _catalog_route(
            "/moodysmismicrosite/report/:industry?",
            "穆迪评级",
            params=[
                {
                    "name": "industry",
                    "required": False,
                    "default": "全部",
                    "options": [],
                }
            ],
        ),
    ]

    first = _select_specs(routes, "穆迪评级报告", "research")
    second = _select_specs(routes, "请帮我找一找信用方面的机构材料", "research")

    assert first == second
    assert first[0][0] == "/eastmoney/report/:category"

def test_industry_news_uses_only_planner_supplied_subjects():
    terms = _subject_terms(["人形机器人"])

    assert terms == ["人形机器人"]

def test_all_47_infos_routes_have_an_explicit_business_capability() -> None:
    assert len(RSS_ROUTE_CAPABILITIES) == 47
    assert "research" in RSS_ROUTE_CAPABILITIES["/wkjyqh/research"]
    assert "regulatory" in RSS_ROUTE_CAPABILITIES["/sse/inquire"]
    assert "monetary_policy" in RSS_ROUTE_CAPABILITIES["/gov/pbc/tradeAnnouncement"]

def test_research_selector_never_uses_exchange_inquiry_routes() -> None:
    routes = [
        _catalog_route(
            "/szse/inquire/:category?/:select?/:keyword?",
            "半导体研究问询",
            params=[
                {"name": "category", "required": False},
                {"name": "select", "required": False},
                {"name": "keyword", "required": False},
            ],
        ),
        _catalog_route("/wkjyqh/research", "五矿期货研究报告"),
    ]

    selected = _select_specs(routes, "半导体研报", "research")

    assert [path for path, _, _ in selected] == ["/wkjyqh/research"]

def test_szse_inquiry_fills_all_path_segments_before_keyword() -> None:
    route = _catalog_route(
        "/szse/inquire/:category?/:select?/:keyword?",
        "深交所问询",
        params=[
            {"name": "category", "required": False},
            {"name": "select", "required": False},
            {"name": "keyword", "required": False},
        ],
    )

    selected = _select_specs(
        [route],
        "请查询该公司的交易所函件",
        "announcement",
        ["000001"],
    )

    assert selected[0][1] == {
        "category": "0",
        "select": "全部函件类别",
        "keyword": "000001",
    }

def test_monetary_operation_parser_extracts_amount_term_and_rate() -> None:
    parsed = _operation_item(
        {"title": "公开市场业务交易公告", "published": "2026-07-16"},
        "人民银行以固定利率、数量招标方式开展了7天期逆回购操作，操作量为1000亿元，操作利率1.40%。",
    )

    assert parsed["instrument"] == "逆回购"
    assert parsed["term_days"] == 7
    assert parsed["amount_yi"] == 1000
    assert parsed["rate_pct"] == 1.40

def test_monetary_parser_handles_spaced_html_numbers_and_central_bank_bill() -> None:
    parsed = _operation_item(
        {"title": "公开市场业务交易公告 [2026]第118号", "published": "2026-06-22"},
        "发行了2026年第六期央行票据。期次 发行量（人民币）期限 中标利率 "
        "2026年第六期央行票据（香港） 400 亿元 6 个月（182天） 1. 34 %",
    )

    assert parsed["instrument_code"] == "central_bank_bill"
    assert parsed["instrument"] == "央行票据"
    assert parsed["amount_yi"] == 400
    assert parsed["term_months"] == 6
    assert parsed["rate_pct"] == 1.34

def test_semantic_rss_normalizes_web_fallback_into_items() -> None:
    catalog = {
        "count": 47,
        "routes": [_catalog_route("/cls/telegraph/:category?", "财联社电报")],
    }
    fallback = {
        "success": True,
        "provider": "test_search",
        "results": [
            {
                "title": "贵州茅台最新公告",
                "url": "https://example.com/a",
                "snippet": "公告摘要",
                "source": "example.com",
                "published_date": "2026-07-15",
            }
        ],
    }
    with (
        patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=catalog),
        patch("api.v1.endpoints._rss_reader.read_feed", return_value={"items": [], "errors": []}),
        patch("src.tools.websearch.websearch", return_value=fallback) as mocked_websearch,
    ):
        result = search_financial_news("贵州茅台最新公告", topic="announcement")

    assert mocked_websearch.call_args.kwargs["query"] == "贵州茅台最新公告"
    assert result["success"] is True
    assert result["fallback_attempted"] is True
    assert result["fallback_used"] is True
    assert result["source"] == "websearch/test_search"
    assert result["items"][0]["source_type"] == "websearch"
    assert result["items"][0]["link"] == "https://example.com/a"

def test_semantic_rss_rejects_missing_structured_topic() -> None:
    with pytest.raises(ValueError, match="topic"):
        search_financial_news("贵州茅台换一种说法也不能触发工具内猜测")

def test_semantic_rss_macro_matching_recognizes_reverse_repo_inside_pbo_c_text() -> None:
    catalog = {
        "count": 47,
        "routes": [_catalog_route("/gov/pbc/tradeAnnouncement", "公开市场交易公告")],
    }
    feed = {
        "items": [
            {
                "title": "公开市场业务交易公告 [2026]第136号",
                "summary": "中国人民银行开展6260亿元7天期逆回购操作",
                "link": "https://www.pbc.gov.cn/example",
                "published": datetime.now().astimezone().isoformat(),
            },
            {
                "title": "韩国叫停单一股票杠杆ETF产品上市",
                "summary": "韩国央行表示将继续关注市场",
                "link": "https://example.com/korea-etf",
                "published": datetime.now().astimezone().isoformat(),
            },
        ],
        "errors": [],
    }
    with (
        patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=catalog),
        patch("api.v1.endpoints._rss_reader.read_feed", return_value=feed),
    ):
        result = search_financial_news(
            "请查看这次公开市场流动性操作",
            topic="macro",
            subjects=["逆回购"],
            fallback_to_web=False,
        )

    assert result["topic"] == "macro"
    assert result["item_count"] == 2
    by_link = {item["link"]: item for item in result["items"]}
    assert by_link["https://www.pbc.gov.cn/example"]["exact_subject_mentions"] == ["逆回购"]
    assert by_link["https://example.com/korea-etf"]["exact_subject_mentions"] == []
    assert all(item["semantic_status"] == "model_required" for item in result["items"])

def test_semantic_rss_exact_query_route_beats_broad_topic_description() -> None:
    routes = [
        _catalog_route(
            "/eastmoney/search/:keyword",
            "东方财富搜索",
            params=[{"name": "keyword", "required": True}],
        ),
        {
            **_catalog_route("/hexun/pe/news", "和讯PE资讯"),
            "description": "覆盖半导体等行业景气资讯",
        },
    ]

    selected = _select_specs(routes, "半导体景气", "industry", max_routes=2)

    assert selected[0][0] == "/eastmoney/search/:keyword"

def test_semantic_rss_filters_expired_and_body_only_company_mentions() -> None:
    catalog = {
        "count": 47,
        "routes": [
            _catalog_route(
                "/eastmoney/search/:keyword",
                "东方财富搜索",
                params=[{"name": "keyword", "required": True}],
            )
        ],
    }
    feed = {
        "items": [
            {
                "title": "贵州茅台发布经营数据",
                "summary": "公司披露最新数据",
                "link": "https://example.com/current",
                "published": datetime.now().astimezone().isoformat(),
            },
            {
                "title": "北交所公司行情汇总",
                "summary": "表格中包含贵州茅台等大量公司",
                "link": "https://example.com/table",
                "published": datetime.now().astimezone().isoformat(),
            },
            {
                "title": "贵州茅台历史新闻",
                "summary": "过期内容",
                "link": "https://example.com/old",
                "published": "2020-01-01T00:00:00+08:00",
            },
            {
                "title": "11136,贵州茅台",
                "summary": "错误泄漏的图表标签",
                "link": "https://example.com/chart-label",
                "published": datetime.now().astimezone().isoformat(),
            },
        ],
        "errors": [],
        "_cached": False,
    }
    with (
        patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=catalog),
        patch("api.v1.endpoints._rss_reader.read_feed", return_value=feed),
    ):
        result = search_financial_news(
            "贵州茅台最新消息",
            topic="company",
            subjects=["贵州茅台"],
            days=30,
            fallback_to_web=False,
        )

    assert {item["link"] for item in result["items"]} == {
        "https://example.com/current",
        "https://example.com/table",
    }
    assert all(item["semantic_status"] == "model_required" for item in result["items"])
    assert result["days"] == 30
    assert any("过滤 1 条过期" in warning for warning in result["warnings"])

def test_semantic_rss_empty_success_is_distinct_from_failed_web_fallback() -> None:
    catalog = {
        "count": 47,
        "routes": [_catalog_route("/cls/telegraph/:category?", "财联社电报")],
    }
    fallback = {
        "success": False,
        "provider": "none",
        "results": [],
        "errors": ["all providers failed"],
    }
    with (
        patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=catalog),
        patch("api.v1.endpoints._rss_reader.read_feed", return_value={"items": [], "errors": []}),
        patch("src.tools.websearch.websearch", return_value=fallback),
    ):
        result = search_financial_news("市场发生了什么", topic="market")

    # The RSS route itself was reached successfully and legitimately returned
    # zero rows.  The separate web fallback failed; neither fact may overwrite
    # the other or turn a valid empty feed into an upstream transport failure.
    assert result["success"] is True
    assert result["source"] == "RSSHub"
    assert result["fallback_attempted"] is True
    assert result["fallback_used"] is False
    assert result["fallback_recommended"] is True

def test_semantic_rss_rejects_empty_query_and_invalid_topic() -> None:
    with pytest.raises(ValueError, match="query"):
        search_financial_news("   ")
    with pytest.raises(ValueError, match="topic"):
        search_financial_news("贵州茅台", topic="other")

def test_capital_flow_normalizes_units_and_window_observations() -> None:
    direct = pd.DataFrame(
        [
            {"date": pd.Timestamp("2026-07-14").date(), "main_net_inflow": 10, "main_net_inflow_pct": 1.0},
            {"date": pd.Timestamp("2026-07-15").date(), "main_net_inflow": -3, "main_net_inflow_pct": -0.5},
        ]
    )
    direct.attrs["history_transport"] = "curl_cffi"

    with (
        patch("src.tools.get_stock_capital_flow.cached_call", return_value=(direct, False)),
        patch("src.tools.get_stock_capital_flow._is_stale", return_value=(False, None)),
    ):
        result = get_stock_capital_flow("600519", days=20)

    assert result["success"] is True
    assert result["fallback_used"] is False
    assert result["item_count"] == 2
    assert result["summary"]["main_net_inflow_5d"] == 7
    assert result["summary"]["observations_5d"] == 2
    assert result["summary"]["windows"]["5d"]["complete_window"] is False
    assert result["amount_unit"] == "元"
    assert result["ratio_unit"] == "%"

def test_capital_flow_recognizes_bse_920_codes() -> None:
    assert _market_for("920000") == "bj"
    assert _market_for("600519") == "sh"
    assert _market_for("000001") == "sz"

def test_sector_flow_paginates_and_preserves_true_money_flow_fields() -> None:
    page_one = [
        {
            "f12": "BK1",
            "f14": "流入行业",
            "f3": 1.2,
            "f62": 100,
            "f184": 2.0,
            "f66": 60,
            "f72": 40,
            "f78": -20,
            "f84": -80,
            "f124": 1784180000,
        },
    ]
    page_two = [
        {
            "f12": "BK2",
            "f14": "流出行业",
            "f3": -1.2,
            "f62": -90,
            "f184": -3.0,
            "f66": -50,
            "f72": -40,
            "f78": 10,
            "f84": 80,
            "f124": 1784180000,
        },
    ]

    def fake_page(params, page):
        return (page_one, 101) if page == 1 else (page_two, 101)

    with patch("src.tools.get_sector_flow._request_page", side_effect=fake_page):
        records = fetch_all_sector_flow("industry", "today")

    assert len(records) == 2
    assert records[0]["main_net_inflow"] == 100
    assert records[0]["super_large_net_inflow"] == 60
    assert records[1]["main_net_inflow"] == -90
    assert records[1]["main_flow_rank"] == 2

def test_sector_flow_never_substitutes_price_performance_for_money_flow() -> None:
    with patch("src.tools.get_sector_flow.cached_call", side_effect=RuntimeError("upstream down")):
        result = get_sector_flow(type="industry", period="today", top_n=5)

    assert result["success"] is False
    assert result["inflow_top"] == []
    assert result["outflow_top"] == []
    assert result["fallback_used"] is False

def test_consensus_total_failure_has_unknown_freshness_without_data_time() -> None:
    with (
        patch("src.tools.get_consensus_estimates._forecast", side_effect=RuntimeError("upstream down")),
        patch("src.tools.get_consensus_estimates._detail", side_effect=RuntimeError("upstream down")),
    ):
        result = get_consensus_estimates("600519")

    assert result["success"] is False
    assert result["coverage_available"] is False
    assert result["is_stale"] is None
    assert len(result["errors"]) == 2
