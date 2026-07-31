# -*- coding: utf-8 -*-
"""General-purpose, local-first URL reader.

The fast path follows OpenCode's webfetch contract: normal HTTP, browser-like
headers, a Cloudflare honest-UA retry, redirect safety and a 5 MB limit.  The
response is then validated before it is accepted.  WAF payloads and JavaScript
shells are escalated to the project's local Patchright browser; Firecrawl is a
final extraction fallback, not proof that a page was fetched successfully.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import logging
import os
import re
import socket
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlparse

import httpx

from src.tools._firecrawl import firecrawl_rest_config
from src.tools.base import ToolSpec, object_schema

logger = logging.getLogger(__name__)

MAX_RESPONSE_SIZE = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120
MAX_REDIRECTS = 8
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
_HONEST_UA = "opencode"
_SKIP_TAGS = ["script", "style", "noscript", "iframe", "object", "embed", "template"]
_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".xls"}
_DOCUMENT_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}
_TEXT_APPLICATION_MIMES = {
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/xhtml+xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/javascript",
    "application/x-javascript",
}
_CONTENT_TOKENS = re.compile(
    r"(?:^|[-_])(?:article|content|post|entry|story|detail|正文|新闻)(?:$|[-_])",
    re.I,
)
_BOILERPLATE_TOKENS = re.compile(
    r"(?:comment|reply|discussion|sidebar|recommend|related|footer|header|nav|menu|toolbar|share|login|广告|评论|推荐)",
    re.I,
)

WEBFETCH_DESCRIPTION = (
    "读取任意公开 http(s) URL，默认返回 Markdown，也可返回纯文本或 HTML。"
    "支持普通网页、JavaScript 动态页面、常见反爬页面、JSON/XML/文本、图片及 PDF/Word/PPT/Excel 文档。"
    "标准 HTTP 失败或返回 WAF/空壳时会自动使用本地 Scrapling/Patchright 和自托管 Firecrawl，"
    "并返回每次尝试、最终地址和实际提取方式。"
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
            item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
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
            r"<(script|style|noscript|iframe|object|embed|template)\b[^>]*>.*?</\1>",
            "",
            html,
            flags=re.S | re.I,
        )
        return re.sub(r"<[^>]+>", "", cleaned).strip()
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_SKIP_TAGS):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _markdownify(node: Any) -> str:
    try:
        from markdownify import markdownify
    except ImportError:
        return _extract_text_from_html(str(node))
    return markdownify(str(node), heading_style="ATX", bullets="-").strip()


def _metadata_from_soup(soup: Any) -> dict[str, str | None]:
    def meta_value(*selectors: str) -> str:
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                value = node.get("content") or node.get_text(" ", strip=True)
                if value:
                    return str(value).strip()
        return ""

    title = meta_value('meta[property="og:title"]', 'meta[name="twitter:title"]', "title")
    description = meta_value(
        'meta[property="og:description"]', 'meta[name="description"]', 'meta[name="twitter:description"]'
    )
    content_time = meta_value(
        'meta[property="article:published_time"]',
        'meta[name="date"]',
        'meta[name="pubdate"]',
        "time[datetime]",
    )
    return {"title": title, "description": description, "content_time": content_time or None}


def _normalized_similarity(left: str, right: str) -> float:
    normalize = lambda value: re.sub(r"\s+|[^\w\u4e00-\u9fff]", "", value.lower())
    a, b = normalize(left)[:1200], normalize(right)[:1200]
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    return SequenceMatcher(None, a, b).ratio()


def _semantic_candidate(soup: Any, metadata: dict[str, str | None]) -> tuple[Any | None, float]:
    """Find a main-content DOM node without relying on site-specific selectors."""
    seen: set[int] = set()
    candidates: list[Any] = []
    for selector in ("article", "main", '[role="main"]', '[itemprop="articleBody"]'):
        for node in soup.select(selector):
            if id(node) not in seen:
                seen.add(id(node))
                candidates.append(node)
    for node in soup.find_all(["div", "section"]):
        marker = " ".join([str(node.get("id") or ""), *[str(v) for v in (node.get("class") or [])]])
        if _CONTENT_TOKENS.search(marker) and id(node) not in seen:
            seen.add(id(node))
            candidates.append(node)
        if len(candidates) >= 240:
            break

    reference = " ".join(str(metadata.get(key) or "") for key in ("title", "description"))
    best_node: Any | None = None
    best_score = -1_000.0
    for node in candidates:
        text = node.get_text(" ", strip=True)
        if len(text) < 20:
            continue
        marker = " ".join([str(node.get("id") or ""), *[str(v) for v in (node.get("class") or [])]])
        links = " ".join(link.get_text(" ", strip=True) for link in node.find_all("a"))
        link_ratio = min(1.0, len(links) / max(len(text), 1))
        punctuation = len(re.findall(r"[。！？；.!?;]", text))
        score = min(260.0, len(text) / 6.0)
        score += min(90.0, punctuation * 5.0)
        score += min(80.0, len(node.find_all("p")) * 10.0)
        score -= link_ratio * 220.0
        if node.name == "article":
            score += 300.0
        elif node.name == "main" or str(node.get("role") or "").lower() == "main":
            score += 170.0
        if str(node.get("itemprop") or "").lower() == "articlebody":
            score += 300.0
        if _CONTENT_TOKENS.search(marker):
            score += 100.0
        if _BOILERPLATE_TOKENS.search(marker):
            score -= 280.0
        score += _normalized_similarity(text, reference) * 360.0
        if score > best_score:
            best_node, best_score = node, score
    return best_node, best_score


def _extract_html(raw: str, fmt: str) -> tuple[str, str, dict[str, str | None]]:
    """Extract readable content using semantic DOM and Trafilatura candidates."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        if fmt == "html":
            return raw, "full_html", {"title": "", "description": "", "content_time": None}
        text = _extract_text_from_html(raw)
        return text, "html_text_fallback", {"title": "", "description": "", "content_time": None}

    soup = BeautifulSoup(raw, "lxml")
    metadata = _metadata_from_soup(soup)
    for tag in soup(_SKIP_TAGS):
        tag.decompose()
    semantic, semantic_score = _semantic_candidate(soup, metadata)
    visible_text = (soup.body or soup).get_text("\n", strip=True)

    # Strong semantic nodes beat generic extractors.  This handles pages whose
    # comment/list area is much longer than the actual article body.
    if semantic is not None and semantic_score >= 300:
        if fmt == "html":
            content = str(semantic)
        elif fmt == "text":
            content = semantic.get_text("\n", strip=True)
        else:
            content = _markdownify(semantic)
        if content.strip():
            return content.strip(), "semantic_dom", metadata

    if fmt != "html":
        try:
            import trafilatura

            extracted = trafilatura.extract(
                raw,
                output_format="txt" if fmt == "text" else "markdown",
                include_comments=False,
                include_links=fmt == "markdown",
                include_images=fmt == "markdown",
                include_tables=True,
                favor_precision=True,
                deduplicate=True,
            )
        except Exception:
            logger.debug("Trafilatura extraction failed", exc_info=True)
            extracted = None
        if extracted and len(extracted.strip()) >= 80:
            extracted = extracted.strip()
            # Article extractors intentionally discard navigation, but on
            # dashboards and web applications they can also discard nearly all
            # useful state.  Preserve the rendered page when extraction keeps
            # less than 12% of a substantial visible document.
            extraction_ratio = len(extracted) / max(len(visible_text), 1)
            if len(visible_text) < 1500 or extraction_ratio >= 0.12:
                return extracted, "trafilatura", metadata

    if semantic is not None:
        if fmt == "html":
            content = str(semantic)
        elif fmt == "text":
            content = semantic.get_text("\n", strip=True)
        else:
            content = _markdownify(semantic)
        if content.strip():
            return content.strip(), "semantic_dom", metadata

    body = soup.body or soup
    if fmt == "html":
        content = str(body)
    elif fmt == "text":
        content = body.get_text("\n", strip=True)
    else:
        content = _markdownify(body)
    return content.strip(), "full_page_fallback", metadata


