from src.services.market_data_client import read_source

"""RSS feed URL building and HTTP fetching."""
import logging
import json
import os
import re
from html import escape, unescape
from html.parser import HTMLParser
from datetime import datetime
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote, urlencode, urlparse
import feedparser
import requests
from src.config import Config
from api.v1.endpoints._rss_routes import RSSHUB_ROUTES
from api.v1.endpoints._rss_text import normalize_text_item

logger = logging.getLogger(__name__)
_PARAM_RE = re.compile(":([a-zA-Z_][a-zA-Z0-9_]*)(\\{[^}]+\\})?(\\?)?")


def _readable_http_url(value: Any) -> str:
    """Return a browser-readable article URL, never a GUID or relative identifier."""
    if not value:
        return ""
    candidate = str(value).strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    return candidate if parsed.scheme in {"http", "https"} and parsed.netloc else ""


class _FeedTextExtractor(HTMLParser):
    """Small dependency-free HTML-to-text converter for RSS descriptions."""

    _BLOCK_TAGS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


_ENCODED_HTML_RE = re.compile(
    "&lt;\\s*/?\\s*(?:p|div|a|span|br|ul|ol|li|h[1-6]|table)\\b", re.I
)


def _normalize_content_html(html_value: Any, text_value: Any = "") -> str:
    """Normalize RSS content while preserving rich markup for safe frontend rendering."""
    raw_html = str(html_value or "").strip()
    if raw_html:
        for _ in range(2):
            if not _ENCODED_HTML_RE.search(raw_html):
                break
            decoded = unescape(raw_html)
            if decoded == raw_html:
                break
            raw_html = decoded
        return raw_html
    raw_text = str(text_value or "").strip()
    return escape(raw_text).replace("\n", "<br>") if raw_text else ""


def _html_to_text(value: Any) -> str:
    """Return readable plain text from raw, encoded, or malformed RSS HTML."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    for _ in range(2):
        decoded = unescape(raw)
        if decoded == raw:
            break
        raw = decoded
    parser = _FeedTextExtractor()
    try:
        parser.feed(raw)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = re.sub("<[^>]+>", " ", raw)
    lines = [re.sub("[ \\t\\r\\f\\v]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join((line for line in lines if line)).strip()


def _derive_item_title(title: Any, body_text: str) -> str:
    existing = _html_to_text(title)
    if existing:
        return existing
    first_line = next(
        (line.strip() for line in body_text.splitlines() if line.strip()), ""
    )
    if not first_line:
        return ""
    if len(first_line) <= 80:
        return first_line
    return first_line[:79].rstrip() + "…"


def _is_unresolvable_truncated_item(title: str, link: str, body_text: str = "") -> bool:
    """Hide items that are visibly truncated and provide no document to resolve."""
    if link or not (title.rstrip().endswith("...") or title.rstrip().endswith("…")):
        return False
    normalized_title = title.rstrip(". …").strip()
    normalized_body = body_text.rstrip(". …").strip()
    return (
        not normalized_body
        or normalized_body == normalized_title
        or body_text.rstrip().endswith(("...", "…"))
    )


_PARAM_FORMATTERS: Dict[str, Dict[str, Callable[[str], str]]] = {
    "xueqiu": {"id": lambda v: _stock_code_to_rsshub_id(v)}
}
_PARAM_FORMATTERS_BY_ROUTE: Dict[str, Dict[str, Callable[[str], str]]] = {
    "/xueqiu/fund/:id": {"id": lambda v: v}
}
_PRIVILEGED_RSS_OPTIONS = frozenset(
    {
        "chatgpt",
        "image_hotlink_template",
        "multimedia_hotlink_template",
        "scihub",
        "tgiv",
    }
)


def _server_managed_rss_options() -> Dict[str, Any]:
    raw = str(os.getenv("RSSHUB_AGENT_PRIVILEGED_OPTIONS") or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("RSSHUB_AGENT_PRIVILEGED_OPTIONS is not valid JSON")
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if str(key) in _PRIVILEGED_RSS_OPTIONS
    }


def _normalize_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Drop falsy / empty values; stringify keys. RSSHub ignores empty params anyway."""
    out: Dict[str, Any] = {}
    privileged = _server_managed_rss_options()
    for k, v in (options or {}).items():
        key = str(k).strip()
        if not key or v is None:
            continue
        if key in _PRIVILEGED_RSS_OPTIONS:
            if key not in privileged:
                raise PermissionError(f"RSSHub 特权参数 {key} 未由服务端策略启用")
            if str(v).strip().casefold() not in {"1", "enabled", "server", "true"}:
                raise PermissionError(f"RSSHub 特权参数 {key} 只能请求服务端托管值")
            v = privileged[key]
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        elif isinstance(v, bool):
            v = "true" if v else "false"
        out[key] = v
    return out


