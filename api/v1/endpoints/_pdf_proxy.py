# -*- coding: utf-8 -*-
"""PDF 代理的 SSRF 防护与抓取 — 供前端 PDF.js 同源渲染南华研报等 PDF。

前端 PDF.js 渲染需要同源 URL（上游 ``mall.nanhua.net`` 跨域无 CORS 头，且响应带
``Content-Disposition: attachment`` 会触发下载）。这里提供安全校验 + 字节抓取，由
``rss.py`` 的 ``GET /pdf/proxy`` 端点调用，返回 ``Content-Disposition: inline`` 的
PDF 字节。

SSRF 防护：host 白名单（起步 ``mall.nanhua.net``，可被 env
``PDF_PROXY_ALLOWED_HOSTS`` 逗号分隔扩展）+ ``ipaddress`` 内网拦截（loopback/private/
link-local/reserved/multicast/unspecified 全拒，任一解析结果为内网即拒，防混合地址
绕过）+ 重定向二次校验 + ``%PDF-`` magic 校验（拒绝把 HTML 错误页当 PDF）。

不做二进制缓存：SQLite TEXT 缓存不适合 PDF 字节；浏览器侧靠响应头
``Cache-Control: private, max-age=3600`` 缓存 1h。
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import shutil
import socket
import subprocess
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 30.0
# 子进程拉取的硬上限（含 curl 启动 + TLS + 传输），略大于 FETCH_TIMEOUT。
_FETCH_PROC_TIMEOUT = 40.0

# 统一 UA（与 _rss_fetch.py / _nanhua_tree.py 一致）。
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _allowed_hosts() -> set[str]:
    """Allowlist of hosts the PDF proxy may fetch. Extendable via env.

    Defaults cover the PDF hosts the RSS feeds actually emit:
    ``mall.nanhua.net`` (南华研报), ``pdf.dfcfw.com`` (东方财富研报 —
    ``/eastmoney/report/:category`` 把非 stock 类别的 item.link 重写为该域
    的 .pdf 直链), ``static.sse.com.cn`` / ``disc.static.szse.cn``（沪深交易所
    公告）以及 ``www.chinaratings.com.cn`` / ``www.wkjyqh.com`` 等研报来源。
    其余 host 仍需经 ``PDF_PROXY_ALLOWED_HOSTS`` 显式放行。
    """
    hosts = {
        "mall.nanhua.net",
        "pdf.dfcfw.com",
        "static.sse.com.cn",
        "disc.static.szse.cn",
        "www.chinaratings.com.cn",
        "www.wkjyqh.com",
    }
    extra = os.environ.get("PDF_PROXY_ALLOWED_HOSTS", "")
    for raw in extra.split(","):
        h = raw.strip().lower()
        if h:
            hosts.add(h)
    return hosts


def _host_is_public(host: str) -> bool:
    """True if every resolved IP for ``host`` is a public address.

    Resolves all A/AAAA records and rejects the host if *any* result is a
    private/loopback/link-local/reserved/multicast/unspecified address — this
    blocks DNS-rebinding-style mixed responses where one record is public and
    another points inward.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    ips = {info[4][0] for info in infos}
    if not ips:
        return False
    for ip_str in ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def is_safe_pdf_url(url: str) -> Optional[str]:
    """Validate ``url`` against the allowlist + public-IP rule.

    Returns the cleaned URL string if safe, ``None`` otherwise.
    """
    if not url:
        return None
    candidate = url.strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = (parsed.hostname or "").lower()
    if not host or host not in _allowed_hosts():
        return None
    if not _host_is_public(host):
        return None
    return candidate


def _looks_like_anti_crawl(content: bytes) -> bool:
    """Heuristic: body is a JS/HTML anti-bot challenge, not a PDF.

    Tencent EdgeOne (e.g. ``pdf.dfcfw.com``) answers ``requests`` with a tiny
    ``<script>...EO_Bot_Ssid...`` challenge that sets a cookie then reloads —
    the ``Content-Type`` lies (``application/pdf``) but the bytes start with
    ``<script``/``<!``/``<html``. A real PDF starts with ``%PDF-``.
    """
    if content[:5] == b"%PDF-":
        return False
    head = content[:64].lstrip()
    return head[:1] in (b"<",) or content[:200].lower().find(b"<script") >= 0


_ACW_ARG_RE = re.compile(rb"var\s+arg1\s*=\s*['\"]([0-9A-Fa-f]{40})['\"]")
_ACW_POSITIONS = (
    15,
    35,
    29,
    24,
    33,
    16,
    1,
    38,
    10,
    9,
    19,
    31,
    40,
    27,
    22,
    23,
    25,
    13,
    6,
    11,
    39,
    18,
    20,
    8,
    14,
    21,
    32,
    26,
    2,
    30,
    7,
    4,
    17,
    5,
    3,
    28,
    34,
    37,
    12,
    36,
)
_ACW_MASK = "3000176000856006061501533003690027800375"


def _acw_cookie_from_challenge(content: bytes) -> Optional[str]:
    """Solve the public ``acw_sc__v2`` permutation/XOR challenge.

    ``static.sse.com.cn`` protects announcement PDFs with a small JavaScript
    challenge.  A browser computes this cookie and reloads, but the server-side
    proxy receives the HTML challenge instead of PDF bytes.  Reproduce the
    deterministic calculation here so SSE announcements remain readable inside
    the app; return ``None`` for every other anti-bot body.
    """
    match = _ACW_ARG_RE.search(content)
    if not match:
        return None
    arg1 = match.group(1).decode("ascii")
    shuffled = "".join(arg1[position - 1] for position in _ACW_POSITIONS)
    try:
        return "".join(
            f"{int(shuffled[index:index + 2], 16) ^ int(_ACW_MASK[index:index + 2], 16):02x}"
            for index in range(0, len(_ACW_MASK), 2)
        )
    except ValueError:
        return None