def _challenge_reason(content: str, content_type: str) -> str | None:
    text = content.strip()
    if not text:
        return "网页正文为空"
    normalized = text[:20_000].replace("\\", "").lower()
    markers = (
        ("_waf_", "页面返回了 WAF 加密挑战而非正文"),
        ("cf-chl-", "页面返回了 Cloudflare 验证而非正文"),
        ("cloudflare ray id", "页面返回了 Cloudflare 验证而非正文"),
        ("cf-turnstile", "页面返回了 Cloudflare 验证而非正文"),
        ("enable javascript and cookies to continue", "页面要求浏览器验证后才能读取正文"),
        ("just a moment...", "页面要求浏览器验证后才能读取正文"),
        ("px-captcha", "页面返回了 PerimeterX 验证而非正文"),
        ("perimeterx", "页面返回了 PerimeterX 验证而非正文"),
        ("datadome", "页面返回了 DataDome 验证而非正文"),
        ("akamai bot manager", "页面返回了 Akamai 验证而非正文"),
        ("g-recaptcha", "页面返回了验证码而非正文"),
        ("hcaptcha", "页面返回了验证码而非正文"),
        ("访问验证", "页面返回了访问验证而非正文"),
        ("安全验证", "页面返回了安全验证而非正文"),
        ("滑动验证", "页面返回了滑动验证而非正文"),
    )
    for marker, reason in markers:
        if marker in normalized:
            return reason

    mime = content_type.split(";", 1)[0].strip().lower()
    if "html" in mime:
        visible = _extract_text_from_html(text) if "<" in text else text
        if len(visible.strip()) < 80:
            return "页面只返回了 JavaScript 空壳或过短正文"
        loading_markers = len(re.findall(r"(?:数据)?加载中|loading[.….]*", visible, flags=re.I))
        placeholder_lines = len(re.findall(r"(?m)^\s*[-—–]{1,3}\s*$", visible))
        visible_lines = max(1, len([line for line in visible.splitlines() if line.strip()]))
        if loading_markers >= 2 and placeholder_lines >= 8 and placeholder_lines / visible_lines >= 0.08:
            return "页面返回了尚未加载完成的 JavaScript 动态占位内容"
        # A declared HTML response made almost entirely of encoded characters
        # is commonly an encrypted bot challenge even without a known marker.
        compact = re.sub(r"\s+", "", text[:12_000])
        encoded = sum(ch.isalnum() or ch in '+/=_-{}":,' for ch in compact)
        if len(compact) > 800 and encoded / len(compact) > 0.97 and "<html" not in compact[:1000].lower():
            return "页面返回了疑似加密反爬载荷而非 HTML 正文"
    return None


