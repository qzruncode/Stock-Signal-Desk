# -*- coding: utf-8 -*-
"""Internal public-web readers used by the model-visible web operation.

``source_operations`` is the sole model-visible surface.  ``source_id=auto``
uses the local fallback chain; an explicit provider remains available when a
caller needs deterministic diagnostics.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any, Callable, Mapping

from src.tools.base import parse_iso_data_time, report_tool_progress
from src.tools.webfetch import (
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    _challenge_reason,
    _firecrawl_fetch,
    _http_fetch,
    _scrapling_fetch,
    _unusable_document_reason,
    _validate_public_url,
    fetch_url,
)
from src.tools.websearch import (
    _compact_results,
    _engine_query,
    _exa_search,
    _firecrawl_search,
    _parallel_search,
)


_MAX_SEARCH_CONTEXT_CHARACTERS = 24_000
_MAX_WEB_CONTENT_CHARACTERS = 60_000
_ATTACHMENT_ONLY_MESSAGES = frozenset(
    {
        "Image fetched successfully",
        "Binary file fetched successfully",
    }
)


# The provider-level reader already rejects empty pages and low-quality HTML.
# This second guard is deliberately narrower: it only corrects an adapter that
# labelled a *known access challenge* as successful, without second-guessing a
# short but otherwise valid source document.
_ACCESS_CHALLENGE_REASONS = frozenset(
    {
        "页面返回了 WAF 加密挑战而非正文",
        "页面返回了访问验证而非正文",
        "页面返回了 Cloudflare 验证而非正文",
        "页面要求浏览器验证后才能读取正文",
        "页面返回了 PerimeterX 验证而非正文",
        "页面返回了 DataDome 验证而非正文",
        "页面返回了 Akamai 验证而非正文",
        "页面返回了验证码而非正文",
        "页面返回了安全验证而非正文",
        "页面返回了滑动验证而非正文",
        "页面返回了疑似加密反爬载荷而非 HTML 正文",
    }
)


_EXPLICIT_CONTENT_TIME_VALUE = r"""
    (?P<year>(?:19|20)\d{2})\s*(?:年|[./-])\s*
    (?P<month>\d{1,2})\s*(?:月|[./-])\s*
    (?P<day>\d{1,2})(?:\s*日)?
    (?P<clock>
        (?:\s+|T)\d{1,2}:\d{2}(?::\d{2})?
        (?:\s*(?:Z|[+-]\d{2}:?\d{2}))?
    )?
