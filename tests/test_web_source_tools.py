from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tools.web_source_tools import (
    read_web_firecrawl,
    read_web_patchright,
    search_web_exa,
    search_web_firecrawl_searxng,
)


def test_firecrawl_search_is_one_explicit_source_without_provider_fallback() -> None:
    raw = {
        "provider": "firecrawl_searxng",
        "success": True,
        "duration_ms": 12,
        "results": [
            {
                "title": "机器人供应链",
                "url": "https://example.com/robotics",
                "snippet": "产业链动态",
                "source": "example.com",
                "published_date": "2026-08-08T09:00:00+08:00",
            }
        ],
    }
    with (
        patch("src.tools.web_source_tools._firecrawl_search", return_value=raw) as firecrawl,
        patch("src.tools.web_source_tools._exa_search") as exa,
        patch("src.tools.web_source_tools._parallel_search") as parallel,
    ):
        result = search_web_firecrawl_searxng("人形机器人产业链", numResults=5)

    firecrawl.assert_called_once_with("人形机器人产业链", limit=5, include_content=False)
    exa.assert_not_called()
    parallel.assert_not_called()
    assert result["success"] is True
    assert result["provider"] == "firecrawl_searxng"
    assert result["source_scope"] == "single_provider_web_search"
    assert result["fallback_used"] is False
    assert result["data_time"] == "2026-08-08T09:00:00+08:00"
    assert result["data_time_provenance"] == "source"


def test_exa_failure_is_an_observation_not_a_hidden_provider_switch() -> None:
    raw = {
        "provider": "exa",
        "success": False,
        "duration_ms": 7,
        "error": "provider unavailable",
        "results": [],
        "output": "",
    }
    with (
        patch("src.tools.web_source_tools._exa_search", return_value=raw) as exa,
        patch("src.tools.web_source_tools._firecrawl_search") as firecrawl,
        patch("src.tools.web_source_tools._parallel_search") as parallel,
    ):
        result = search_web_exa("arbitrary long-tail topic")

    exa.assert_called_once()
    firecrawl.assert_not_called()
    parallel.assert_not_called()
    assert result["success"] is False
    assert result["errors"] == ["provider unavailable"]
    assert result["fallback_used"] is False
    assert result["attempts"] == [{
        "provider": "exa",
        "success": False,
        "skipped": False,
        "duration_ms": 7,
        "error": "provider unavailable",
    }]


def test_patchright_reader_does_not_switch_to_http_or_firecrawl() -> None:
    raw = {
        "provider": "patchright",
        "success": True,
        "duration_ms": 31,
        "content": "页面正文",
        "attachments": None,
        "final_url": "https://example.com/article",
        "title": "文章",
        "content_type": "text/html",
        "content_time": "2026-08-08T08:00:00+08:00",
        "extraction_method": "patchright_browser+article",
    }
    with (
        patch("src.tools.web_source_tools._validate_public_url") as validate_url,
        patch("src.tools.web_source_tools._scrapling_fetch", return_value=raw) as patchright,
        patch("src.tools.web_source_tools._http_fetch") as http,
        patch("src.tools.web_source_tools._firecrawl_fetch") as firecrawl,
    ):
        result = read_web_patchright("https://example.com/article", timeout=45)

    validate_url.assert_called_once_with("https://example.com/article")
    patchright.assert_called_once_with(
        "https://example.com/article",
        "markdown",
        45,
        browser=True,
    )
    http.assert_not_called()
    firecrawl.assert_not_called()
    assert result["success"] is True
    assert result["provider"] == "patchright"
    assert result["source_scope"] == "single_provider_web_fetch"
    assert result["fallback_used"] is False
    assert result["data_time"] == "2026-08-08T08:00:00+08:00"
    assert result["data_time_provenance"] == "source"