def _quality_warning(content: str, fmt: str) -> str | None:
    if fmt == "html":
        return None
    text = content.strip()
    lines = [line for line in text.splitlines() if line.strip()]
    if len(text) >= 1500 and len(lines) >= 20:
        markdown_link_ratio = text.count("](") / len(lines)
        if markdown_link_ratio > 0.55:
            return "页面正文链接密度过高，继续尝试主内容抓取器"
    return None


def _decode_text(body: bytes, encoding: str | None) -> str:
    if encoding:
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(body).best()
        if best is not None:
            return str(best)
    except Exception:
        logger.debug("Character-set detection failed", exc_info=True)
    return body.decode("utf-8", errors="replace")


def _document_extension(url: str, mime: str) -> str | None:
    extension = PurePosixPath(unquote(urlparse(url).path)).suffix.lower()
    if extension in _DOCUMENT_EXTENSIONS:
        return extension
    by_mime = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-excel": ".xls",
    }
    return by_mime.get(mime)


def _convert_document(body: bytes, extension: str, fmt: str, url: str) -> tuple[str, str]:
    from markitdown import MarkItDown

    result = MarkItDown(enable_plugins=False).convert_stream(io.BytesIO(body), file_extension=extension, url=url)
    markdown = str(result.text_content or "").strip()
    if not markdown:
        raise ValueError("文档解析结果为空")
    if fmt == "text":
        return re.sub(r"(?m)^#{1,6}\s+|[*_`]", "", markdown).strip(), "markitdown"
    if fmt == "html":
        try:
            import markdown as markdown_lib

            return markdown_lib.markdown(markdown, extensions=["tables"]), "markitdown"
        except ImportError:
            return f"<pre>{markdown}</pre>", "markitdown"
    return markdown, "markitdown"


def _timed_result(provider: str, started: float, **values: Any) -> dict[str, Any]:
    return {"provider": provider, "duration_ms": int((time.perf_counter() - started) * 1000), **values}


def _html_result(
    provider: str,
    started: float,
    raw: str,
    fmt: str,
    final_url: str,
    method: str,
    *,
    rendered_text: str | None = None,
) -> dict[str, Any]:
    challenge = _challenge_reason(raw, "text/html")
    if challenge:
        return _timed_result(
            provider,
            started,
            success=False,
            skipped=False,
            error=challenge,
            failure_kind="challenge",
            final_url=final_url,
        )
    content, extraction, metadata = _extract_html(raw, fmt)
    if rendered_text and fmt != "html" and extraction == "full_page_fallback" and len(rendered_text.strip()) >= 80:
        # Browser innerText excludes hidden menus/templates that can inflate
        # full-DOM Markdown by tens of thousands of characters and push the
        # actual live dashboard data outside the agent's content window.
        content = rendered_text.strip()
        extraction = "rendered_visible_text"
    if not content.strip():
        return _timed_result(
            provider,
            started,
            success=False,
            skipped=False,
            error="网页正文为空",
            failure_kind="empty",
            final_url=final_url,
        )
    return _timed_result(
        provider,
        started,
        success=True,
        skipped=False,
        error=None,
        content=content,
        attachments=None,
        final_url=final_url,
        title=str(metadata.get("title") or ""),
        content_type="text/html",
        extraction_method=f"{method}+{extraction}",
        content_time=metadata.get("content_time"),
        quality_warning=_quality_warning(content, fmt),
    )


