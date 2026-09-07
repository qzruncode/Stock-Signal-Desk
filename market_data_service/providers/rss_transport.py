"""Compatibility transport restricted to the configured RSSHub origin."""

from urllib.parse import urlparse
from market_data_service.settings import get_settings
from market_data_service.providers.rss_fetch import (
    _fetch_rss_feed,
    _fetch_rss_feed_json,
)


def _validate(url: str):
    actual, allowed = urlparse(url), urlparse(get_settings().rsshub_url)
    if (actual.scheme, actual.hostname, actual.port) != (
        allowed.scheme,
        allowed.hostname,
        allowed.port,
    ):
        raise ValueError("RSS 请求必须使用数据服务配置的 RSSHub 地址")
    if actual.username or actual.password or actual.fragment:
        raise ValueError("RSS 地址格式不符合服务约定")


def fetch_xml(url: str, limit: int = 30, timeout: float = 15.0):
    _validate(url)
    return _fetch_rss_feed(
        url, limit=min(100, max(1, limit)), timeout=min(45, max(1, timeout))
    )


def fetch_json(url: str, limit: int = 30, timeout: float = 15.0):
    _validate(url)
    return _fetch_rss_feed_json(
        url, limit=min(100, max(1, limit)), timeout=min(45, max(1, timeout))
    )
