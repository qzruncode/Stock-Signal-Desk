# -*- coding: utf-8 -*-
"""===================================
A股自选股智能分析系统 - 搜索服务模块
===================================

按搜索供应商拆分到子模块，保持 ``from src.search_service import ...`` 兼容。
"""

import logging
import time
from typing import Any, Dict

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from newspaper import Article, Config

logger = logging.getLogger(__name__)

# Transient network errors (retryable)
_SEARCH_TRANSIENT_EXCEPTIONS = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_SEARCH_TRANSIENT_EXCEPTIONS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _post_with_retry(url: str, *, headers: Dict[str, str], json: Dict[str, Any], timeout: int) -> requests.Response:
    """POST with retry on transient SSL/network errors."""
    return requests.post(url, headers=headers, json=json, timeout=timeout)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_SEARCH_TRANSIENT_EXCEPTIONS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _get_with_retry(url: str, *, headers: Dict[str, str], params: Dict[str, Any], timeout: int) -> requests.Response:
    """GET with retry on transient SSL/network errors."""
    return requests.get(url, headers=headers, params=params, timeout=timeout)


def fetch_url_content(url: str, timeout: int = 5) -> str:
    """获取 URL 网页正文内容 (使用 newspaper3k)。"""
    try:
        config = Config()
        config.browser_user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        )
        config.request_timeout = timeout
        config.fetch_images = False
        config.memoize_articles = False

        article = Article(url, config=config, language="zh")
        article.download()
        article.parse()

        text = article.text.strip()
        cleaned = "\n".join(line.strip() for line in text.split("\n") if line.strip())
        return cleaned[:1500]
    except Exception as e:
        logger.debug(f"Fetch content failed for {url}: {e}")

    return ""


from src.search_service._models import SearchResponse, SearchResult
from src.search_service._base import BaseSearchProvider
from src.search_service.providers.anspire import AnspireSearchProvider
from src.search_service.providers.bocha import BochaSearchProvider
from src.search_service.providers.brave import BraveSearchProvider
from src.search_service.providers.minimax import MiniMaxSearchProvider
from src.search_service.providers.searxng import SearXNGSearchProvider
from src.search_service.providers.serpapi import SerpAPISearchProvider
from src.search_service.providers.tavily import TavilySearchProvider
from src.search_service._service import (
    SearchService,
    get_search_service,
    reset_search_service,
)

__all__ = [
    "AnspireSearchProvider",
    "BaseSearchProvider",
    "BochaSearchProvider",
    "BraveSearchProvider",
    "MiniMaxSearchProvider",
    "SearXNGSearchProvider",
    "SearchResponse",
    "SearchResult",
    "SearchService",
    "SerpAPISearchProvider",
    "TavilySearchProvider",
    "fetch_url_content",
    "get_search_service",
    "reset_search_service",
]
