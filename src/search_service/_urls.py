# -*- coding: utf-8 -*-
"""共享的 URL 工具函数。"""

from urllib.parse import urlparse


def extract_domain(url: str) -> str:
    """从 URL 提取域名作为来源标签。"""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        return domain or "未知来源"
    except Exception:
        return "未知来源"
