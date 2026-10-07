"""Function group 2 extracted from src/tools/webfetch.py."""

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

__all__ = [
    '_http_fetch',
    '_scrapling_fetch',
    '_firecrawl_fetch',
    '_attempt_view',
    '_https_upgrade_url',
    '_scrapling_failure_kind',
    'fetch_url',
]

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

        content_disposition = response.headers.get("content-disposition", "")
        extension = _document_extension(
            final_url,
            mime,
            body=body,
            content_disposition=content_disposition,
        )
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
                document_extension=extension,
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
        raw: str | bytes = ""
        document_body: bytes | None = None
        content_type = ""
        content_disposition = ""
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
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                response_headers = getattr(response, "headers", {}) or {}
                content_type = str(response_headers.get("content-type") or "")
                content_disposition = str(response_headers.get("content-disposition") or "")
                candidate_extension = _document_extension(
                    url,
                    content_type.split(";", 1)[0].strip().lower(),
                    content_disposition=content_disposition,
                )
                if candidate_extension and response is not None:
                    try:
                        response_body = response.body()
                    except Exception:
                        response_body = None
                    if isinstance(response_body, bytes):
                        document_body = response_body
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
            response_headers = getattr(page, "headers", {}) or getattr(page, "response_headers", {}) or {}
            content_type = str(response_headers.get("content-type") or "")
            content_disposition = str(response_headers.get("content-disposition") or "")
            raw = getattr(page, "body", None) or getattr(page, "text", None)
            if isinstance(raw, bytes):
                document_body = raw
            if not raw and hasattr(page, "get"):
                raw = page.get()
            if isinstance(raw, bytes):
                document_body = raw
            final_url = str(getattr(page, "url", None) or url)
            rendered_text = None
        _validate_public_url(final_url)
        extension = _document_extension(
            final_url,
            content_type.split(";", 1)[0].strip().lower(),
            body=document_body or b"",
            content_disposition=content_disposition,
        )
        if extension:
            if not document_body:
                return _timed_result(
                    provider,
                    started,
                    success=False,
                    skipped=False,
                    error="检测到文档响应，但未取得文档二进制内容",
                    failure_kind="content",
                    final_url=final_url,
                    content_type=content_type or "application/octet-stream",
                    document_extension=extension,
                )
            content, extraction_method = _convert_document(
                document_body,
                extension,
                fmt,
                final_url,
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
                title=f"{final_url} ({content_type or extension})",
                content_type=content_type or "application/octet-stream",
                document_extension=extension,
                extraction_method=f"{provider}+{extraction_method}",
                quality_warning=_quality_warning(content, fmt),
            )
        if isinstance(raw, bytes):
            raw = _decode_text(raw, "utf-8")
        raw = str(raw or "")
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
        error = str(exc)
        return _timed_result(
            provider,
            started,
            success=False,
            skipped=False,
            error=error,
            failure_kind=_scrapling_failure_kind(error),
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


def _https_upgrade_url(url: str) -> str | None:
    """Return the HTTPS alias for a plain HTTP URL when it is safe to try."""
    try:
        parsed = urlparse(str(url or ""))
        if parsed.scheme.lower() != "http" or not parsed.netloc or parsed.port is not None:
            return None
    except ValueError:
        return None
    return parsed._replace(scheme="https").geturl()


def _scrapling_failure_kind(error: str) -> str:
    """Classify local Scrapling TLS/runtime failures for fast provider fallback."""
    text = str(error or "").lower()
    runtime_markers = (
        "invalid library",
        "openssl_internal",
        "boringssl",
        "ssl_error_syscall",
        "no active session available",
        "curl: (35)",
    )
    return "provider_unavailable" if any(marker in text for marker in runtime_markers) else "transport"

def fetch_url(url: str, format: str = "markdown", timeout: int | None = None) -> dict[str, Any]:
    fmt = format or "markdown"
    if fmt not in {"markdown", "text", "html"}:
        raise ValueError("format 必须是 markdown、text 或 html")
    _validate_public_url(url)
    timeout_seconds = max(5, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))

    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    degraded_result: dict[str, Any] | None = None

    def run(
        candidate: Callable[[], dict[str, Any]],
        *,
        attempted_url: str,
    ) -> dict[str, Any]:
        current = candidate()
        unusable_document = _unusable_document_reason(
            str(current.get("content") or ""),
            content_type=str(current.get("content_type") or ""),
            document_extension=current.get("document_extension"),
            extraction_method=str(current.get("extraction_method") or ""),
        )
        if current.get("success") is True and unusable_document:
            current = {
                **current,
                "success": False,
                "error": unusable_document,
                "failure_kind": "content",
                "content": "",
            }
        attempt = _attempt_view(current)
        attempt["url"] = attempted_url
        attempts.append(attempt)
        return current

    direct = run(
        lambda: _http_fetch(url, fmt, timeout_seconds),
        attempted_url=url,
    )
    if direct.get("success") and not direct.get("quality_warning"):
        result = direct
    elif direct.get("success"):
        degraded_result = direct

    secure_url = _https_upgrade_url(url)
    upgraded_direct: dict[str, Any] | None = None
    if result is None and secure_url:
        upgraded_direct = run(
            lambda: _http_fetch(secure_url, fmt, timeout_seconds),
            attempted_url=secure_url,
        )
        if upgraded_direct.get("success") and not upgraded_direct.get("quality_warning"):
            result = upgraded_direct
        elif upgraded_direct.get("success"):
            degraded_result = upgraded_direct

    if result is None:
        strategy_result = upgraded_direct or direct
        fallback_url = secure_url or url
        if strategy_result.get("failure_kind") == "challenge":
            # Static HTTP and Firecrawl commonly reproduce the same encrypted
            # challenge.  A real browser is the useful next step.
            order: list[Callable[[], dict[str, Any]]] = [
                lambda: _scrapling_fetch(fallback_url, fmt, timeout_seconds, browser=True),
                lambda: _firecrawl_fetch(fallback_url, fmt, timeout_seconds),
                lambda: _scrapling_fetch(fallback_url, fmt, timeout_seconds, browser=False),
            ]
        elif strategy_result.get("success"):
            order = [
                lambda: _firecrawl_fetch(fallback_url, fmt, timeout_seconds),
                lambda: _scrapling_fetch(fallback_url, fmt, timeout_seconds, browser=True),
                lambda: _scrapling_fetch(fallback_url, fmt, timeout_seconds, browser=False),
            ]
        else:
            order = [
                lambda: _scrapling_fetch(fallback_url, fmt, timeout_seconds, browser=False),
                lambda: _scrapling_fetch(fallback_url, fmt, timeout_seconds, browser=True),
                lambda: _firecrawl_fetch(fallback_url, fmt, timeout_seconds),
            ]
        for candidate in order:
            current = run(candidate, attempted_url=fallback_url)
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
            "document_extension": None,
            "title": "",
            "content": "",
            "attachments": None,
            "success": False,
            "provider": "none",
            "attempts": attempts,
            "retrieved_at": now,
            "data_time": None,
            "data_time_provenance": "unavailable",
            "data_time_note": "网页读取失败；抓取完成时间不作为数据时间。",
            "content_time": None,
            "fallback_used": len(attempts) > 1,
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
        "document_extension": result.get("document_extension"),
        "title": result.get("title") or "",
        "content": result.get("content") or "",
        "attachments": result.get("attachments"),
        "success": True,
        "partial": bool(result.get("partial") or result.get("_truncated")),
        "provider": provider,
        "attempts": attempts,
        "retrieved_at": now,
        "data_time": result.get("content_time"),
        "data_time_provenance": "source" if result.get("content_time") else "unavailable",
        "data_time_note": (
            None
            if result.get("content_time")
            else "网页未提供可识别的发布日期或更新时间；抓取完成时间不作为数据时间。"
        ),
        "content_time": result.get("content_time"),
        "fallback_used": len(attempts) > 1 or provider != "http",
        "is_stale": None,
        "freshness_unknown": not bool(result.get("content_time")),
        "extraction_method": result.get("extraction_method"),
        "_truncated": False,
        "errors": [],
        # Failed providers are retained in ``attempts`` for diagnostics, but
        # they are not warnings on a successful final read.  Exposing an
        # intermediate error here made a valid fallback result look like a
        # failed PDF/article parse to the model and to the run inspector.
        "warnings": quality_warnings,
    }
