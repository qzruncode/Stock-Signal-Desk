# -*- coding: utf-8 -*-
"""websearch 工具 —— 复刻 OpenCode packages/opencode/src/tool/websearch.ts + mcp-websearch.ts

通过 MCP JSON-RPC 2.0 `tools/call` 调用 Exa / Parallel 搜索后端。
逐行对应 websearch.ts / mcp-websearch.ts 的逻辑：

- provider 选择：env OPENCODE_WEBSEARCH_PROVIDER 覆盖 > flags > hash(sessionID) % 2
  （Exa / Parallel 双引擎 A/B 分流）
- Exa: POST https://mcp.exa.ai/mcp，tool=web_search_exa
  （有 EXA_API_KEY 时拼到 query string）
- Parallel: POST https://search.parallel.ai/mcp，tool=web_search
  （有 PARALLEL_API_KEY 时带 Authorization: Bearer；UA opencode/<version>）
- 请求体：{jsonrpc:"2.0", id:1, method:"tools/call", params:{name, arguments}}
- Accept: application/json, text/event-stream
- 25 秒超时
- 响应解析：先尝试直接 JSON，否则逐行扫 `data: ` SSE 前缀（兼容 SSE 流）

不依赖项目其它模块。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# --- 常量（复刻 mcp-websearch.ts）---
# Exa：有 EXA_API_KEY 时拼到 query string，否则用裸 URL
EXA_URL = (
    f"https://mcp.exa.ai/mcp?exaApiKey={os.getenv('EXA_API_KEY')}"
    if os.getenv("EXA_API_KEY")
    else "https://mcp.exa.ai/mcp"
)
PARALLEL_URL = "https://search.parallel.ai/mcp"

# MCP 调用超时（复刻 TS 里的 "25 seconds"）
_MCP_TIMEOUT_SECONDS = 25.0

# 工具版本号，用于 Parallel 请求的 User-Agent（对应 OpenCode 的 InstallationVersion）
_TOOL_VERSION = "1.0.0-port"


# 复刻 websearch.txt 的工具说明（保留英文原文以忠实复刻 OpenCode）
# 注：{{year}} 占位符由 tool_registry 在注册时替换为当前年份（对应 websearch.ts 的 description getter）
WEBSEARCH_DESCRIPTION = """- Search the web using the session's web search provider - performs real-time web searches and can scrape content from specific URLs
- Provides up-to-date information for current events and recent data
- Supports configurable result counts and returns the content from the most relevant websites
- Use this tool for accessing information beyond knowledge cutoff
- Searches are performed automatically within a single API call

Usage notes:
  - Supports live crawling modes when available: 'fallback' (backup if cached unavailable) or 'preferred' (prioritize live crawling)
  - Search types when available: 'auto' (balanced), 'fast' (quick results), 'deep' (comprehensive search)
  - Configurable context length for optimal LLM integration
  - Domain filtering and advanced search options available

The current year is {{year}}. You MUST use this year when searching for recent information or current events
- Example: If the current year is 2026 and the user asks for "latest AI news", search for "AI news 2026", NOT "AI news 2025\""""


def _env_flag(name: str) -> bool:
    """读取布尔型环境变量。"""
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _checksum(value: str) -> str:
    """对应 OpenCode @opencode-ai/core/util/encode 的 checksum。

    OpenCode 用它对 sessionID 取摘要后做 `parseInt(checksum, 36) % 2` 分流。
    这里用 sha1 hex 前 8 位（hex 是 base-36 的子集，可直接按 36 进制解析）。
    """
    return hashlib.sha1((value or "").encode("utf-8")).hexdigest()[:8]


def select_websearch_provider(
    session_id: str,
    exa: bool = False,
    parallel: bool = False,
) -> str:
    """对应 websearch.ts 的 selectWebSearchProvider。

    返回 "exa" 或 "parallel"。
    """
    override = os.getenv("OPENCODE_WEBSEARCH_PROVIDER")
    if override in ("exa", "parallel"):
        return override
    if parallel:
        return "parallel"
    if exa:
        return "exa"
    # hash(sessionID) % 2 做 A/B 分流（复刻）
    cs = _checksum(session_id or "0")
    try:
        return "exa" if int(cs, 36) % 2 == 0 else "parallel"
    except ValueError:
        return "exa"


def _web_search_provider_label(provider: str) -> str:
    """对应 websearch.ts 的 webSearchProviderLabel。"""
    if provider == "parallel":
        return "Parallel Web Search"
    if provider == "exa":
        return "Exa Web Search"
    return "Web Search"


def _parallel_auth_headers() -> Dict[str, str]:
    """对应 websearch.ts 的 parallelAuthHeaders。"""
    headers = {"User-Agent": f"opencode/{_TOOL_VERSION}"}
    if os.getenv("PARALLEL_API_KEY"):
        headers["Authorization"] = f"Bearer {os.getenv('PARALLEL_API_KEY')}"
    return headers


