# -*- coding: utf-8 -*-
"""General-purpose URL fetcher with an OpenCode-compatible fast path.

Ordinary HTTP is always attempted first.  Local Scrapling, self-hosted
Firecrawl and Patchright are transport fallbacks for blocked or JavaScript-only
pages; they do not replace the normal web protocol.
"""

from __future__ import annotations

import base64
import ipaddress
import logging
import os
import re
import socket
import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import httpx

from src.tools._firecrawl import firecrawl_rest_config
from src.tools.base import ToolSpec, object_schema

logger = logging.getLogger(__name__)

MAX_RESPONSE_SIZE = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120
MAX_REDIRECTS = 8
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
_HONEST_UA = "opencode"
_SKIP_TAGS = ["script", "style", "noscript", "iframe", "object", "embed"]

WEBFETCH_DESCRIPTION = (
    "读取任意公开 http(s) URL，默认返回 Markdown，也可返回纯文本或 HTML。"
    "先使用标准 HTTP；遇到反爬、空壳或 JavaScript 页面时自动降级到本地 Scrapling、"
    "自托管 Firecrawl 和 Patchright。支持图片附件、重定向安全校验、5MB 上限及完整抓取记录。"
)


def _accept_header_for(fmt: str) -> str:
    if fmt == "markdown":
        return "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1"
    if fmt == "text":
        return "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1"
    return "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, text/markdown;q=0.7, */*;q=0.1"


def _allow_private() -> bool:
    return os.getenv("WEBFETCH_ALLOW_PRIVATE", "").strip().lower() in {"1", "true", "yes", "on"}


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL 必须是完整的 http:// 或 https:// 地址")
    if parsed.username or parsed.password:
        raise ValueError("URL 不允许包含用户名或密码")
    if _allow_private():
        return

    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("出于 SSRF 安全限制，不允许抓取本机或内网地址")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError("出于 SSRF 安全限制，不允许抓取本机、内网或保留地址")

    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        }
    except socket.gaierror as exc:
        raise ValueError(f"域名解析失败: {host}") from exc
    synthetic_proxy = ipaddress.ip_network("198.18.0.0/15")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global and ip not in synthetic_proxy:
            raise ValueError("出于 SSRF 安全限制，不允许抓取本机、内网或保留地址")


