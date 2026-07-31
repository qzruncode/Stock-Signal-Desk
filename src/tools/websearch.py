# -*- coding: utf-8 -*-
"""Generic web search backed by the project's local search infrastructure.

The user's query is passed through unchanged. The primary provider is the
project-managed Firecrawl service, whose search backend is the project-managed
SearXNG metasearch instance. Exa and Parallel are remote fallbacks only.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from src.tools._firecrawl import firecrawl_rest_config
from src.tools.base import ToolSpec, object_schema

EXA_MCP_URL = "https://mcp.exa.ai/mcp"
PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
MCP_TIMEOUT_SECONDS = 25.0
FIRECRAWL_SEARCH_TIMEOUT_SECONDS = 25.0

WEBSEARCH_DESCRIPTION = (
    "通用公开网页搜索，可搜索股票、新闻、天气、技术资料等任意主题。"
    "查询原样交给项目内置的 Firecrawl + SearXNG 多引擎搜索；只有本地搜索失败或无结果时，"
    "才依次使用 Exa、Parallel 兜底。返回来源链接、摘要和完整降级记录。"
    "若已有更准确的结构化工具，应优先使用结构化工具。"
)


def _mcp_text(payload: str) -> str | None:
    """Parse the JSON or SSE response shape used by OpenCode MCP search."""
    candidates = [payload.strip()]
    candidates.extend(line[6:].strip() for line in payload.splitlines() if line.startswith("data: "))
    for candidate in candidates:
        if not candidate.startswith("{"):
            continue
        try:
            body = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        result = body.get("result") if isinstance(body, dict) else None
        content = result.get("content") if isinstance(result, dict) else None
        if not isinstance(content, list):
            continue
        texts = [str(item["text"]).strip() for item in content if isinstance(item, dict) and item.get("text")]
        if texts:
            return "\n\n".join(texts)
    return None


def _mcp_call(
    url: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> str:
    request_headers = {"Accept": "application/json, text/event-stream"}
    request_headers.update(headers or {})
    response = httpx.post(
        url,
        headers=request_headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        timeout=MCP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    text = _mcp_text(response.text)
    if not text:
        raise RuntimeError(f"{tool} 返回了无法解析的 MCP 响应")
    return text


def _engine_query(query: str) -> str:
    """Preserve the user's search text; trim only accidental outer whitespace."""
    return str(query or "").strip()


def _attempt(provider: str, started: float, **values: Any) -> dict[str, Any]:
    return {
        "provider": provider,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        **values,
    }


def _host(url: str, fallback: str) -> str:
    try:
        return httpx.URL(url).host or fallback
    except Exception:
        return fallback


def _firecrawl_search(
    query: str,
    *,
    limit: int,
    include_content: bool = False,
) -> dict[str, Any]:
    """Search through SearXNG and optionally crawl each result in one request."""
    started = time.perf_counter()
    config = firecrawl_rest_config()
    if config is None:
        return _attempt(
            "firecrawl_searxng",
            started,
            success=False,
            skipped=True,
            error="Firecrawl 本地搜索地址未配置",
            results=[],
        )

    base_url, headers, _ = config
    try:
        timeout_seconds = 70.0 if include_content else FIRECRAWL_SEARCH_TIMEOUT_SECONDS
        request_payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "sources": ["web"],
            "lang": "auto",
            "timeout": int(timeout_seconds * 1000),
        }
        if include_content:
            request_payload["scrapeOptions"] = {
                "formats": ["markdown"],
                "onlyMainContent": True,
            }
        response = httpx.post(
            f"{base_url}/v2/search",
            headers=headers,
            json=request_payload,
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=2.0,
            ),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("success") is not True:
            error = payload.get("error") if isinstance(payload, dict) else None
            raise RuntimeError(str(error or "Firecrawl 搜索返回失败"))

        data = payload.get("data")
        web = data.get("web") if isinstance(data, dict) else None
        results: list[dict[str, Any]] = []
        for item in web if isinstance(web, list) else []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not url or not title:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            content = str(item.get("markdown") or "").strip() if include_content else ""
            if re.search(
                r"(?:Access Verification|slide to complete the verification|人机验证|验证码)",
                content[:1200],
                flags=re.I,
            ):
                content = ""
            published_date = (
                item.get("publishedDate")
                or metadata.get("publishedTime")
                or metadata.get("article:published_time")
                or metadata.get("date")
            )
            row = {
                "title": title,
                "url": url,
                "snippet": str(item.get("description") or "").strip(),
                "source": _host(url, "web"),
                "published_date": published_date,
                "result_type": "web",
                "search_provider": "firecrawl_searxng",
            }
            if content:
                row["content_text"] = content
                row["content_characters"] = len(content)
                row["crawl_provider"] = "firecrawl"
            results.append(row)
            if len(results) >= limit:
                break

        return _attempt(
            "firecrawl_searxng",
            started,
            success=bool(results),
            skipped=False,
            error=None if results else "Firecrawl/SearXNG 未返回网页结果",
            results=results,
        )
    except Exception as exc:
        return _attempt(
            "firecrawl_searxng",
            started,
            success=False,
            skipped=False,
            error=str(exc),
            results=[],
        )