def _fetch_with_curl(url: str) -> Tuple[bytes, str, str]:
    """Fallback fetch via the system ``curl`` binary.

    Some CDNs (Tencent EdgeOne on ``pdf.dfcfw.com``) fingerprint TLS/HTTP and
    serve a JS challenge to Python's ``requests``/urllib3 stack while letting
    ``curl`` (nghttp2 + system TLS) through with the real PDF. We only reach
    here after ``requests`` returned a non-PDF body, and only when ``curl`` is
    on PATH — otherwise the caller gives up.

    Returns ``(content, content_type, final_url)``. ``url`` is assumed already
    SSRF-validated by the caller; the redirect target is re-checked there.
    Transport errors propagate.

    Body goes to stdout; ``-w`` appends a single ``@@META`` line carrying the
    final URL + content-type so we can re-check the redirect target without
    parsing multi-hop header dumps.
    """
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "-L",
            "--max-redirs",
            "5",
            "--max-time",
            str(int(FETCH_TIMEOUT)),
            "-A",
            _UA,
            "-H",
            "Accept: */*",
            "-H",
            "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
            "-w",
            "\n@@META\t%{url_effective}\t%{content_type}\n",
            url,
        ],
        capture_output=True,
        timeout=_FETCH_PROC_TIMEOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl exit {proc.returncode}: {proc.stderr.decode('utf-8', 'ignore')[:200]}")
    out = proc.stdout
    marker = out.rfind(b"\n@@META\t")
    if marker >= 0:
        body = out[:marker]
        meta = out[marker + len(b"\n@@META\t") :].splitlines()[0]
        parts = meta.split(b"\t", 1)
        final_url = parts[0].decode("latin1", "ignore").strip() or url
        final_ct = parts[1].decode("latin1", "ignore").strip() if len(parts) > 1 else ""
    else:
        body = out
        final_url, final_ct = url, ""
    if not final_ct:
        final_ct = "application/pdf"
    return body, final_ct, final_url


def fetch_pdf(url: str) -> Optional[Tuple[bytes, str]]:
    """Fetch and validate a PDF. Returns ``(content, content_type)`` or ``None``.

    ``None`` means the URL failed safety/magic validation or the upstream
    returned a non-PDF body; caller surfaces a 502. Network exceptions
    propagate so the caller can distinguish transport errors.

    Two-stage fetch: first ``requests`` (fast, sufficient for nanhua etc.);
    if the body looks like an anti-bot JS challenge (EdgeOne on
    ``pdf.dfcfw.com``), retry via the system ``curl`` binary whose TLS/HTTP
    fingerprint the CDN accepts. ``curl`` is optional — absent it, the
    ``requests`` result stands and the caller surfaces the failure.
    """
    safe_url = is_safe_pdf_url(url)
    if not safe_url:
        return None

    session = requests.Session()
    resp = session.get(
        safe_url,
        headers={"User-Agent": _UA},
        timeout=FETCH_TIMEOUT,
        allow_redirects=True,
    )
    resp.raise_for_status()
    acw_cookie = _acw_cookie_from_challenge(resp.content)
    if acw_cookie:
        # Keep the cookies set by the first response (acw_tc/cdn_sec_tc), add
        # the JS-computed cookie, then replay the exact request once.
        session.cookies.set(
            "acw_sc__v2",
            acw_cookie,
            domain=urlparse(resp.url).hostname or urlparse(safe_url).hostname,
            path="/",
        )
        resp = session.get(
            safe_url,
            headers={"User-Agent": _UA},
            timeout=FETCH_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
    # Re-check the *final* URL after redirects — a whitelisted host could 302
    # to an internal address.
    if not is_safe_pdf_url(resp.url):
        logger.warning("[PDF] redirect to disallowed URL: %s -> %s", safe_url, resp.url)
        return None
    content = resp.content
    if content[:5] != b"%PDF-" and _looks_like_anti_crawl(content):
        logger.info("[PDF] anti-crawl body from %s, retrying via curl", safe_url)
        curl_result = _fetch_with_curl_or_none(safe_url)
        if curl_result is not None:
            body, ct, final_url = curl_result
            if not is_safe_pdf_url(final_url):
                logger.warning("[PDF] curl redirect to disallowed URL: %s -> %s", safe_url, final_url)
                return None
            if body[:5] == b"%PDF-":
                return body, ct
            logger.warning("[PDF] curl still non-PDF from %s (first bytes: %r)", safe_url, body[:16])
        # curl unavailable or still failed: fall through to the generic warning.
    # Magic check: reject HTML error pages / non-PDF payloads.
    if not content[:5] == b"%PDF-":
        logger.warning("[PDF] non-PDF body from %s (first bytes: %r)", safe_url, content[:16])
        return None
    content_type = resp.headers.get("content-type", "application/pdf")
    return content, content_type


def _fetch_with_curl_or_none(url: str) -> Optional[Tuple[bytes, str, str]]:
    """Run :func:`_fetch_with_curl` only when the ``curl`` binary exists.

    Returns ``None`` (not an error) when ``curl`` isn't installed so the caller
    can surface the original ``requests`` failure instead of crashing.
    """
    if not shutil.which("curl"):
        logger.warning("[PDF] curl not on PATH; cannot bypass anti-crawl for %s", url)
        return None
    return _fetch_with_curl(url)
