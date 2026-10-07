"""Function group 1 extracted from src/tools/webfetch.py."""

from __future__ import annotations

from src.tools.webfetch import (
    base64,
    io,
    ipaddress,
    logging,
    os,
    re,
    socket,
    time,
    datetime,
    SequenceMatcher,
    PurePosixPath,
    Any,
    Callable,
    unquote,
    urljoin,
    urlparse,
    httpx,
    firecrawl_rest_config,
    ToolSpec,
    object_schema,
    logger,
    MAX_RESPONSE_SIZE,
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    MAX_REDIRECTS,
    _CHROME_UA,
    _HONEST_UA,
    _SKIP_TAGS,
    _DOCUMENT_EXTENSIONS,
    _DOCUMENT_MIMES,
    _TEXT_APPLICATION_MIMES,
    _CONTENT_TOKENS,
    _BOILERPLATE_TOKENS,
 )

__all__ = ['_accept_header_for', '_allow_private', '_validate_public_url', '_extract_text_from_html', '_markdownify', '_metadata_from_soup', '_normalized_similarity', '_semantic_candidate', '_extract_html', '_challenge_reason', '_quality_warning', '_decode_text', '_document_extension', '_convert_document', '_unusable_document_reason', '_timed_result', '_html_result']

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
        # Some providers return this JavaScript configuration as Markdown,
        # rather than an HTML status page.  It is still a bot challenge, not
        # readable source content.
        ("cf_app_waf", "页面返回了访问验证而非正文"),
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

    # Do not reject an ordinary article that merely discusses verification.
    # The pairing below is the actual access-gate wording emitted by several
    # anti-bot pages, so it is safe to treat as a failed fetch.
    if (
        "access verification" in normalized
        and (
            "slide to complete the verification" in normalized
            or "before accessing the web page" in normalized
        )
    ):
        return "页面返回了访问验证而非正文"

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

def _document_extension(
    url: str,
    mime: str,
    *,
    body: bytes = b"",
    content_disposition: str = "",
) -> str | None:
    """Resolve a document type even when a download URL hides its filename.

    Research-report endpoints frequently return a signed URL without a file
    suffix and ``application/octet-stream``. In that case the response
    headers and the PDF magic bytes are more reliable than the visible URL.
    """
    extension = PurePosixPath(unquote(urlparse(url).path)).suffix.lower()
    if extension in _DOCUMENT_EXTENSIONS:
        return extension

    disposition_match = re.search(
        r"(?:^|;)\s*filename\*?\s*=\s*(?:UTF-8''|\"?)([^;\"]+)",
        content_disposition or "",
        flags=re.IGNORECASE,
    )
    if disposition_match:
        disposition_extension = PurePosixPath(
            unquote(disposition_match.group(1).strip().strip('"'))
        ).suffix.lower()
        if disposition_extension in _DOCUMENT_EXTENSIONS:
            return disposition_extension

    by_mime = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-excel": ".xls",
    }
    extension = by_mime.get(mime)
    if extension:
        return extension

    # A PDF signature is unambiguous enough for the generic/octet-stream
    # responses used by several research-report hosts.
    if body[:5] == b"%PDF-":
        return ".pdf"
    return None

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


def _unusable_document_reason(
    content: str,
    *,
    content_type: str = "",
    document_extension: str | None = None,
    extraction_method: str = "",
) -> str | None:
    """Reject a document response that was returned as binary/text garbage.

    A provider can report HTTP success while decoding a PDF body as text.  A
    non-empty string is not enough evidence that a document was extracted: the
    raw PDF signature, NUL bytes, and replacement-character runs are strong
    indicators that the model would receive an unreadable payload.  MarkItDown
    output is the explicit successful document-extraction path.
    """
    text = str(content or "")
    if not text.strip():
        return None
    extraction = str(extraction_method or "").strip().lower()
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    if "markitdown" in extraction:
        return None
    head = text.lstrip()[:4_096]
    if head.startswith("%PDF-") or "\x00" in head or head.count("\ufffd") >= 4:
        return "返回了原始文档二进制，未提取出可读正文"
    if mime in _DOCUMENT_MIMES:
        return "返回了文档附件，但没有通过文档解析器提取正文"
    if document_extension in _DOCUMENT_EXTENSIONS:
        return "返回了文档内容，但没有通过文档解析器提取正文"
    return None

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