# ---------------------------------------------------------------------------
# MCP 调用（复刻 mcp-websearch.ts）
# ---------------------------------------------------------------------------


def _parse_payload(payload: str) -> Optional[str]:
    """对应 mcp-websearch.ts 的 parsePayload：从单段 JSON 里取 result.content[].text。"""
    payload = payload.strip()
    if not payload.startswith("{"):
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    if not isinstance(content, list):
        return None
    for item in content:
        if isinstance(item, dict) and item.get("text"):
            return item["text"]
    return None


def _parse_response(body: str) -> Optional[str]:
    """对应 mcp-websearch.ts 的 parseResponse。

    先尝试整体 JSON 解析；失败则逐行扫 `data: ` SSE 前缀。
    """
    trimmed = body.strip()
    if trimmed:
        direct = _parse_payload(trimmed)
        if direct:
            return direct

    for line in body.split("\n"):
        if not line.startswith("data: "):
            continue
        text = _parse_payload(line[len("data: "):])
        if text:
            return text
    return None


def _mcp_call(
    url: str,
    tool: str,
    arguments: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """对应 mcp-websearch.ts 的 call()。

    POST JSON-RPC 2.0 tools/call，Accept 同时声明 JSON 与 SSE，
    25 秒超时；返回解析后的 text 内容。
    """
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    req_headers: Dict[str, str] = {
        "Accept": "application/json, text/event-stream",
    }
    if headers:
        req_headers.update(headers)

    with httpx.Client(timeout=_MCP_TIMEOUT_SECONDS) as client:
        resp = client.post(url, headers=req_headers, json=body)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{tool} request failed: HTTP {resp.status_code}",
                request=resp.request,
                response=resp,
            )
        return _parse_response(resp.text)


# ---------------------------------------------------------------------------
# Provider 调用（对应 websearch.ts 的 callProvider）
# ---------------------------------------------------------------------------


def _call_provider(
    provider: str,
    query: str,
    num_results: int,
    livecrawl: str,
    search_type: str,
    context_max_characters: Optional[int],
    session_id: str,
) -> Optional[str]:
    """对应 websearch.ts 的 callProvider。"""
    if provider == "parallel":
        # Parallel：tool=web_search，args={objective, search_queries, session_id, model_name?}
        args: Dict[str, Any] = {
            "objective": query,
            "search_queries": [query],
            "session_id": session_id or None,
            # model_name 对应 webSearchModelName(ctx.extra)；本工具无模型上下文，省略
        }
        # 移除值为 None 的可选字段（JSON-RPC 不需要）
        args = {k: v for k, v in args.items() if v is not None}
        return _mcp_call(
            PARALLEL_URL,
            "web_search",
            args,
            headers=_parallel_auth_headers(),
        )

    # Exa：tool=web_search_exa，args={query, type, numResults, livecrawl, contextMaxCharacters?}
    exa_args: Dict[str, Any] = {
        "query": query,
        "type": search_type or "auto",
        "numResults": num_results or 8,
        "livecrawl": livecrawl or "fallback",
    }
    if context_max_characters is not None:
        exa_args["contextMaxCharacters"] = context_max_characters
    return _mcp_call(EXA_URL, "web_search_exa", exa_args)


# ---------------------------------------------------------------------------
# 工具入口
# ---------------------------------------------------------------------------


def websearch(
    query: str,
    num_results: int = 8,
    livecrawl: str = "fallback",
    search_type: str = "auto",
    context_max_characters: Optional[int] = None,
    session_id: str = "",
) -> Dict[str, Any]:
    """执行 web 搜索（复刻 websearch.ts 的 execute）。

    Args:
        query: 搜索关键词
        num_results: 返回结果数，默认 8
        livecrawl: 实时爬取模式 'fallback' / 'preferred'，默认 fallback
        search_type: 搜索类型 'auto' / 'fast' / 'deep'，默认 auto
        context_max_characters: 上下文最大字符数，默认 None（由 provider 默认）
        session_id: 会话 ID，用于 provider A/B 分流；为空时用 query 兜底

    Returns:
        dict: {output, title, provider}
    """
    # provider 选择：session_id 为空时用 query 做 A/B 分流（对应 ctx.sessionID）
    effective_session = session_id or query
    provider = select_websearch_provider(
        effective_session,
        exa=_env_flag("WEBSEARCH_ENABLE_EXA"),
        parallel=_env_flag("WEBSEARCH_ENABLE_PARALLEL"),
    )
    title = _web_search_provider_label(provider)
    logger.info("websearch: provider=%s query=%r", provider, query)

    result = _call_provider(
        provider,
        query,
        num_results,
        livecrawl,
        search_type,
        context_max_characters,
        session_id,
    )

    return {
        "output": result or "No search results found. Please try a different query.",
        "title": f"{title}: {query}",
        "provider": provider,
    }
