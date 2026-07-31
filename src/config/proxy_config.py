# -*- coding: utf-8 -*-
"""Proxy configuration: single source of truth for proxy env var handling."""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_DOMESTIC_DOMAINS = [
    "eastmoney.com",
    "sina.com.cn",
    "163.com",
    "tushare.pro",
    "baostock.com",
    "sse.com.cn",
    "szse.cn",
    "csindex.com.cn",
    "cninfo.com.cn",
    "localhost",
    "127.0.0.1",
]


def resolve_proxy_and_configure_no_proxy(
    http_proxy: Optional[str],
    https_proxy: Optional[str],
) -> None:
    """Set up NO_PROXY env var based on http_proxy and domestic domain list.

    When a proxy is configured, automatically set NO_PROXY to exclude domestic
    financial data sources, preventing quote/data fetch failures.
    """
    if not http_proxy:
        return

    current_no_proxy = os.getenv("NO_PROXY") or os.getenv("no_proxy") or ""
    existing_domains = current_no_proxy.split(",") if current_no_proxy else []

    final_domains = list(set(existing_domains + _DOMESTIC_DOMAINS))
    final_no_proxy = ",".join(filter(None, final_domains))

    os.environ["NO_PROXY"] = final_no_proxy
    os.environ["no_proxy"] = final_no_proxy

    os.environ["HTTP_PROXY"] = http_proxy
    os.environ["http_proxy"] = http_proxy

    if https_proxy:
        os.environ["HTTPS_PROXY"] = https_proxy
        os.environ["https_proxy"] = https_proxy