def _extract_text_from_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        cleaned = re.sub(
            r"<(script|style|noscript|iframe|object|embed)\b[^>]*>.*?</\1>",
            "",
            html,
            flags=re.S | re.I,
        )
        return re.sub(r"<[^>]+>", "", cleaned).strip()
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_SKIP_TAGS):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _convert_html_to_markdown(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        from markdownify import markdownify as markdownify
    except ImportError:
        return _extract_text_from_html(html)
    soup = BeautifulSoup(html, "lxml")
    for tag in soup([*_SKIP_TAGS, "meta", "link", "title"]):
        tag.decompose()
    return markdownify(str(soup.body or soup), heading_style="ATX", bullets="-").strip()


def _convert(raw: str, fmt: str, content_type: str) -> str:
    if "html" not in content_type.lower():
        return raw
    if fmt == "html":
        return raw
    if fmt == "text":
        return _extract_text_from_html(raw)
    return _convert_html_to_markdown(raw)


def _title_from_html(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    return _extract_text_from_html(match.group(1)).strip() if match else ""


def _looks_unusable(content: str, content_type: str) -> str | None:
    text = content.strip()
    if not text:
        return "网页正文为空"
    if "html" not in content_type.lower():
        return None
    probe = text[:5000].lower()
    challenge_markers = (
        "cf-chl-", "cloudflare ray id", "enable javascript and cookies to continue",
        "just a moment...", "g-recaptcha", "hcaptcha",
    )
    if any(marker in probe for marker in challenge_markers):
        return "页面返回了反爬验证而非正文"
    visible = _extract_text_from_html(text) if "<" in text else text
    if len(visible.strip()) < 80:
        return "页面只返回了空壳或过短正文"
    return None


def _quality_warning(content: str, fmt: str) -> str | None:
    """Identify generic navigation-heavy output without rejecting usable data."""
    if fmt == "html":
        return None
    text = content.strip()
    lines = [line for line in text.splitlines() if line.strip()]
    if len(text) >= 1500 and len(lines) >= 20:
        markdown_link_ratio = text.count("](") / len(lines)
        if markdown_link_ratio > 0.55:
            return "页面正文链接密度过高，继续尝试主内容抓取器"
    return None


def _timed_result(provider: str, started: float, **values: Any) -> dict[str, Any]:
    return {
        "provider": provider,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        **values,
    }


def _http_fetch(url: str, fmt: str, timeout: int) -> dict[str, Any]:
    """OpenCode-compatible HTTP fetch with redirect-by-redirect SSRF checks."""
    started = time.perf_counter()
    try:
        headers = {
            "User-Agent": _CHROME_UA,
            "Accept": _accept_header_for(fmt),
            "Accept-Language": "en-US,en;q=0.9",
        }
        current_url = url
        response: httpx.Response | None = None
        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            for _ in range(MAX_REDIRECTS + 1):
                _validate_public_url(current_url)
                response = client.get(current_url, headers=headers)
                if response.status_code == 403 and response.headers.get("cf-mitigated") == "challenge":
                    response = client.get(current_url, headers={**headers, "User-Agent": _HONEST_UA})
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("location")
                if not location:
                    break
                current_url = urljoin(str(response.url), location)
            else:
                raise ValueError(f"网页重定向超过 {MAX_REDIRECTS} 次上限")

        assert response is not None
        response.raise_for_status()
        final_url = str(response.url)
        _validate_public_url(final_url)
        declared_length = response.headers.get("content-length")
        if declared_length and declared_length.isdigit() and int(declared_length) > MAX_RESPONSE_SIZE:
            raise ValueError("Response too large (exceeds 5MB limit)")
        body = response.content
        if len(body) > MAX_RESPONSE_SIZE:
            raise ValueError("Response too large (exceeds 5MB limit)")

        content_type = response.headers.get("content-type", "")
        mime = content_type.split(";", 1)[0].strip().lower()
        title = f"{final_url} ({content_type})"
        if mime.startswith("image/"):
            attachment = {
                "type": "file",
                "mime": mime,
                "url": f"data:{mime};base64,{base64.b64encode(body).decode('ascii')}",
            }
            return _timed_result(
                "http",
                started,
                success=True,
                skipped=False,
                error=None,
                content="Image fetched successfully",
                attachments=[attachment],
                final_url=final_url,
                title=title,
                content_type=content_type,
                extraction_method="direct_http",
            )

        encoding = response.encoding or "utf-8"
        raw = body.decode(encoding, errors="replace")
        content = _convert(raw, fmt, content_type)
        quality_error = _looks_unusable(raw if "html" in content_type.lower() else content, content_type)
        return _timed_result(
            "http",
            started,
            success=quality_error is None,
            skipped=False,
            error=quality_error,
            content=content,
            attachments=None,
            final_url=final_url,
            title=_title_from_html(raw) or title,
            content_type=content_type,
            extraction_method="direct_http",
            quality_warning=_quality_warning(content, fmt) if quality_error is None else None,
        )
    except Exception as exc:
        return _timed_result("http", started, success=False, skipped=False, error=str(exc))


def _scrapling_fetch(url: str, fmt: str, timeout: int, *, browser: bool) -> dict[str, Any]:
    provider = "patchright" if browser else "scrapling"
    started = time.perf_counter()
    try:
        if browser:
            from scrapling.fetchers import StealthyFetcher

            page = StealthyFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                disable_resources=True,
                timeout=timeout * 1000,
            )
        else:
            from scrapling.fetchers import Fetcher

            page = Fetcher.get(
                url,
                timeout=timeout,
                retries=2,
                impersonate="chrome",
                follow_redirects="safe",
            )
        raw = getattr(page, "body", None) or getattr(page, "text", None)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not raw and hasattr(page, "get"):
            raw = page.get()
        raw = str(raw or "")
        final_url = str(getattr(page, "url", None) or url)
        _validate_public_url(final_url)
        quality_error = _looks_unusable(raw, "text/html")
        content = _convert(raw, fmt, "text/html")
        return _timed_result(
            provider,
            started,
            success=quality_error is None,
            skipped=False,
            error=quality_error,
            content=content,
            attachments=None,
            final_url=final_url,
            title=_title_from_html(raw),
            content_type="text/html",
            extraction_method="patchright_browser" if browser else "scrapling_http",
            quality_warning=_quality_warning(content, fmt) if quality_error is None else None,
        )
    except ImportError:
        return _timed_result(provider, started, success=False, skipped=True, error="Scrapling/Patchright 未安装")
    except Exception as exc:
        return _timed_result(provider, started, success=False, skipped=False, error=str(exc))


def _firecrawl_fetch(url: str, fmt: str, timeout: int) -> dict[str, Any]:
    started = time.perf_counter()
    config = firecrawl_rest_config()
    if config is None:
        return _timed_result("firecrawl", started, success=False, skipped=True, error="项目内置 Firecrawl 不可用")
    base_url, headers, auth_mode = config
    try:
        requested_format = "html" if fmt == "html" else "markdown"
        response = httpx.post(
            f"{base_url}/v2/scrape",
            headers=headers,
            json={
                "url": url,
                "formats": [requested_format],
                "onlyMainContent": True,
                "timeout": timeout * 1000,
            },
            timeout=timeout + 5,
        )
        response.raise_for_status()
        body = response.json()
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        raw = str(data.get(requested_format) or data.get("markdown") or data.get("html") or "")
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        final_url = str(metadata.get("sourceURL") or url)
        _validate_public_url(final_url)
        content_type = "text/html" if requested_format == "html" else "text/markdown"
        content = _convert(raw, fmt, content_type)
        quality_error = _looks_unusable(raw, content_type)
        return _timed_result(
            "firecrawl",
            started,
            success=quality_error is None,
            skipped=False,
            error=quality_error,
            content=content,
            attachments=None,
            final_url=final_url,
            title=str(metadata.get("title") or ""),
            content_type=content_type,
            extraction_method="firecrawl_main_content",
            quality_warning=_quality_warning(content, fmt) if quality_error is None else None,
            content_time=metadata.get("publishedTime") or metadata.get("modifiedTime"),
            auth_mode=auth_mode,
        )
    except Exception as exc:
        return _timed_result(
            "firecrawl",
            started,
            success=False,
            skipped=False,
            error=str(exc),
            auth_mode=auth_mode,
        )


def _attempt_view(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"content", "attachments", "title", "final_url", "content_type"}
    }


