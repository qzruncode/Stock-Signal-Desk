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


def test_peer_result_is_bounded_but_preserves_total_count() -> None:
    rows = [
        {
            "CORRE_SECURITY_CODE": f"{index:06d}",
            "CORRE_SECURITY_NAME": f"peer-{index}",
            "TOTAL_COUNT": 30,
            "PAIMING": index + 1,
            "REPORT_DATE": "2025-12-31",
            "PEG": index / 10,
            "PE_TTM": 10 + index,
            "PB_MRQ": 2 + index / 10,
        }
        for index in range(15)
    ]
    rows.append(
        {
            "CORRE_SECURITY_CODE": "600519",
            "CORRE_SECURITY_NAME": "贵州茅台",
            "TOTAL_COUNT": 30,
            "PAIMING": 20,
            "REPORT_DATE": "2025-12-31",
            "PEG": 1.5,
            "PE_TTM": 20,
            "PB_MRQ": 5,
        }
    )
    with (
        patch("src.tools.get_peer_comparison._request_rows", return_value=rows),
        patch("src.tools.get_peer_comparison.cached_call", side_effect=lambda _, fn, **__: (fn(), False)),
    ):
        result = get_peer_comparison("600519", dimension="valuation")

    bucket = result["dimensions"]["valuation"]
    assert result["success"] is True
    assert bucket["sample_size"] == 30
    assert bucket["target_rank"] == 20
    assert len(bucket["top_peers"]) == 10


def test_websearch_is_generic_and_preserves_the_original_query() -> None:
    query = "how to repair a mechanical keyboard stabilizer"

    assert _engine_query(query) == query
    assert _provider_order(query) == ["firecrawl", "exa", "parallel"]


def test_websearch_does_not_spend_remote_fallback_when_local_search_succeeds() -> None:
    local = {
        "provider": "firecrawl_searxng",
        "success": True,
        "skipped": False,
        "error": None,
        "results": [
            {
                "title": f"Result {index}",
                "url": f"https://example.com/{index}",
                "snippet": "body",
                "search_provider": "firecrawl_searxng",
            }
            for index in range(3)
        ],
    }
    with (
        patch("src.tools.websearch._firecrawl_search", return_value=local),
        patch("src.tools.websearch._exa_search") as exa,
        patch("src.tools.websearch._parallel_search") as parallel,
    ):
        result = websearch("arbitrary topic", num_results=8)

    assert result["success"] is True
    assert result["provider"] == "firecrawl_searxng"
    assert result["resolved_query"] == "arbitrary topic"
    assert result["fallback_used"] is False
    exa.assert_not_called()
    parallel.assert_not_called()


def test_websearch_uses_exa_only_after_local_search_returns_no_results() -> None:
    local_failed = {
        "provider": "firecrawl_searxng",
        "success": False,
        "skipped": False,
        "error": "no results",
        "results": [],
    }
    exa_result = {
        "provider": "exa",
        "success": True,
        "skipped": False,
        "error": None,
        "output": "Title: Two\nURL: https://two.example",
        "results": [{"title": "Two", "url": "https://two.example", "snippet": "", "search_provider": "exa"}],
    }
    with (
        patch("src.tools.websearch._firecrawl_search", return_value=local_failed),
        patch("src.tools.websearch._exa_search", return_value=exa_result),
        patch("src.tools.websearch._parallel_search") as parallel,
    ):
        result = websearch("anything", num_results=8)

    assert result["success"] is True
    assert result["provider"] == "exa"
    assert result["fallback_used"] is True
    assert [attempt["provider"] for attempt in result["attempts"]] == ["firecrawl_searxng", "exa"]
    assert result["output"].startswith("Title: Two")
    parallel.assert_not_called()


def test_websearch_uses_parallel_only_after_local_and_exa_fail() -> None:
    local_failed = {
        "provider": "firecrawl_searxng",
        "success": False,
        "skipped": False,
        "error": "local unavailable",
        "results": [],
    }
    exa_failed = {
        "provider": "exa",
        "success": False,
        "skipped": False,
        "error": "exa unavailable",
        "results": [],
        "output": "",
    }
    parallel_result = {
        "provider": "parallel",
        "success": True,
        "skipped": False,
        "error": None,
        "results": [],
        "output": "Title: Result\nURL: https://example.com",
    }
    with (
        patch("src.tools.websearch._firecrawl_search", return_value=local_failed),
        patch("src.tools.websearch._exa_search", return_value=exa_failed),
        patch("src.tools.websearch._parallel_search", return_value=parallel_result),
    ):
        result = websearch("anything")

    assert result["success"] is True
    assert result["provider"] == "parallel"
    assert result["fallback_used"] is True
    assert [attempt["provider"] for attempt in result["attempts"]] == [
        "firecrawl_searxng",
        "exa",
        "parallel",
    ]