def _http_fetch(url: str, fmt: str, timeout: int) -> dict[str, Any]:
    """OpenCode-compatible HTTP fetch with validation and document support."""
    started = time.perf_counter()
    try:
        headers = {
            "User-Agent": _CHROME_UA,
            "Accept": _accept_header_for(fmt),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
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
                extraction_method="direct_http_attachment",
            )

        extension = _document_extension(final_url, mime)
        if extension or mime in _DOCUMENT_MIMES:
            if not extension:
                raise ValueError(f"无法识别文档格式: {mime}")
            content, method = _convert_document(body, extension, fmt, final_url)
            return _timed_result(
                "http",
                started,
                success=True,
                skipped=False,
                error=None,
                content=content,
                attachments=None,
                final_url=final_url,
                title=title,
                content_type=content_type,
                extraction_method=method,
                quality_warning=_quality_warning(content, fmt),
            )

        is_text = mime.startswith("text/") or mime in _TEXT_APPLICATION_MIMES or not mime
        if not is_text:
            attachment = {
                "type": "file",
                "mime": mime or "application/octet-stream",
                "url": f"data:{mime or 'application/octet-stream'};base64,{base64.b64encode(body).decode('ascii')}",
            }
            return _timed_result(
                "http",
                started,
                success=True,
                skipped=False,
                error=None,
                content="Binary file fetched successfully",
                attachments=[attachment],
                final_url=final_url,
                title=title,
                content_type=content_type,
                extraction_method="direct_http_attachment",
            )

        raw = _decode_text(body, response.encoding)
        if "html" in mime or "<html" in raw[:1000].lower() or "<!doctype html" in raw[:1000].lower():
            result = _html_result("http", started, raw, fmt, final_url, "direct_http")
            if result.get("success") and not result.get("title"):
                result["title"] = title
            result["content_type"] = content_type
            return result

        challenge = _challenge_reason(raw, content_type)
        return _timed_result(
            "http",
            started,
            success=challenge is None,
            skipped=False,
            error=challenge,
            failure_kind="challenge" if challenge else None,
            content=raw,
            attachments=None,
            final_url=final_url,
            title=title,
            content_type=content_type,
            extraction_method="direct_http_text",
            quality_warning=None,
        )
    except Exception as exc:
        return _timed_result("http", started, success=False, skipped=False, error=str(exc), failure_kind="transport")