def _results_from_mcp_text(
    output: str,
    provider: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Best-effort structure while retaining the provider's original output."""
    url_pattern = re.compile(r"https?://[^\s<>\])}]+")
    results: list[dict[str, Any]] = []
    lines = output.splitlines()
    for index, line in enumerate(lines):
        match = url_pattern.search(line)
        if not match:
            continue
        url = match.group(0).rstrip(".,;，。；")
        title = ""
        for candidate in reversed(lines[max(0, index - 3) : index + 1]):
            cleaned = re.sub(
                r"^(?:#+|[-*]|\d+[.)]|Title:|标题[:：])\s*",
                "",
                candidate,
            ).strip()
            if cleaned and "http://" not in cleaned and "https://" not in cleaned:
                title = cleaned
                break
        if not title:
            title = _host(url, provider)
        if any(item["url"] == url for item in results):
            continue
        results.append(
            {
                "title": title[:300],
                "url": url,
                "snippet": "",
                "source": _host(url, provider),
                "published_date": None,
                "result_type": "web",
                "search_provider": provider,
            }
        )
        if len(results) >= limit:
            break
    return results


def _exa_search(
    query: str,
    *,
    limit: int,
    livecrawl: str,
    search_type: str,
    context_max_characters: int | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        url = EXA_MCP_URL
        if os.getenv("EXA_API_KEY"):
            url = f"{url}?{urlencode({'exaApiKey': os.environ['EXA_API_KEY']})}"
        arguments: dict[str, Any] = {
            "query": query,
            "type": search_type,
            "numResults": limit,
            "livecrawl": livecrawl,
        }
        if context_max_characters is not None:
            arguments["contextMaxCharacters"] = context_max_characters
        output = _mcp_call(url, "web_search_exa", arguments)
        return _attempt(
            "exa",
            started,
            success=bool(output.strip()),
            skipped=False,
            error=None,
            results=_results_from_mcp_text(output, "exa", limit),
            output=output,
            authenticated=bool(os.getenv("EXA_API_KEY")),
        )
    except Exception as exc:
        return _attempt(
            "exa",
            started,
            success=False,
            skipped=False,
            error=str(exc),
            results=[],
            output="",
        )


def _parallel_search(query: str, *, limit: int, session_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        headers = {"User-Agent": "opencode/stock-agent"}
        key = os.getenv("PARALLEL_API_KEY", "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        arguments: dict[str, Any] = {
            "objective": query,
            "search_queries": [query],
        }
        if session_id:
            arguments["session_id"] = session_id
        output = _mcp_call(
            PARALLEL_MCP_URL,
            "web_search",
            arguments,
            headers=headers,
        )
        return _attempt(
            "parallel",
            started,
            success=bool(output.strip()),
            skipped=False,
            error=None,
            results=_results_from_mcp_text(output, "parallel", limit),
            output=output,
            authenticated=bool(key),
        )
    except Exception as exc:
        return _attempt(
            "parallel",
            started,
            success=False,
            skipped=False,
            error=str(exc),
            results=[],
            output="",
        )


def _provider_order(query: str = "") -> list[str]:
    del query
    return ["firecrawl", "exa", "parallel"]


def _compact_results(
    results: list[dict[str, Any]],
    *,
    limit: int,
    max_chars: int,
    include_content: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    compacted: list[dict[str, Any]] = []
    used = 0
    truncated = False
    selected = results[:limit]
    per_item_content_limit = max(1000, min(6000, max_chars // max(1, len(selected))))
    for item in selected:
        row = dict(item)
        snippet = str(row.get("snippet") or "")
        remaining = max_chars - used
        if remaining <= 0:
            truncated = True
            break
        if len(snippet) > remaining:
            snippet = snippet[:remaining].rstrip() + "…"
            truncated = True
        row["snippet"] = snippet
        used += len(snippet)
        if include_content:
            content = str(row.get("content_text") or "")
            content_limit = min(per_item_content_limit, max(0, max_chars - used))
            if len(content) > content_limit:
                content = content[:content_limit].rstrip() + "…" if content_limit else ""
                truncated = True
            row["content_text"] = content
            used += len(content)
        else:
            row.pop("content_text", None)
            row.pop("content_characters", None)
        compacted.append(row)
    return compacted, truncated


def websearch(
    query: str,
    num_results: int = 8,
    livecrawl: str = "fallback",
    search_type: str = "auto",
    context_max_characters: int | None = None,
    session_id: str = "",
    include_content: bool = False,
) -> dict[str, Any]:
    resolved_query = _engine_query(query)
    if not resolved_query:
        raise ValueError("query 不能为空")
    if livecrawl not in {"fallback", "preferred"}:
        raise ValueError("livecrawl 必须是 fallback 或 preferred")
    if search_type not in {"auto", "fast", "deep"}:
        raise ValueError("type 必须是 auto、fast 或 deep")

    limit = max(1, min(int(num_results), 20))
    max_chars = max(1000, min(int(context_max_characters or 12000), 50000))
    attempts: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None

    for provider in _provider_order():
        if provider == "firecrawl":
            current = _firecrawl_search(
                resolved_query,
                limit=limit,
                include_content=include_content,
            )
        elif provider == "exa":
            current = _exa_search(
                resolved_query,
                limit=limit,
                livecrawl=livecrawl,
                search_type=search_type,
                context_max_characters=context_max_characters,
            )
        else:
            current = _parallel_search(
                resolved_query,
                limit=limit,
                session_id=session_id,
            )

        attempts.append({key: value for key, value in current.items() if key not in {"results", "output"}})
        if current.get("success"):
            selected = current
            break

    selected_results = list((selected or {}).get("results") or [])
    compacted, truncated = _compact_results(
        selected_results,
        limit=limit,
        max_chars=max_chars,
        include_content=include_content,
    )
    provider_output = str((selected or {}).get("output") or "")
    success = selected is not None and bool(compacted or provider_output)
    provider = str((selected or {}).get("provider") or "none")

    published: list[datetime] = []
    for item in compacted:
        value = item.get("published_date")
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            published.append(parsed)
        except ValueError:
            continue
    latest = max(published) if published else None
    days = 365 if search_type == "deep" else 30
    failures = [str(item["error"]) for item in attempts if item.get("error") and not item.get("skipped")]

    return {
        "query": resolved_query,
        "resolved_query": resolved_query,
        "success": success,
        "results": compacted,
        "result_count": len(compacted),
        "output": provider_output,
        "provider": provider,
        "attempts": attempts,
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "data_time": latest.isoformat() if latest else None,
        "fallback_used": success and provider != "firecrawl_searxng",
        "is_stale": (latest < datetime.now().astimezone() - timedelta(days=days) if latest else None),
        "freshness_unknown": latest is None,
        "latest_published_date": latest.isoformat() if latest else None,
        "_truncated": truncated,
        "errors": [] if success else failures,
        "warnings": failures if success else [],
        "content_requested": bool(include_content),
        "content_result_count": sum(bool(item.get("content_text")) for item in compacted),
    }


def _execute(
    query: str,
    numResults: int = 8,
    livecrawl: str = "fallback",
    type: str = "auto",
    contextMaxCharacters: int | None = None,
    sessionId: str = "",
    includeContent: bool = False,
) -> dict[str, Any]:
    return websearch(
        query=query,
        num_results=numResults,
        livecrawl=livecrawl,
        search_type=type,
        context_max_characters=contextMaxCharacters,
        session_id=sessionId,
        include_content=includeContent,
    )


TOOL = ToolSpec(
    name="websearch",
    description=WEBSEARCH_DESCRIPTION,
    parameters=object_schema(
        {
            "query": {"type": "string", "description": "原样发送给搜索引擎的查询内容"},
            "numResults": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 8,
            },
            "livecrawl": {
                "type": "string",
                "enum": ["fallback", "preferred"],
                "default": "fallback",
            },
            "type": {
                "type": "string",
                "enum": ["auto", "fast", "deep"],
                "default": "auto",
            },
            "contextMaxCharacters": {
                "type": "integer",
                "minimum": 1000,
                "maximum": 50000,
                "default": 12000,
            },
            "includeContent": {
                "type": "boolean",
                "default": False,
                "description": "是否让本地 Firecrawl 在搜索时同步抓取结果页正文",
            },
        },
        ["query"],
    ),
    executor=_execute,
    category="search",
)