"""
_EXPLICIT_CONTENT_TIME_PATTERNS = (
    re.compile(
        rf"""(?:发布时间|发布日期|发布于|发表时间|发表日期|更新时间|更新日期|更新于)
        \s*(?:[:：]|为|是)?\s*{_EXPLICIT_CONTENT_TIME_VALUE}""",
        re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(
        rf"""(?:^|[\n|_])\s*(?P<label>时间|date)\s*[:：]\s*
        {_EXPLICIT_CONTENT_TIME_VALUE}""",
        re.IGNORECASE | re.MULTILINE | re.VERBOSE,
    ),
    re.compile(
        rf"""\b(?:published|updated)\s*(?:on)?\s*[:：]?\s*
        {_EXPLICIT_CONTENT_TIME_VALUE}""",
        re.IGNORECASE | re.VERBOSE,
    ),
)


def _explicit_content_time(content: str) -> tuple[str, str] | None:
    """Extract only a clearly labelled source time from page text.

    Rendered markdown often drops HTML metadata even though the page itself
    exposes a publication/update field.  This intentionally does not infer a
    date from a URL, retrieval time, or an unlabeled date mentioned in prose.
    """
    # Page headers and article metadata appear near the beginning.  Limiting
    # the scan also keeps the direct-reader's normalization cost bounded.
    head = str(content or "")[:8_000]
    for pattern in _EXPLICIT_CONTENT_TIME_PATTERNS:
        match = pattern.search(head)
        if match is None:
            continue
        year = match.group("year")
        month = match.group("month")
        day = match.group("day")
        clock = " ".join(str(match.group("clock") or "").split())
        value = f"{year}-{int(month):02d}-{int(day):02d}"
        if clock:
            value += f" {clock.replace('T', '', 1).strip()}"
        label = str(match.groupdict().get("label") or "网页正文时间字段").strip()
        return value, label
    return None


def _bounded_limit(value: int) -> int:
    return max(1, min(int(value), 20))


def _bounded_context(value: int | None) -> int:
    return max(1_000, min(int(value or 12_000), _MAX_SEARCH_CONTEXT_CHARACTERS))


def _source_datetime(value: Any) -> datetime | None:
    return parse_iso_data_time(value)


def _resolve_source_time(raw_value: Any, content: str) -> tuple[str | None, str | None, list[str]]:
    """Resolve provider metadata and clearly labelled body time as source time."""
    data_time = str(raw_value).strip() if raw_value not in (None, "") else None
    notes: list[str] = []
    warnings: list[str] = []
    if data_time and parse_iso_data_time(data_time) is None:
        data_time = None
        notes.append("来源返回的 data_time 无法解析为 ISO 8601，已降级为时间未知。")
        warnings.append("来源返回了无法解析的 data_time；已按时间未知处理。")
    if not data_time:
        explicit_time = _explicit_content_time(content)
        if explicit_time is not None:
            candidate, label = explicit_time
            if parse_iso_data_time(candidate) is not None:
                data_time = candidate
                notes.append(f"网页正文中明确标注的来源时间字段：{label}。")
            else:
                notes.append("网页正文中的来源时间字段无法解析为 ISO 8601，已按时间未知处理。")
                warnings.append("网页正文中的来源时间字段无法解析；已按时间未知处理。")
    return data_time, (" ".join(notes) if notes else None), warnings


def _content_access(
    *,
    success: bool,
    content: str,
    attachments: Any,
    extraction_method: Any,
    requested_url: str,
    final_url: str,
    provider: str,
    content_type: str,
    document_extension: Any = None,
    error: str = "",
) -> dict[str, Any]:
    """Describe whether a fetch produced usable textual source content."""
    text = str(content or "").strip()
    extraction = str(extraction_method or "").strip()
    attachment_only = bool(attachments) and (
        extraction.endswith("attachment") or text in _ATTACHMENT_ONLY_MESSAGES
    )
    document_error = _unusable_document_reason(
        text,
        content_type=content_type,
        document_extension=document_extension,
        extraction_method=extraction,
    )
    content_extracted = bool(success and text and not attachment_only and not document_error)
    return {
        "mode": "content_read",
        "content_read": bool(success),
        "content_extracted": content_extracted,
        "content_read_required": True,
        "content_length": len(text) if content_extracted else 0,
        "requested_url": requested_url,
        "final_url": final_url,
        "provider": provider,
        "content_type": content_type,
        "extraction_method": extraction or None,
        "note": (
            "读取到了附件，但没有提取出可供正文分析的文本。"
            if success and not content_extracted
            else (document_error or error or None)
        ),
    }


def _search_attempt(raw: Mapping[str, Any], provider: str) -> dict[str, Any]:
    return {
        "provider": str(raw.get("provider") or provider),
        "success": raw.get("success") is True,
        "skipped": raw.get("skipped") is True,
        "duration_ms": raw.get("duration_ms"),
        "error": raw.get("error"),
    }


def _single_provider_search(
    *,
    query: str,
    provider: str,
    execute: Callable[[str], Mapping[str, Any]],
    num_results: int,
    context_max_characters: int | None,
    stale_after_days: int,
) -> dict[str, Any]:
    resolved_query = _engine_query(query)
    if not resolved_query:
        raise ValueError("query 不能为空")
    limit = _bounded_limit(num_results)
    max_characters = _bounded_context(context_max_characters)
    report_tool_progress(f"正在查询公开网页来源 {provider}", progress=10)
    raw = dict(execute(resolved_query))
    compacted, truncated = _compact_results(
        list(raw.get("results") or []),
        limit=limit,
        max_chars=max_characters,
        include_content=False,
    )
    output = str(raw.get("output") or "")
    if len(output) > max_characters:
        output = output[:max_characters].rstrip() + "…"
        truncated = True
    success = raw.get("success") is True and bool(compacted or output)
    error = str(raw.get("error") or "").strip()
    published = [
        parsed
        for parsed in (_source_datetime(item.get("published_date")) for item in compacted)
        if parsed is not None
    ]
    latest = max(published) if published else None
    report_tool_progress(f"公开网页来源 {provider} 已返回", progress=100)
    return {
        "query": resolved_query,
        "resolved_query": resolved_query,
        "success": success,
        "partial": success and bool(error),
        "provider": provider,
        "source": {
            "provider": provider,
            "operation": "web_search",
        },
        "source_scope": "single_provider_web_search",
        "results": compacted,
        "result_count": len(compacted),
        "output": output,
        "attempts": [_search_attempt(raw, provider)],
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "data_time": latest.isoformat() if latest else None,
        "data_time_provenance": "source" if latest else "unavailable",
        "data_time_note": (
            None
            if latest
            else "搜索结果未提供可识别的原始发布日期；检索完成时间不作为数据时间。"
        ),
        "is_stale": (
            latest < datetime.now().astimezone() - timedelta(days=stale_after_days)
            if latest
            else None
        ),
        "freshness_unknown": latest is None,
        "latest_published_date": latest.isoformat() if latest else None,
        "fallback_used": False,
        "_truncated": truncated,
        "errors": [] if success else ([error] if error else [f"{provider} 未返回可用搜索结果"]),
        "warnings": [error] if success and error else [],
    }


def search_web_firecrawl_searxng(
    query: str,
    numResults: int = 8,
    contextMaxCharacters: int | None = 12_000,
) -> dict[str, Any]:
    """Search only the project Firecrawl/SearXNG source, without crawling pages."""
    return _single_provider_search(
        query=query,
        provider="firecrawl_searxng",
        execute=lambda text: _firecrawl_search(
            text,
            limit=_bounded_limit(numResults),
            include_content=False,
        ),
        num_results=numResults,
        context_max_characters=contextMaxCharacters,
        stale_after_days=30,
    )


def search_web_exa(
    query: str,
    numResults: int = 8,
    livecrawl: str = "fallback",
    type: str = "auto",
    contextMaxCharacters: int | None = 12_000,
) -> dict[str, Any]:
    """Search only Exa; another provider is never called on failure."""
    if livecrawl not in {"fallback", "preferred"}:
        raise ValueError("livecrawl 必须是 fallback 或 preferred")
    if type not in {"auto", "fast", "deep"}:
        raise ValueError("type 必须是 auto、fast 或 deep")
    return _single_provider_search(
        query=query,
        provider="exa",
        execute=lambda text: _exa_search(
            text,
            limit=_bounded_limit(numResults),
            livecrawl=livecrawl,
            search_type=type,
            context_max_characters=_bounded_context(contextMaxCharacters),
        ),
        num_results=numResults,
        context_max_characters=contextMaxCharacters,
        stale_after_days=365 if type == "deep" else 30,
    )


def search_web_parallel(
    query: str,
    numResults: int = 8,
    contextMaxCharacters: int | None = 12_000,
) -> dict[str, Any]:
    """Search only Parallel's public-web source in one request."""
    return _single_provider_search(
        query=query,
        provider="parallel",
        execute=lambda text: _parallel_search(
            text,
            limit=_bounded_limit(numResults),
            session_id="",
        ),
        num_results=numResults,
        context_max_characters=contextMaxCharacters,
        stale_after_days=30,
    )