def _scrapling_fetch(url: str, fmt: str, timeout: int, *, browser: bool) -> dict[str, Any]:
    provider = "patchright" if browser else "scrapling"
    started = time.perf_counter()
    try:
        if browser:
            from patchright.sync_api import TimeoutError as PatchrightTimeoutError
            from patchright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser_instance = playwright.chromium.launch(headless=True)
                context = browser_instance.new_context(
                    locale="zh-CN",
                    user_agent=_CHROME_UA,
                    viewport={"width": 1440, "height": 1000},
                )
                page = context.new_page()

                def handle_route(route: Any) -> None:
                    request = route.request
                    if request.is_navigation_request():
                        try:
                            _validate_public_url(request.url)
                        except ValueError:
                            route.abort()
                            return
                    if request.resource_type in {"image", "media", "font"}:
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/*", handle_route)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(10_000, timeout * 1000))
                except PatchrightTimeoutError:
                    pass
                # Some quote dashboards update after the network first becomes
                # idle.  A short bounded settle period captures that state.
                page.wait_for_timeout(min(3_000, timeout * 500))
                raw = page.content()
                rendered_text = page.locator("body").inner_text(timeout=min(10_000, timeout * 1000))
                final_url = page.url
                context.close()
                browser_instance.close()
        else:
            from scrapling.fetchers import Fetcher

            page = Fetcher.get(
                url,
                timeout=timeout,
                retries=2,
                impersonate="chrome",
                follow_redirects="safe",
            )
            status = int(getattr(page, "status", None) or getattr(page, "status_code", None) or 200)
            if status >= 400:
                raise ValueError(f"HTTP {status}")
            raw = getattr(page, "body", None) or getattr(page, "text", None)
            if isinstance(raw, bytes):
                raw = _decode_text(raw, "utf-8")
            if not raw and hasattr(page, "get"):
                raw = page.get()
            raw = str(raw or "")
            final_url = str(getattr(page, "url", None) or url)
            rendered_text = None
        _validate_public_url(final_url)
        return _html_result(
            provider,
            started,
            raw,
            fmt,
            final_url,
            "patchright_browser" if browser else "scrapling_http",
            rendered_text=rendered_text,
        )
    except ImportError:
        return _timed_result(
            provider,
            started,
            success=False,
            skipped=True,
            error="Scrapling/Patchright 未安装",
            failure_kind="runtime",
        )
    except Exception as exc:
        return _timed_result(
            provider,
            started,
            success=False,
            skipped=False,
            error=str(exc),
            failure_kind="transport",
        )


def _firecrawl_fetch(url: str, fmt: str, timeout: int) -> dict[str, Any]:
    started = time.perf_counter()
    config = firecrawl_rest_config()
    if config is None:
        return _timed_result(
            "firecrawl",
            started,
            success=False,
            skipped=True,
            error="项目内置 Firecrawl 不可用",
            failure_kind="runtime",
        )
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
        challenge = _challenge_reason(raw, content_type)
        if challenge:
            return _timed_result(
                "firecrawl",
                started,
                success=False,
                skipped=False,
                error=challenge,
                failure_kind="challenge",
                auth_mode=auth_mode,
            )
        content = raw
        extraction = "firecrawl_main_content"
        if requested_format == "html":
            content, nested_method, nested_metadata = _extract_html(raw, fmt)
            extraction = f"firecrawl+{nested_method}"
            if not metadata.get("title"):
                metadata["title"] = nested_metadata.get("title")
        return _timed_result(
            "firecrawl",
            started,
            success=bool(content.strip()),
            skipped=False,
            error=None if content.strip() else "网页正文为空",
            content=content,
            attachments=None,
            final_url=final_url,
            title=str(metadata.get("title") or ""),
            content_type=content_type,
            extraction_method=extraction,
            quality_warning=_quality_warning(content, fmt),
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
            failure_kind="transport",
            auth_mode=auth_mode,
        )


def _attempt_view(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"content", "attachments", "title", "final_url", "content_type", "quality_warning"}
        and value is not None
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

    def run(candidate: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        current = candidate()
        attempts.append(_attempt_view(current))
        return current

    direct = run(lambda: _http_fetch(url, fmt, timeout_seconds))
    if direct.get("success") and not direct.get("quality_warning"):
        result = direct
    elif direct.get("success"):
        degraded_result = direct

    if result is None:
        if direct.get("failure_kind") == "challenge":
            # Static HTTP and Firecrawl commonly reproduce the same encrypted
            # challenge.  A real browser is the useful next step.
            order: list[Callable[[], dict[str, Any]]] = [
                lambda: _scrapling_fetch(url, fmt, timeout_seconds, browser=True),
                lambda: _firecrawl_fetch(url, fmt, timeout_seconds),
                lambda: _scrapling_fetch(url, fmt, timeout_seconds, browser=False),
            ]
        elif direct.get("success"):
            order = [
                lambda: _firecrawl_fetch(url, fmt, timeout_seconds),
                lambda: _scrapling_fetch(url, fmt, timeout_seconds, browser=True),
                lambda: _scrapling_fetch(url, fmt, timeout_seconds, browser=False),
            ]
        else:
            order = [
                lambda: _scrapling_fetch(url, fmt, timeout_seconds, browser=False),
                lambda: _scrapling_fetch(url, fmt, timeout_seconds, browser=True),
                lambda: _firecrawl_fetch(url, fmt, timeout_seconds),
            ]
        for candidate in order:
            current = run(candidate)
            if current.get("success"):
                if current.get("quality_warning"):
                    if (
                        current.get("provider") == "patchright"
                        and str(current.get("extraction_method") or "").endswith("full_page_fallback")
                        and len(str(current.get("content") or "")) >= 1500
                    ):
                        # A rendered dashboard is intentionally navigation- and
                        # link-heavy.  Keeping its complete live state is more
                        # useful than replacing it with a shorter article-only
                        # fallback.
                        result = current
                        break
                    if degraded_result is None:
                        degraded_result = current
                    continue
                if (
                    degraded_result is not None
                    and len(str(current.get("content") or "")) < len(str(degraded_result.get("content") or "")) * 0.4
                ):
                    # A provider without a warning is not automatically better
                    # when it discarded most of a previously fetched page.
                    continue
                result = current
                break

    if result is None and degraded_result is not None:
        result = degraded_result

    now = datetime.now().astimezone().isoformat()
    failures = [str(item["error"]) for item in attempts if item.get("error") and not item.get("skipped")]
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