def _build_feed_url_generic(
    route_path: str,
    params: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    namespace: Optional[str] = None,
) -> str:
    """Build a RSSHub feed URL from a generic route template + params + universal options.

    ``route_path`` is the RSSHub route (e.g. ``/wallstreetcn/news/:category?``).
    Path params are substituted; optional params with empty/missing values are
    dropped from the path. Universal ``options`` become the query string.
    Raises ``ValueError`` when a required path param is missing.
    """
    base_url = Config.get_instance().rsshub_base_url.rstrip("/")
    path: str = route_path or ""
    params = params or {}

    def _resolve(name: str) -> Optional[str]:
        raw = params.get(name)
        if raw is None:
            return None
        val = str(raw).strip()
        if not val:
            return None
        formatter = _PARAM_FORMATTERS_BY_ROUTE.get(path, {}).get(
            name
        ) or _PARAM_FORMATTERS.get(namespace or "", {}).get(name)
        if formatter:
            try:
                val = formatter(val)
            except Exception:
                pass
        return val

    def _sub(match: re.Match) -> str:
        name, _regex, opt_q = (match.group(1), match.group(2), match.group(3))
        optional = opt_q == "?"
        val = _resolve(name)
        if val:
            return quote(val, safe="")
        if optional:
            return ""
        raise ValueError(f"缺少必填参数: {name}")

    rendered = _PARAM_RE.sub(_sub, path)
    rendered = re.sub("/+", "/", rendered)
    if rendered.endswith("/") and len(rendered) > 1:
        rendered = rendered.rstrip("/")
    if not rendered.startswith("/"):
        rendered = "/" + rendered
    opts = _normalize_options(options)
    qs = urlencode(opts) if opts else ""
    return f"{base_url}{rendered}{('?' + qs if qs else '')}"


def _stock_code_to_rsshub_id(code: str) -> str:
    """将 6 位股票代码转为 RSSHub/雪球格式 (SH600519, SZ000002, BJ430001)。"""
    c = code.strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if c.startswith(prefix):
            c = c[len(prefix) :]
            break
    if "." in c:
        c = c.split(".")[0]
    if not c.isdigit() or len(c) != 6:
        return code
    if c[0] in ("6", "9"):
        return f"SH{c}"
    elif c[0] in ("0", "2", "3"):
        return f"SZ{c}"
    else:
        return f"BJ{c}"


def _normalize_stock_code(code: str) -> str:
    """将股票代码统一为 6 位纯数字，供交易所公告路由使用。"""
    raw_code = code.strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if raw_code.startswith(prefix):
            raw_code = raw_code[len(prefix) :]
            break
    if "." in raw_code:
        raw_code = raw_code.split(".")[0]
    return raw_code


def _build_feed_url(
    source: str,
    stock_code: Optional[str] = None,
    type: Optional[str] = None,
    category: Optional[str] = None,
    keyword: Optional[str] = None,
    uid: Optional[str] = None,
) -> str:
    """根据 source 和参数构建 RSSHub feed URL。"""
    route_info = RSSHUB_ROUTES.get(source)
    if not route_info:
        raise ValueError(f"未知的 RSS 源: {source}")
    base_url = Config.get_instance().rsshub_base_url.rstrip("/")
    path: str = route_info["path"]
    if "{id}" in path:
        if not stock_code:
            raise ValueError(f"源 {source} 需要提供 stock_code 参数")
        rsshub_id = _stock_code_to_rsshub_id(stock_code)
        path = path.replace("{id}", rsshub_id)
    if "{type}" in path:
        t = type or route_info.get("default_type", "news")
        path = path.replace("{type}", t)
    if "{category}" in path:
        cat = category or route_info.get("default_category", "")
        path = path.replace("{category}", cat)
    if "{keyword}" in path:
        if not keyword:
            raise ValueError(f"源 {source} 需要提供 keyword 参数")
        path = path.replace("{keyword}", quote(keyword.strip(), safe=""))
    if "{uid}" in path:
        if not uid:
            raise ValueError(f"源 {source} 需要提供 uid 参数")
        path = path.replace("{uid}", quote(uid.strip(), safe=""))
    if "{query}" in path:
        if not stock_code:
            raise ValueError(f"源 {source} 需要提供 stock_code 参数")
        raw_code = _normalize_stock_code(stock_code)
        path = path.replace("{query}", f"stock={raw_code}")
    return f"{base_url}{path}"


def _fetch_rss_feed(url: str, limit: int = 20, timeout: float = 15.0) -> dict:
    return read_source(
        "rss.transport._fetch_rss_feed",
        {"url": url, "limit": limit, "timeout": timeout},
    )


def _authors_to_str(authors: Any) -> str:
    """JSON Feed authors (list of {name,url,avatar} or str) → 逗号分隔字符串。"""
    if not authors:
        return ""
    if isinstance(authors, str):
        return authors
    if isinstance(authors, list):
        names = []
        for a in authors:
            if isinstance(a, dict):
                n = a.get("name") or ""
                if n:
                    names.append(str(n))
            elif isinstance(a, str):
                names.append(a)
        return ", ".join(names)
    return str(authors)


def _attachments_from_json(att: Any) -> list:
    """归一化 JSON Feed attachments（音频/视频/图片直链）。"""
    if not isinstance(att, list):
        return []
    out = []
    for a in att:
        if not isinstance(a, dict):
            continue
        url = a.get("url")
        if not url:
            continue
        out.append(
            {
                "url": str(url),
                "mime_type": str(a.get("mime_type") or a.get("mime") or ""),
                "title": str(a.get("title") or ""),
                "size_in_bytes": a.get("size_in_bytes"),
                "duration_in_seconds": a.get("duration_in_seconds"),
            }
        )
    return out


def _fetch_rss_feed_json(url: str, limit: int = 20, timeout: float = 20.0) -> dict:
    return read_source(
        "rss.transport._fetch_rss_feed_json",
        {"url": url, "limit": limit, "timeout": timeout},
    )
