# -*- coding: utf-8 -*-
"""Offline contract tests for the Stock Agent's semantic and fallback tools."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

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


"""Focused test slice 3; shared fixtures remain local to this slice."""


def test_webfetch_open_http_contract_retries_cloudflare_with_honest_user_agent() -> (
    None
):
    challenge = Mock(status_code=403, headers={"cf-mitigated": "challenge"})
    success = Mock(
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
        content=(
            "<html><title>Page</title><body>"
            + "useful content " * 20
            + "</body></html>"
        ).encode(),
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
        headers={
            "content-type": "text/plain",
            "content-length": str(MAX_RESPONSE_SIZE + 1),
        },
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
        patch(
            "src.tools.webfetch._convert_document",
            return_value=("# 年报\n\n正文", "markitdown"),
        ),
    ):
        result = _http_fetch("https://example.com/report.pdf", "markdown", 30)

    assert result["success"] is True
    assert result["content"] == "# 年报\n\n正文"
    assert result["extraction_method"] == "markitdown"


@pytest.mark.parametrize(
    ("url", "headers", "body"),
    [
        (
            "https://example.com/download?id=report-1",
            {"content-type": "application/octet-stream"},
            b"%PDF-1.7 hidden filename",
        ),
        (
            "https://example.com/download?id=report-2",
            {
                "content-type": "application/octet-stream",
                "content-disposition": 'attachment; filename="report.pdf"',
            },
            b"not a real PDF body for this routing test",
        ),
    ],
)
def test_webfetch_detects_pdf_downloads_without_a_pdf_url_suffix(
    url: str,
    headers: dict[str, str],
    body: bytes,
) -> None:
    pdf_response = Mock(
        status_code=200,
        headers=headers,
        content=body,
        encoding=None,
        url=url,
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
        patch(
            "src.tools.webfetch._convert_document",
            return_value=("# 研报正文", "markitdown"),
        ) as convert,
    ):
        result = _http_fetch(url, "markdown", 30)

    assert result["success"] is True
    assert result["document_extension"] == ".pdf"
    convert.assert_called_once_with(body, ".pdf", "markdown", url)
