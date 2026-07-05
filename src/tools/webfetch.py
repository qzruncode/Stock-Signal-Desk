# -*- coding: utf-8 -*-
"""webfetch 工具 —— 复刻 OpenCode packages/opencode/src/tool/webfetch.ts

抓取指定 URL 的内容，按 format 返回 markdown / text / html。
逐行对应 webfetch.ts 的 execute 逻辑：

- 校验 http(s) URL
- 按 format 构造带 q 优先级的 Accept 头
- Chrome UA 发请求；若命中 Cloudflare challenge (403 + cf-mitigated: challenge) 则
  用诚实 UA "opencode" 重试一次
- 5MB 响应上限（content-length 头 + 实际字节数双重校验）
- 图片 MIME → base64 data URL 附件
- markdown: HTML→markdownify（对应 turndown，未安装时降级 bs4 纯文本）
- text: HTML→extract_text_from_html（对应 extractTextFromHTML，跳过 script/style 等）
- html: 原样

不依赖项目其它模块。
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# --- 常量（与 webfetch.ts 完全一致）---
MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5MB
DEFAULT_TIMEOUT = 30  # seconds
MAX_TIMEOUT = 120  # seconds (2 minutes)

# Chrome UA（与 webfetch.ts 同款字符串）
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)
# Cloudflare challenge 命中后重试用的诚实 UA（对应 webfetch.ts 里 retry 时的 "opencode"）
_HONEST_UA = "opencode"

# 需要在 text/markdown 抽取时跳过的 HTML 标签（对应 extractTextFromHTML 的 skip 列表）
_SKIP_TAGS = ["script", "style", "noscript", "iframe", "object", "embed"]


# 复刻 webfetch.txt 的工具说明（保留英文原文以忠实复刻 OpenCode）
WEBFETCH_DESCRIPTION = """- Fetches content from a specified URL
- Takes a URL and optional format as input
- Fetches the URL content, converts to requested format (markdown by default)
- Returns the content in the specified format
- Use this tool when you need to retrieve and analyze web content

Usage notes:
  - IMPORTANT: if another tool is present that offers better web fetching capabilities, is more targeted to the task, or has fewer restrictions, prefer using that tool instead of this one.
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - Format options: "markdown" (default), "text", or "html"
  - This tool is read-only and does not modify any files
  - Results may be summarized if the content is very large"""


def _is_image(mime: str) -> bool:
    """对应 webfetch.ts 的 isImageAttachment。"""
    return mime.startswith("image/")


def _accept_header_for(fmt: str) -> str:
    """对应 webfetch.ts 里按 format 构造 Accept 头的 switch 分支（含 q 优先级 fallback）。"""
    if fmt == "markdown":
        return "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1"
    if fmt == "text":
        return "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1"
    if fmt == "html":
        return "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, text/markdown;q=0.7, */*;q=0.1"
    # default（对应 webfetch.ts 的 default 分支）
    return "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"


def _extract_text_from_html(html: str) -> str:
    """对应 webfetch.ts 的 extractTextFromHTML。

    用 htmlparser2 解析时跳过 script/style/noscript/iframe/object/embed。
    Python 侧用 bs4 实现等价语义；bs4 不可用时降级正则。
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        import re

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
    return soup.get_text().strip()


def _convert_html_to_markdown(html: str) -> str:
    """对应 webfetch.ts 的 convertHTMLToMarkdown（turndown）。

    turndown 配置：headingStyle: atx, hr: ---, bulletListMarker: -, codeBlockStyle: fenced, emDelimiter: *
    turndown 只处理 body，并用 .remove(["script","style","meta","link"]) 连标签带内容一起删。
    Python 侧用 bs4 先做等价清理（移除 head/script/style/meta/link/title 等，只保留 body），
    再交给 markdownify 转换；markdownify 未安装时降级到 bs4 纯文本。
    """
    try:
        from markdownify import markdownify as md
    except ImportError:
        logger.debug("markdownify 未安装，降级为 bs4 纯文本抽取")
        return _extract_text_from_html(html)

    # 先用 bs4 移除 script/style/meta/link/title/noscript/iframe 等标签及其内容，
    # 并只取 <body>（对应 turndown 只处理 body、不输出 <head> 内容的语义）。
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # bs4 也不可用：直接交给 markdownify + strip 兜底
        return md(
            html,
            heading_style="ATX",
            bullets="-",
            code_language="",
            strip=["script", "style", "meta", "link", "title"],
        )

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "meta", "link", "title", "noscript", "iframe", "object", "embed"]):
        tag.decompose()
    body = soup.body or soup
    return md(
        str(body),
        heading_style="ATX",   # 对应 headingStyle: "atx"
        bullets="-",           # 对应 bulletListMarker: "-"
        code_language="",      # fenced code block（对应 codeBlockStyle: "fenced"）
        strip=["script", "style", "meta", "link"],  # 双保险
    )


