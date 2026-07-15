# -*- coding: utf-8 -*-
"""Offline contract tests for the Stock Agent's semantic and fallback tools."""

from __future__ import annotations

import os
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from src.tools.get_consensus_estimates import get_consensus_estimates
from src.tools.get_peer_comparison import get_peer_comparison
from src.tools.get_stock_capital_flow import get_stock_capital_flow
from src.tools.search_financial_news import _select_specs, search_financial_news
from src.tools.webfetch import MAX_RESPONSE_SIZE, _accept_header_for, _http_fetch, fetch_url
from src.tools.websearch import (
    _engine_query,
    _exa_search,
    _firecrawl_search,
    _mcp_text,
    _provider_order,
    websearch,
)


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


def test_semantic_rss_selector_can_choose_non_default_catalog_route() -> None:
    routes = [
        _catalog_route("/eastmoney/report/:category", "研究报告", params=[{
            "name": "category", "required": True, "options": [{"value": "stock", "label": "个股研报"}],
        }]),
        _catalog_route("/moodysmismicrosite/report/:industry?", "穆迪评级", params=[{
            "name": "industry", "required": False, "default": "全部", "options": [],
        }]),
    ]

    selected = _select_specs(routes, "穆迪评级报告", "research")

    assert selected[0][0] == "/moodysmismicrosite/report/:industry?"
    assert selected[0][1] == {"industry": "全部"}


def test_semantic_rss_normalizes_web_fallback_into_items() -> None:
    catalog = {
        "count": 47,
        "routes": [_catalog_route("/cls/telegraph/:category?", "财联社电报")],
    }
    fallback = {
        "success": True,
        "provider": "test_search",
        "results": [{
            "title": "贵州茅台最新公告",
            "url": "https://example.com/a",
            "snippet": "公告摘要",
            "source": "example.com",
            "published_date": "2026-07-15",
        }],
    }
    with patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=catalog), \
         patch("api.v1.endpoints._rss_reader.read_feed", return_value={"items": [], "errors": []}), \
         patch("src.tools.websearch.websearch", return_value=fallback):
        result = search_financial_news("贵州茅台最新公告", topic="announcement")

    assert result["success"] is True
    assert result["fallback_used"] is True
    assert result["source"] == "websearch/test_search"
    assert result["items"][0]["source_type"] == "websearch"
    assert result["items"][0]["link"] == "https://example.com/a"


def test_semantic_rss_rejects_empty_query_and_invalid_topic() -> None:
    with pytest.raises(ValueError, match="query"):
        search_financial_news("   ")
    with pytest.raises(ValueError, match="topic"):
        search_financial_news("贵州茅台", topic="other")


def test_capital_flow_uses_bounded_direct_fallback() -> None:
    direct = pd.DataFrame([
        {"日期": "2026-07-14", "主力净流入-净额": "10"},
        {"日期": "2026-07-15", "主力净流入-净额": "-3"},
    ])

    def fake_cached_call(key, fn, **kwargs):
        if key.startswith("stock_capital_flow:direct"):
            return direct, False
        raise RuntimeError("akshare disconnected")

    with patch("src.tools.get_stock_capital_flow.cached_call", side_effect=fake_cached_call):
        result = get_stock_capital_flow("600519", days=20)

    assert result["success"] is True
    assert result["fallback_used"] is True
    assert result["item_count"] == 2
    assert result["summary"]["main_net_inflow_5d"] == 7
    assert "AKShare 资金流接口失败" in result["errors"][0]


def test_consensus_total_failure_is_not_marked_fresh() -> None:
    with patch("src.tools.get_consensus_estimates._forecast", side_effect=RuntimeError("upstream down")):
        result = get_consensus_estimates("600519")

    assert result["success"] is False
    assert result["coverage_available"] is False
    assert result["is_stale"] is True
    assert len(result["errors"]) == 2


def test_peer_result_is_bounded_but_preserves_total_count() -> None:
    rows = pd.DataFrame([{"股票代码": f"{index:06d}", "市盈率": index} for index in range(30)])
    with patch("src.tools.get_peer_comparison.cached_call", return_value=(rows, False)):
        result = get_peer_comparison("600519", dimension="valuation")

    bucket = result["dimensions"]["valuation"]
    assert result["success"] is True
    assert bucket["item_count"] == 30
    assert len(bucket["items"]) == 12


def test_websearch_is_generic_and_preserves_the_original_query() -> None:
    query = "how to repair a mechanical keyboard stabilizer"

    assert _engine_query(query) == query
    assert _provider_order(query) == ["firecrawl", "exa", "parallel"]