def _single_provider_fetch(
    *,
    url: str,
    format: str,
    timeout: int | None,
    provider: str,
    execute: Callable[[str, str, int], Mapping[str, Any]],
) -> dict[str, Any]:
    fmt = str(format or "markdown")
    if fmt not in {"markdown", "text", "html"}:
        raise ValueError("format 必须是 markdown、text 或 html")
    _validate_public_url(url)
    timeout_seconds = max(5, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    report_tool_progress(f"正在读取公开网页来源 {provider}", progress=10)
    raw = dict(execute(url, fmt, timeout_seconds))
    raw_content = str(raw.get("content") or "")
    content = raw_content
    truncated = False
    if len(content) > _MAX_WEB_CONTENT_CHARACTERS:
        content = content[:_MAX_WEB_CONTENT_CHARACTERS].rstrip() + "…"
        truncated = True
    success = raw.get("success") is True
    error = str(raw.get("error") or "").strip()
    content_type = str(raw.get("content_type") or "")
    document_extension = raw.get("document_extension")
    unusable_document = _unusable_document_reason(
        raw_content,
        content_type=content_type,
        document_extension=document_extension,
        extraction_method=str(raw.get("extraction_method") or ""),
    )
    if success and unusable_document:
        success = False
        error = unusable_document
        content = ""
    # A provider may report HTTP success while returning an anti-bot page.
    # Validate the returned body here as well so a future provider adapter
    # cannot accidentally turn an access challenge into Claim-Evidence input.
    detected_reason = _challenge_reason(content, content_type)
    challenge = detected_reason if detected_reason in _ACCESS_CHALLENGE_REASONS else None
    if success and challenge:
        success = False
        error = challenge
    data_time, content_time_note, time_warnings = _resolve_source_time(
        raw.get("content_time"),
        content,
    )
    warning = str(raw.get("quality_warning") or "").strip()
    warnings = ([warning] if success and warning else []) + time_warnings
    final_url = str(raw.get("final_url") or url)
    extraction_method = raw.get("extraction_method")
    report_tool_progress(f"公开网页来源 {provider} 已返回", progress=100)
    attempt = _search_attempt(raw, provider)
    attempt["success"] = success
    attempt["error"] = error or None
    result = {
        "url": url,
        "final_url": final_url,
        "format": fmt,
        "content_type": content_type,
        "document_extension": document_extension,
        "title": str(raw.get("title") or ""),
        "content": content,
        "attachments": raw.get("attachments"),
        "success": success,
        "partial": success and bool(error),
        "provider": provider,
        "source": {
            "provider": provider,
            "url": final_url,
            "operation": "web_fetch",
        },
        "source_scope": "single_provider_web_fetch",
        "attempts": [attempt],
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "data_time": data_time,
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": (
            content_time_note
            if content_time_note
            else (
                None
                if data_time
                else "网页未提供可识别的发布日期或更新时间；抓取完成时间不作为数据时间。"
            )
        ),
        "content_time": data_time,
        "is_stale": None,
        "freshness_unknown": data_time is None,
        "extraction_method": extraction_method,
        "fallback_used": False,
        "_truncated": bool(raw.get("_truncated")) or truncated,
        "errors": [] if success else ([error] if error else [f"{provider} 未返回网页内容"]),
        "warnings": warnings,
        "failure_kind": "challenge" if challenge else raw.get("failure_kind"),
    }
    result["content_access"] = _content_access(
        success=success,
        content=content,
        attachments=result["attachments"],
        extraction_method=extraction_method,
        requested_url=url,
        final_url=final_url,
        provider=provider,
        content_type=content_type,
        document_extension=document_extension,
        error=error,
    )
    return result