def test_firecrawl_search_passes_original_query_to_local_v2_search() -> None:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "success": True,
        "data": {"web": [{"title": "Result", "url": "https://example.com", "description": "Body"}]},
    }
    query = "明天啥天气？！ exact phrase"

    with patch("src.tools.websearch.httpx.post", return_value=response) as post:
        result = _firecrawl_search(query, limit=7)

    assert result["success"] is True
    assert post.call_args.kwargs["json"]["query"] == query
    assert post.call_args.kwargs["json"]["limit"] == 7
    assert post.call_args.args[0].endswith("/v2/search")


def test_firecrawl_search_can_crawl_result_content_in_same_request() -> None:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "success": True,
        "data": {
            "web": [
                {
                    "title": "五洲新春人形机器人丝杠已送样",
                    "url": "https://example.com/2025/03/05/a",
                    "description": "摘要",
                    "markdown": "2025-03-05 五洲新春人形机器人丝杠已向客户送样。",
                    "metadata": {"publishedTime": "2025-03-05T08:00:00+08:00"},
                }
            ]
        },
    }

    with patch("src.tools.websearch.httpx.post", return_value=response) as post:
        result = _firecrawl_search("人形机器人送样", limit=7, include_content=True)

    payload = post.call_args.kwargs["json"]
    assert payload["scrapeOptions"]["formats"] == ["markdown"]
    assert result["results"][0]["content_text"].startswith("2025-03-05")
    assert result["results"][0]["published_date"].startswith("2025-03-05")


def test_websearch_include_content_survives_context_compaction() -> None:
    local = {
        "provider": "firecrawl_searxng",
        "success": True,
        "skipped": False,
        "error": None,
        "results": [
            {
                "title": "Result",
                "url": "https://example.com/a",
                "snippet": "body",
                "content_text": "company evidence",
                "search_provider": "firecrawl_searxng",
            }
        ],
    }
    with patch("src.tools.websearch._firecrawl_search", return_value=local):
        result = websearch(
            "human robot evidence",
            num_results=8,
            context_max_characters=4000,
            include_content=True,
        )

    assert result["content_requested"] is True
    assert result["content_result_count"] == 1
    assert result["results"][0]["content_text"] == "company evidence"


def test_websearch_normalizes_mixed_published_timezones() -> None:
    local = {
        "provider": "firecrawl_searxng",
        "success": True,
        "skipped": False,
        "error": None,
        "results": [
            {
                "title": "Naive",
                "url": "https://example.com/naive",
                "snippet": "body",
                "published_date": "2025-03-20 08:51:32",
            },
            {
                "title": "Aware",
                "url": "https://example.com/aware",
                "snippet": "body",
                "published_date": "2025-03-21T08:51:32+08:00",
            },
        ],
    }
    with patch("src.tools.websearch._firecrawl_search", return_value=local):
        result = websearch("mixed timezone dates")

    assert result["success"] is True
    assert result["latest_published_date"].startswith("2025-03-21")


def test_opencode_mcp_parser_supports_json_and_sse() -> None:
    payload = '{"result":{"content":[{"type":"text","text":"found"}]}}'

    assert _mcp_text(payload) == "found"
    assert _mcp_text(f"event: message\ndata: {payload}\n\n") == "found"


def test_exa_uses_its_own_key_and_opencode_arguments() -> None:
    with (
        patch.dict(os.environ, {"EXA_API_KEY": "key +/?"}, clear=True),
        patch("src.tools.websearch._mcp_call", return_value="Title: X\nURL: https://example.com") as call,
    ):
        result = _exa_search(
            "universal query",
            limit=5,
            livecrawl="preferred",
            search_type="deep",
            context_max_characters=9000,
        )

    assert result["success"] is True
    assert "exaApiKey=key+%2B%2F%3F" in call.call_args.args[0]
    assert call.call_args.args[1] == "web_search_exa"
    assert call.call_args.args[2]["query"] == "universal query"
    assert call.call_args.args[2]["numResults"] == 5


def test_webfetch_uses_open_http_path_before_any_fallback() -> None:
    direct = {
        "provider": "http",
        "success": True,
        "skipped": False,
        "error": None,
        "content": "正文",
        "attachments": None,
        "final_url": "https://example.com/a",
        "title": "标题",
        "content_type": "text/html",
        "extraction_method": "direct_http",
    }
    with (
        patch("src.tools.webfetch._validate_public_url"),
        patch("src.tools.webfetch._http_fetch", return_value=direct) as http_fetch,
        patch("src.tools.webfetch._scrapling_fetch") as scrapling,
        patch("src.tools.webfetch._firecrawl_fetch") as firecrawl,
    ):
        result = fetch_url("https://example.com/a")

    assert result["provider"] == "http"
    assert result["fallback_used"] is False
    http_fetch.assert_called_once()
    scrapling.assert_not_called()
    firecrawl.assert_not_called()


