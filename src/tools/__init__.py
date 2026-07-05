# -*- coding: utf-8 -*-
"""独立 LLM 工具集合。

这里的工具完全复刻 OpenCode (`sst/opencode`, packages/opencode/src/tool/) 的实现，
不依赖本项目其它模块（如 src.search_service），仅依赖 httpx / markdownify / bs4。

- webfetch: 抓取 URL 内容，按 markdown / text / html 返回（复刻 webfetch.ts）
- websearch: 通过 MCP JSON-RPC 调用 Exa / Parallel 搜索（复刻 websearch.ts + mcp-websearch.ts）
"""

from src.tools.webfetch import WEBFETCH_DESCRIPTION, fetch_url
from src.tools.websearch import WEBSEARCH_DESCRIPTION, websearch

__all__ = [
    "WEBFETCH_DESCRIPTION",
    "WEBSEARCH_DESCRIPTION",
    "fetch_url",
    "websearch",
]