def fetch_url(url: str, format: str = "markdown", timeout: int | None = None) -> dict[str, Any]:
    fmt = format or "markdown"
    if fmt not in {"markdown", "text", "html"}:
        raise ValueError("format 必须是 markdown、text 或 html")
    _validate_public_url(url)
    timeout_seconds = max(5, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))

    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    degraded_result: dict[str, Any] | None = None
    candidates: list[Callable[[], dict[str, Any]]] = [
        lambda: _http_fetch(url, fmt, timeout_seconds),
        lambda: _scrapling_fetch(url, fmt, timeout_seconds, browser=False),
        lambda: _firecrawl_fetch(url, fmt, timeout_seconds),
        lambda: _scrapling_fetch(url, fmt, timeout_seconds, browser=True),
    ]
    for candidate in candidates:
        current = candidate()
        attempts.append(_attempt_view(current))
        if current.get("success"):
            if current.get("quality_warning"):
                if degraded_result is None:
                    degraded_result = current
                continue
            result = current
            break

    if result is None and degraded_result is not None:
        result = degraded_result

    now = datetime.now().astimezone().isoformat()
    failures = [
        str(item["error"])
        for item in attempts
        if item.get("error") and not item.get("skipped")
    ]
    if result is None:
        return {
            "url": url,
            "final_url": url,
            "format": fmt,
            "content_type": "",
            "title": "",
            "content": "",
            "attachments": None,
            "success": False,
            "provider": "none",
            "attempts": attempts,
            "data_time": now,
            "content_time": None,
            "fallback_used": False,
            "is_stale": None,
            "freshness_unknown": True,
            "extraction_method": None,
            "_truncated": False,
            "errors": failures,
            "warnings": [],
        }

    provider = str(result.get("provider") or "unknown")
    quality_warnings = [str(result["quality_warning"])] if result.get("quality_warning") else []
    return {
        "url": url,
        "final_url": result.get("final_url") or url,
        "format": fmt,
        "content_type": result.get("content_type") or "",
        "title": result.get("title") or "",
        "content": result.get("content") or "",
        "attachments": result.get("attachments"),
        "success": True,
        "provider": provider,
        "attempts": attempts,
        "data_time": now,
        "content_time": result.get("content_time"),
        "fallback_used": provider != "http",
        "is_stale": None,
        "freshness_unknown": not bool(result.get("content_time")),
        "extraction_method": result.get("extraction_method"),
        "_truncated": False,
        "errors": [],
        "warnings": [*failures, *quality_warnings],
    }


TOOL = ToolSpec(
    name="webfetch",
    description=WEBFETCH_DESCRIPTION,
    parameters=object_schema(
        {
            "url": {"type": "string", "description": "要读取的公开 http(s) URL"},
            "format": {"type": "string", "enum": ["markdown", "text", "html"], "default": "markdown"},
            "timeout": {"type": "integer", "minimum": 5, "maximum": 120, "default": 30},
        },
        ["url"],
    ),
    executor=fetch_url,
    category="search",
)