def test_webfetch_falls_back_in_transport_order() -> None:
    failed = {"provider": "http", "success": False, "skipped": False, "error": "blocked"}
    static = {
        "provider": "scrapling",
        "success": True,
        "skipped": False,
        "error": None,
        "content": "正文",
        "attachments": None,
        "final_url": "https://example.com/a",
        "title": "标题",
        "content_type": "text/html",
        "extraction_method": "scrapling_http",
    }
    with (
        patch("src.tools.webfetch._validate_public_url"),
        patch("src.tools.webfetch._http_fetch", return_value=failed),
        patch("src.tools.webfetch._scrapling_fetch", return_value=static) as scrapling,
        patch("src.tools.webfetch._firecrawl_fetch") as firecrawl,
    ):
        result = fetch_url("https://example.com/a")

    assert result["provider"] == "scrapling"
    assert result["fallback_used"] is True
    assert result["warnings"] == ["blocked"]
    assert [item["provider"] for item in result["attempts"]] == ["http", "scrapling"]
    assert scrapling.call_args.kwargs["browser"] is False
    firecrawl.assert_not_called()


def test_webfetch_waf_challenge_goes_directly_to_real_browser() -> None:
    challenge = {
        "provider": "http",
        "success": False,
        "skipped": False,
        "error": "页面返回了 WAF 加密挑战而非正文",
        "failure_kind": "challenge",
    }
    rendered = {
        "provider": "patchright",
        "success": True,
        "skipped": False,
        "error": None,
        "content": "# 正文\n\n浏览器渲染后的完整内容",
        "attachments": None,
        "final_url": "https://example.com/a",
        "title": "标题",
        "content_type": "text/html",
        "extraction_method": "patchright_browser+semantic_dom",
    }
    with (
        patch("src.tools.webfetch._validate_public_url"),
        patch("src.tools.webfetch._http_fetch", return_value=challenge),
        patch("src.tools.webfetch._scrapling_fetch", return_value=rendered) as scrapling,
        patch("src.tools.webfetch._firecrawl_fetch") as firecrawl,
    ):
        result = fetch_url("https://example.com/a")

    assert result["success"] is True
    assert result["provider"] == "patchright"
    assert [item["provider"] for item in result["attempts"]] == ["http", "patchright"]
    assert scrapling.call_args.kwargs["browser"] is True
    firecrawl.assert_not_called()


def test_webfetch_keeps_complete_rendered_dashboard_despite_link_density() -> None:
    challenge = {
        "provider": "http",
        "success": False,
        "skipped": False,
        "error": "动态占位内容",
        "failure_kind": "challenge",
    }
    dashboard = {
        "provider": "patchright",
        "success": True,
        "skipped": False,
        "error": None,
        "content": "实时行情和成交数据\n" * 200,
        "attachments": None,
        "final_url": "https://example.com/quote",
        "title": "行情",
        "content_type": "text/html",
        "extraction_method": "patchright_browser+full_page_fallback",
        "quality_warning": "页面正文链接密度过高，继续尝试主内容抓取器",
    }
    with (
        patch("src.tools.webfetch._validate_public_url"),
        patch("src.tools.webfetch._http_fetch", return_value=challenge),
        patch("src.tools.webfetch._scrapling_fetch", return_value=dashboard),
        patch("src.tools.webfetch._firecrawl_fetch") as firecrawl,
    ):
        result = fetch_url("https://example.com/quote")

    assert result["provider"] == "patchright"
    assert "实时行情和成交数据" in result["content"]
    firecrawl.assert_not_called()


def test_webfetch_rejects_encrypted_waf_payload_even_when_http_is_200() -> None:
    payload = '{"\\_waf\\_bd8ce2ce37":"' + ("Aa09_-" * 1000) + '"}'

    assert _challenge_reason(payload, "application/json") == "页面返回了 WAF 加密挑战而非正文"


def test_webfetch_rejects_unrendered_javascript_placeholder_page() -> None:
    placeholders = "\n".join(["-" for _ in range(20)])
    shell = "页面框架已经返回但业务数据仍未完成加载。" * 8
    html = f"<html><body><h1>行情</h1><div>加载中</div><div>数据加载中...</div>{shell}{placeholders}</body></html>"

    assert _challenge_reason(html, "text/html") == "页面返回了尚未加载完成的 JavaScript 动态占位内容"