def test_direct_reader_keeps_transport_time_out_of_data_time() -> None:
    raw = {
        "provider": "patchright",
        "success": True,
        "duration_ms": 31,
        "content": "页面正文",
        "attachments": None,
        "final_url": "https://example.com/article",
        "title": "文章",
        "content_type": "text/html",
        "extraction_method": "patchright_browser+article",
    }
    with (
        patch("src.tools.web_source_tools._validate_public_url"),
        patch("src.tools.web_source_tools._scrapling_fetch", return_value=raw),
    ):
        result = read_web_patchright("https://example.com/article")

    assert result["data_time"] is None
    assert result["data_time_provenance"] == "unavailable"
    assert result["freshness_unknown"] is True
    assert "抓取完成时间" in result["data_time_note"]


def test_direct_reader_extracts_a_clearly_labelled_source_time_from_content() -> None:
    raw = {
        "provider": "patchright",
        "success": True,
        "duration_ms": 31,
        "content": (
            "# 页面标题\n\n"
            "_时间：2026-05-06 03:29:04__来源：公开来源__编辑：编辑_\n\n"
            "正文内容。"
        ),
        "attachments": None,
        "final_url": "https://example.com/article",
        "title": "文章",
        "content_type": "text/html",
        "extraction_method": "patchright_browser+article",
    }
    with (
        patch("src.tools.web_source_tools._validate_public_url"),
        patch("src.tools.web_source_tools._scrapling_fetch", return_value=raw),
    ):
        result = read_web_patchright("https://example.com/article")

    assert result["data_time"] == "2026-05-06 03:29:04"
    assert result["data_time_provenance"] == "source"
    assert result["freshness_unknown"] is False
    assert result["data_time_note"] == "网页正文中明确标注的来源时间字段：时间。"


def test_direct_reader_does_not_infer_a_source_time_from_an_unlabelled_body_date() -> None:
    raw = {
        "provider": "patchright",
        "success": True,
        "duration_ms": 31,
        "content": "分析中提到 2026-05-06 的行业会议，但页面没有发布日期字段。",
        "attachments": None,
        "final_url": "https://example.com/article",
        "title": "文章",
        "content_type": "text/html",
        "extraction_method": "patchright_browser+article",
    }
    with (
        patch("src.tools.web_source_tools._validate_public_url"),
        patch("src.tools.web_source_tools._scrapling_fetch", return_value=raw),
    ):
        result = read_web_patchright("https://example.com/article")

    assert result["data_time"] is None
    assert result["data_time_provenance"] == "unavailable"
    assert result["freshness_unknown"] is True


def test_direct_reader_rejects_access_challenge_even_if_provider_reports_success() -> None:
    raw = {
        "provider": "firecrawl",
        "success": True,
        "duration_ms": 31,
        "content": (
            'appkey: "CF\\_APP\\_WAF"\n\n'
            "Access Verification\n\n"
            "For better experience, please slide to complete the verification process "
            "before accessing the web page."
        ),
        "attachments": None,
        "final_url": "https://example.com/article",
        "title": "Verification",
        "content_type": "text/markdown",
        "extraction_method": "firecrawl_main_content",
    }
    with (
        patch("src.tools.web_source_tools._validate_public_url"),
        patch("src.tools.web_source_tools._firecrawl_fetch", return_value=raw),
    ):
        result = read_web_firecrawl("https://example.com/article")

    assert result["success"] is False
    assert result["partial"] is False
    assert result["failure_kind"] == "challenge"
    assert result["errors"] == ["页面返回了访问验证而非正文"]
    assert result["attempts"][0]["success"] is False
    assert result["attempts"][0]["error"] == "页面返回了访问验证而非正文"


def test_direct_reader_rejects_invalid_url_before_provider_call() -> None:
    with (
        patch("src.tools.web_source_tools._validate_public_url", side_effect=ValueError("blocked")),
        patch("src.tools.web_source_tools._scrapling_fetch") as fetch,
    ):
        with pytest.raises(ValueError, match="blocked"):
            read_web_patchright("http://localhost:8000")
    fetch.assert_not_called()