def fetch_url(
    url: str,
    format: str = "markdown",
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Fetch content from a URL and return it in the requested format.

    复刻 OpenCode webfetch.ts 的 execute 函数。

    Args:
        url: 要抓取的 URL，必须以 http:// 或 https:// 开头
        format: 返回格式，markdown / text / html，默认 markdown
        timeout: 超时秒数，最大 120；不传则默认 30

    Returns:
        dict: {url, format, content_type, title, content, attachments?}
    """
    # 1. URL 校验（对应 webfetch.ts 开头的 startswith 检查）
    if not url.startswith("http://") and not url.startswith("https://"):
        raise ValueError("URL must start with http:// or https://")

    fmt = format or "markdown"
    if fmt not in ("markdown", "text", "html"):
        raise ValueError(f"format must be one of markdown/text/html, got {fmt!r}")

    # 2. 超时计算：min(timeout ?? 30s, 120s)（对应 TS 的 Math.min(...)）
    timeout_seconds = min(
        (timeout if timeout is not None else DEFAULT_TIMEOUT),
        MAX_TIMEOUT,
    )

    # 3. 构造请求头（Chrome UA + 按 format 的 Accept 头）
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": _accept_header_for(fmt),
        "Accept-Language": "en-US,en;q=0.9",
    }

    with httpx.Client(follow_redirects=True) as client:
        # 4. 发请求；若命中 Cloudflare challenge 则用诚实 UA 重试一次（对应 Effect.catchIf）
        resp = client.get(url, headers=headers, timeout=timeout_seconds)
        if resp.status_code == 403 and resp.headers.get("cf-mitigated") == "challenge":
            logger.debug("webfetch: Cloudflare challenge hit, retrying with honest UA 'opencode'")
            retry_headers = {**headers, "User-Agent": _HONEST_UA}
            resp = client.get(url, headers=retry_headers, timeout=timeout_seconds)

        # 非 2xx 抛错（对应 httpOk = HttpClient.filterStatusOk(http)）
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code} for {url}",
                request=resp.request,
                response=resp,
            )

        # 5. content-length 头预检（对应 TS 的 content-length 检查）
        content_length = resp.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_RESPONSE_SIZE:
            raise ValueError("Response too large (exceeds 5MB limit)")

        # 6. 实际字节数校验（对应 TS 的 arrayBuffer.byteLength 检查）
        content_bytes = resp.content
        if len(content_bytes) > MAX_RESPONSE_SIZE:
            raise ValueError("Response too large (exceeds 5MB limit)")

        content_type = resp.headers.get("content-type", "") or ""
        mime = content_type.split(";")[0].strip().lower() or ""
        title = f"{url} ({content_type})"

        # 7. 图片 → base64 data URL 附件（对应 isImageAttachment 分支）
        if _is_image(mime):
            b64 = base64.b64encode(content_bytes).decode("ascii")
            return {
                "url": url,
                "format": fmt,
                "content_type": content_type,
                "title": title,
                "content": "Image fetched successfully",
                "attachments": [
                    {
                        "type": "file",
                        "mime": mime,
                        "url": f"data:{mime};base64,{b64}",
                    }
                ],
            }

        # 8. 按 format + 实际 content-type 转换（对应 TS 的 switch(params.format)）
        text_content = content_bytes.decode("utf-8", errors="replace")

        if fmt == "markdown":
            if "text/html" in content_type:
                content = _convert_html_to_markdown(text_content)
            else:
                content = text_content
        elif fmt == "text":
            if "text/html" in content_type:
                content = _extract_text_from_html(text_content)
            else:
                content = text_content
        else:  # html
            content = text_content

        return {
            "url": url,
            "format": fmt,
            "content_type": content_type,
            "title": title,
            "content": content,
            "attachments": None,
        }