def test_webfetch_semantic_article_beats_long_comment_container() -> None:
    html = (
        """
    <html><head><title>公司公告正文</title>
    <meta name="description" content="公司公告正文：核心经营数据保持增长"></head>
    <body><article><h1>公司公告正文</h1><p>核心经营数据保持增长，现金流同步改善。</p></article>
    <div class="article-content comments">评论区噪声 """
        + ("很长的评论 " * 300)
        + """</div></body></html>
    """
    )

    content, method, metadata = _extract_html(html, "markdown")

    assert method == "semantic_dom"
    assert "核心经营数据保持增长" in content
    assert "很长的评论" not in content
    assert metadata["title"] == "公司公告正文"


def test_webfetch_keeps_visible_dashboard_when_article_extractor_is_too_lossy() -> None:
    rows = "".join(f"<tr><td>指标{i}的详细说明文字</td><td>{i * 10}</td></tr>" for i in range(300))
    html = f"""
    <html><head><title>数据看板</title></head><body>
    <nav>首页 产品 设置</nav><table>{rows}</table><footer>版权信息</footer>
    </body></html>
    """

    with patch("trafilatura.extract", return_value="被过度压缩的摘要" * 12):
        content, method, _ = _extract_html(html, "text")

    assert method == "full_page_fallback"
    assert "指标79" in content


def test_webfetch_blocks_private_ip() -> None:
    with pytest.raises(ValueError, match="SSRF"):
        fetch_url("http://127.0.0.1/admin")


def test_webfetch_open_http_contract_retries_cloudflare_with_honest_user_agent() -> None:
    challenge = Mock(status_code=403, headers={"cf-mitigated": "challenge"})
    success = Mock(
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
        content=("<html><title>Page</title><body>" + "useful content " * 20 + "</body></html>").encode(),
        encoding="utf-8",
        url="https://example.com/page",
    )
    success.raise_for_status.return_value = None
    client = Mock()
    client.get.side_effect = [challenge, success]
    context = Mock()
    context.__enter__ = Mock(return_value=client)
    context.__exit__ = Mock(return_value=False)

    with (
        patch("src.tools.webfetch._validate_public_url"),
        patch("src.tools.webfetch.httpx.Client", return_value=context),
    ):
        result = _http_fetch("https://example.com/page", "markdown", 30)

    assert result["success"] is True
    assert client.get.call_args_list[1].kwargs["headers"]["User-Agent"] == "opencode"
    assert "text/markdown;q=1.0" in _accept_header_for("markdown")


def test_webfetch_open_http_contract_supports_images_and_five_mb_cap() -> None:
    image_response = Mock(
        status_code=200,
        headers={"content-type": "image/png"},
        content=b"\x89PNG",
        encoding=None,
        url="https://example.com/image.png",
    )
    image_response.raise_for_status.return_value = None
    oversized_response = Mock(
        status_code=200,
        headers={"content-type": "text/plain", "content-length": str(MAX_RESPONSE_SIZE + 1)},
        content=b"small",
        encoding="utf-8",
        url="https://example.com/large.txt",
    )
    oversized_response.raise_for_status.return_value = None

    client = Mock()
    client.get.side_effect = [image_response, oversized_response]
    context = Mock()
    context.__enter__ = Mock(return_value=client)
    context.__exit__ = Mock(return_value=False)

    with (
        patch("src.tools.webfetch._validate_public_url"),
        patch("src.tools.webfetch.httpx.Client", return_value=context),
    ):
        image_result = _http_fetch("https://example.com/image.png", "markdown", 30)
        oversized_result = _http_fetch("https://example.com/large.txt", "text", 30)

    assert image_result["success"] is True
    assert image_result["attachments"][0]["url"].startswith("data:image/png;base64,")
    assert oversized_result["success"] is False
    assert "5MB" in oversized_result["error"]


def test_webfetch_open_http_contract_parses_pdf_instead_of_decoding_binary() -> None:
    pdf_response = Mock(
        status_code=200,
        headers={"content-type": "application/pdf"},
        content=b"%PDF-1.7 test",
        encoding=None,
        url="https://example.com/report.pdf",
    )
    pdf_response.raise_for_status.return_value = None
    client = Mock()
    client.get.return_value = pdf_response
    context = Mock()
    context.__enter__ = Mock(return_value=client)
    context.__exit__ = Mock(return_value=False)

    with (
        patch("src.tools.webfetch._validate_public_url"),
        patch("src.tools.webfetch.httpx.Client", return_value=context),
        patch("src.tools.webfetch._convert_document", return_value=("# 年报\n\n正文", "markitdown")),
    ):
        result = _http_fetch("https://example.com/report.pdf", "markdown", 30)

    assert result["success"] is True
    assert result["content"] == "# 年报\n\n正文"
    assert result["extraction_method"] == "markitdown"