def test_websearch_does_not_spend_remote_fallback_when_local_search_succeeds() -> None:
    local = {
        "provider": "firecrawl_searxng", "success": True, "skipped": False, "error": None,
        "results": [
            {"title": f"Result {index}", "url": f"https://example.com/{index}", "snippet": "body", "search_provider": "firecrawl_searxng"}
            for index in range(3)
        ],
    }
    with patch("src.tools.websearch._firecrawl_search", return_value=local), \
         patch("src.tools.websearch._exa_search") as exa, \
         patch("src.tools.websearch._parallel_search") as parallel:
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
        "provider": "exa", "success": True, "skipped": False, "error": None,
        "output": "Title: Two\nURL: https://two.example",
        "results": [{"title": "Two", "url": "https://two.example", "snippet": "", "search_provider": "exa"}],
    }
    with patch("src.tools.websearch._firecrawl_search", return_value=local_failed), \
         patch("src.tools.websearch._exa_search", return_value=exa_result), \
         patch("src.tools.websearch._parallel_search") as parallel:
        result = websearch("anything", num_results=8)

    assert result["success"] is True
    assert result["provider"] == "exa"
    assert result["fallback_used"] is True
    assert [attempt["provider"] for attempt in result["attempts"]] == ["firecrawl_searxng", "exa"]
    assert result["output"].startswith("Title: Two")
    parallel.assert_not_called()


def test_websearch_uses_parallel_only_after_local_and_exa_fail() -> None:
    local_failed = {
        "provider": "firecrawl_searxng", "success": False, "skipped": False,
        "error": "local unavailable", "results": [],
    }
    exa_failed = {
        "provider": "exa", "success": False, "skipped": False,
        "error": "exa unavailable", "results": [], "output": "",
    }
    parallel_result = {
        "provider": "parallel", "success": True, "skipped": False, "error": None,
        "results": [], "output": "Title: Result\nURL: https://example.com",
    }
    with patch("src.tools.websearch._firecrawl_search", return_value=local_failed), \
         patch("src.tools.websearch._exa_search", return_value=exa_failed), \
         patch("src.tools.websearch._parallel_search", return_value=parallel_result):
        result = websearch("anything")

    assert result["success"] is True
    assert result["provider"] == "parallel"
    assert result["fallback_used"] is True
    assert [attempt["provider"] for attempt in result["attempts"]] == [
        "firecrawl_searxng", "exa", "parallel",
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


def test_opencode_mcp_parser_supports_json_and_sse() -> None:
    payload = '{"result":{"content":[{"type":"text","text":"found"}]}}'

    assert _mcp_text(payload) == "found"
    assert _mcp_text(f"event: message\ndata: {payload}\n\n") == "found"


def test_exa_uses_its_own_key_and_opencode_arguments() -> None:
    with patch.dict(os.environ, {"EXA_API_KEY": "key +/?"}, clear=True), \
         patch("src.tools.websearch._mcp_call", return_value="Title: X\nURL: https://example.com") as call:
        result = _exa_search(
            "universal query", limit=5, livecrawl="preferred", search_type="deep", context_max_characters=9000,
        )

    assert result["success"] is True
    assert "exaApiKey=key+%2B%2F%3F" in call.call_args.args[0]
    assert call.call_args.args[1] == "web_search_exa"
    assert call.call_args.args[2]["query"] == "universal query"
    assert call.call_args.args[2]["numResults"] == 5


def test_webfetch_uses_open_http_path_before_any_fallback() -> None:
    direct = {
        "provider": "http", "success": True, "skipped": False, "error": None,
        "content": "正文", "attachments": None, "final_url": "https://example.com/a",
        "title": "标题", "content_type": "text/html", "extraction_method": "direct_http",
    }
    with patch("src.tools.webfetch._validate_public_url"), \
         patch("src.tools.webfetch._http_fetch", return_value=direct) as http_fetch, \
         patch("src.tools.webfetch._scrapling_fetch") as scrapling, \
         patch("src.tools.webfetch._firecrawl_fetch") as firecrawl:
        result = fetch_url("https://example.com/a")

    assert result["provider"] == "http"
    assert result["fallback_used"] is False
    http_fetch.assert_called_once()
    scrapling.assert_not_called()
    firecrawl.assert_not_called()


def test_webfetch_falls_back_in_transport_order() -> None:
    failed = {"provider": "http", "success": False, "skipped": False, "error": "blocked"}
    static = {
        "provider": "scrapling", "success": True, "skipped": False, "error": None,
        "content": "正文", "attachments": None, "final_url": "https://example.com/a",
        "title": "标题", "content_type": "text/html", "extraction_method": "scrapling_http",
    }
    with patch("src.tools.webfetch._validate_public_url"), \
         patch("src.tools.webfetch._http_fetch", return_value=failed), \
         patch("src.tools.webfetch._scrapling_fetch", return_value=static) as scrapling, \
         patch("src.tools.webfetch._firecrawl_fetch") as firecrawl:
        result = fetch_url("https://example.com/a")

    assert result["provider"] == "scrapling"
    assert result["fallback_used"] is True
    assert result["warnings"] == ["blocked"]
    assert [item["provider"] for item in result["attempts"]] == ["http", "scrapling"]
    assert scrapling.call_args.kwargs["browser"] is False
    firecrawl.assert_not_called()


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

    with patch("src.tools.webfetch._validate_public_url"), \
         patch("src.tools.webfetch.httpx.Client", return_value=context):
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

    with patch("src.tools.webfetch._validate_public_url"), \
         patch("src.tools.webfetch.httpx.Client", return_value=context):
        image_result = _http_fetch("https://example.com/image.png", "markdown", 30)
        oversized_result = _http_fetch("https://example.com/large.txt", "text", 30)

    assert image_result["success"] is True
    assert image_result["attachments"][0]["url"].startswith("data:image/png;base64,")
    assert oversized_result["success"] is False
    assert "5MB" in oversized_result["error"]
