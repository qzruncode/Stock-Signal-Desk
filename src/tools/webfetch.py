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



from . import _webfetch_functions1 as _webfetch_functions1
from . import _webfetch_functions2 as _webfetch_functions2


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_webfetch_functions1, _webfetch_functions2):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))


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