def read_web_auto(
    url: str,
    format: str = "markdown",
    timeout: int | None = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Read one URL with the local provider fallback chain."""
    fmt = str(format or "markdown")
    if fmt not in {"markdown", "text", "html"}:
        raise ValueError("format 必须是 markdown、text 或 html")
    _validate_public_url(url)
    timeout_seconds = max(5, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    report_tool_progress("正在自动选择公开网页读取来源", progress=10)
    raw = dict(fetch_url(url=str(url), format=fmt, timeout=timeout_seconds))
    raw_content = str(raw.get("content") or "")
    content = raw_content
    truncated = False
    if len(content) > _MAX_WEB_CONTENT_CHARACTERS:
        content = content[:_MAX_WEB_CONTENT_CHARACTERS].rstrip() + "…"
        truncated = True
    success = raw.get("success") is True
    final_url = str(raw.get("final_url") or url)
    provider = str(raw.get("provider") or "none")
    content_type = str(raw.get("content_type") or "")
    document_extension = raw.get("document_extension")
    unusable_document = _unusable_document_reason(
        raw_content,
        content_type=content_type,
        document_extension=document_extension,
        extraction_method=str(raw.get("extraction_method") or ""),
    )
    errors = [str(item) for item in list(raw.get("errors") or []) if str(item).strip()]
    if success and unusable_document:
        success = False
        content = ""
        errors.insert(0, unusable_document)
    warnings = [str(item) for item in list(raw.get("warnings") or []) if str(item).strip()]
    data_time, content_time_note, time_warnings = _resolve_source_time(
        raw.get("content_time"),
        content,
    )
    for warning in time_warnings:
        if warning not in warnings:
            warnings.append(warning)
    report_tool_progress("公开网页自动读取完成", progress=100)
    result = {
        "url": str(url),
        "final_url": final_url,
        "format": fmt,
        "content_type": content_type,
        "document_extension": document_extension,
        "title": str(raw.get("title") or ""),
        "content": content,
        "attachments": raw.get("attachments"),
        "success": success,
        "partial": bool(success and (raw.get("partial") or raw.get("_truncated") or truncated)),
        "provider": provider,
        "source": {
            "provider": provider,
            "url": final_url,
            "operation": "web_fetch",
        },
        "source_scope": "automatic_web_fetch",
        "attempts": list(raw.get("attempts") or []),
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "data_time": data_time,
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": (
            content_time_note
            if content_time_note
            else (
                None
                if data_time
                else "网页未提供可识别的发布日期或更新时间；抓取完成时间不作为数据时间。"
            )
        ),
        "content_time": data_time,
        "is_stale": None,
        "freshness_unknown": data_time is None,
        "extraction_method": raw.get("extraction_method"),
        "fallback_used": bool(raw.get("fallback_used")),
        "_truncated": bool(raw.get("_truncated")) or truncated,
        "errors": errors if not success else [],
        "warnings": warnings,
    }
    result["content_access"] = _content_access(
        success=success,
        content=content,
        attachments=result["attachments"],
        extraction_method=result["extraction_method"],
        requested_url=str(url),
        final_url=final_url,
        provider=provider,
        content_type=content_type,
        document_extension=document_extension,
        error=(errors[0] if errors else ""),
    )
    return result


def read_web_http(
    url: str,
    format: str = "markdown",
    timeout: int | None = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Read one URL via direct HTTP only; no browser or crawl fallback."""
    return _single_provider_fetch(
        url=url,
        format=format,
        timeout=timeout,
        provider="http",
        execute=_http_fetch,
    )


def read_web_scrapling(
    url: str,
    format: str = "markdown",
    timeout: int | None = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Read one URL through Scrapling's HTTP renderer only."""
    return _single_provider_fetch(
        url=url,
        format=format,
        timeout=timeout,
        provider="scrapling",
        execute=lambda target, fmt, seconds: _scrapling_fetch(
            target,
            fmt,
            seconds,
            browser=False,
        ),
    )


def read_web_patchright(
    url: str,
    format: str = "markdown",
    timeout: int | None = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Read one URL with one rendered Patchright browser session only."""
    return _single_provider_fetch(
        url=url,
        format=format,
        timeout=timeout,
        provider="patchright",
        execute=lambda target, fmt, seconds: _scrapling_fetch(
            target,
            fmt,
            seconds,
            browser=True,
        ),
    )


def read_web_firecrawl(
    url: str,
    format: str = "markdown",
    timeout: int | None = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Read one URL through the self-hosted Firecrawl source only."""
    return _single_provider_fetch(
        url=url,
        format=format,
        timeout=timeout,
        provider="firecrawl",
        execute=_firecrawl_fetch,
    )


__all__ = [
    "read_web_auto",
    "read_web_firecrawl",
    "read_web_http",
    "read_web_patchright",
    "read_web_scrapling",
    "search_web_exa",
    "search_web_firecrawl_searxng",
    "search_web_parallel",
]
