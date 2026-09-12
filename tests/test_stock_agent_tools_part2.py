# -*- coding: utf-8 -*-
"""Offline contract tests for the Stock Agent's semantic and fallback tools."""

from __future__ import annotations

import os
from unittest.mock import Mock, patch

import pytest

import src.tools.source_operations as source_operations

from src.tools.webfetch import (
    MAX_RESPONSE_SIZE,
    _accept_header_for,
    _challenge_reason,
    _extract_html,
    _http_fetch,
    _https_upgrade_url,
    _scrapling_failure_kind,
    fetch_url,
)
from src.tools.websearch import (
    _engine_query,
    _exa_search,
    _firecrawl_search,
    _mcp_text,
    _provider_order,
    _redact_secret_text,
    websearch,
)


"""Focused test slice 2; shared fixtures remain local to this slice."""


def test_websearch_is_generic_and_preserves_the_original_query() -> None:
    query = "how to repair a mechanical keyboard stabilizer"

    assert _engine_query(query) == query
    assert _provider_order(query) == ["firecrawl", "exa", "parallel"]


def test_search_web_source_auto_reuses_the_existing_fallback_chain() -> None:
    expected = {
        "success": True,
        "provider": "exa",
        "fallback_used": True,
        "attempts": [{"provider": "firecrawl_searxng", "success": False}],
        "results": [{"title": "结果", "url": "https://example.com/result"}],
    }
    with patch.object(
        source_operations, "websearch", return_value=expected
    ) as fallback:
        result = source_operations.search_web_source(
            source_id="auto",
            query="最新产业进展",
            num_results=99,
            context_max_characters=100,
            livecrawl="fallback",
            search_type="auto",
        )

    assert result is expected
    fallback.assert_called_once_with(
        query="最新产业进展",
        num_results=20,
        context_max_characters=1_000,
        livecrawl="fallback",
        search_type="auto",
    )


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
        "results": [
            {
                "title": "Two",
                "url": "https://two.example",
                "snippet": "",
                "search_provider": "exa",
            }
        ],
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
    assert [attempt["provider"] for attempt in result["attempts"]] == [
        "firecrawl_searxng",
        "exa",
    ]
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
        "data": {
            "web": [
                {"title": "Result", "url": "https://example.com", "description": "Body"}
            ]
        },
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


def test_remote_search_errors_redact_credentials_from_provider_urls(
    monkeypatch,
) -> None:
    secret = "exa-secret-1234"
    monkeypatch.setenv("EXA_API_KEY", secret)

    with patch(
        "src.tools.websearch._mcp_call",
        side_effect=RuntimeError(
            f"request failed: https://mcp.exa.ai/mcp?exaApiKey={secret}"
        ),
    ):
        result = _exa_search(
            "query",
            limit=5,
            livecrawl="fallback",
            search_type="auto",
            context_max_characters=None,
        )

    assert result["success"] is False
    assert secret not in result["error"]
    assert "exaApiKey=[REDACTED]" in result["error"]
    assert secret not in _redact_secret_text(
        f"Authorization: Bearer {secret}", (secret,)
    )


def test_exa_uses_its_own_key_and_opencode_arguments() -> None:
    with (
        patch.dict(os.environ, {"EXA_API_KEY": "key +/?"}, clear=True),
        patch(
            "src.tools.websearch._mcp_call",
            return_value="Title: X\nURL: https://example.com",
        ) as call,
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


def test_webfetch_upgrades_http_to_https_before_other_providers() -> None:
    failed = {
        "provider": "http",
        "success": False,
        "skipped": False,
        "error": "Empty reply from server",
        "failure_kind": "transport",
    }
    upgraded = {
        "provider": "http",
        "success": True,
        "skipped": False,
        "error": None,
        "content": "正文",
        "attachments": None,
        "final_url": "https://example.com/article",
        "title": "标题",
        "content_type": "text/html",
        "extraction_method": "direct_http",
    }
    with (
        patch("src.tools.webfetch._validate_public_url"),
        patch("src.tools.webfetch._http_fetch", side_effect=[failed, upgraded]) as http_fetch,
        patch("src.tools.webfetch._scrapling_fetch") as scrapling,
        patch("src.tools.webfetch._firecrawl_fetch") as firecrawl,
    ):
        result = fetch_url("http://example.com/article")

    assert _https_upgrade_url("http://example.com/article") == "https://example.com/article"
    assert [call.args[0] for call in http_fetch.call_args_list] == [
        "http://example.com/article",
        "https://example.com/article",
    ]
    assert result["provider"] == "http"
    assert result["fallback_used"] is True
    assert [item["url"] for item in result["attempts"]] == [
        "http://example.com/article",
        "https://example.com/article",
    ]
    scrapling.assert_not_called()
    firecrawl.assert_not_called()


def test_scrapling_tls_runtime_errors_are_marked_as_provider_unavailable() -> None:
    assert _scrapling_failure_kind(
        "TLS connect error: error:00000000:invalid library (0):OPENSSL_internal"
    ) == "provider_unavailable"
    assert _scrapling_failure_kind("connection reset by peer") == "transport"


def test_webfetch_falls_back_in_transport_order() -> None:
    failed = {
        "provider": "http",
        "success": False,
        "skipped": False,
        "error": "文档解析结果为空",
    }
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
    assert result["warnings"] == []
    assert [item["provider"] for item in result["attempts"]] == ["http", "scrapling"]
    assert result["attempts"][0]["error"] == "文档解析结果为空"
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
        patch(
            "src.tools.webfetch._scrapling_fetch", return_value=rendered
        ) as scrapling,
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

    assert (
        _challenge_reason(payload, "application/json")
        == "页面返回了 WAF 加密挑战而非正文"
    )


def test_webfetch_rejects_unrendered_javascript_placeholder_page() -> None:
    placeholders = "\n".join(["-" for _ in range(20)])
    shell = "页面框架已经返回但业务数据仍未完成加载。" * 8
    html = f"<html><body><h1>行情</h1><div>加载中</div><div>数据加载中...</div>{shell}{placeholders}</body></html>"

    assert (
        _challenge_reason(html, "text/html")
        == "页面返回了尚未加载完成的 JavaScript 动态占位内容"
    )


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
    rows = "".join(
        f"<tr><td>指标{i}的详细说明文字</td><td>{i * 10}</td></tr>" for i in range(300)
    )
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
