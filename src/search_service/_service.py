# -*- coding: utf-8 -*-
"""SearchService 聚合服务与单例工厂。"""

import logging
import re
import threading
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

from data_provider.us_index_mapping import is_us_index_code
from src import search_service as _ss
from src.config import (
    NEWS_STRATEGY_WINDOWS,
    normalize_news_strategy_profile,
    resolve_news_window_days,
)
from src.search_service._base import BaseSearchProvider
from src.search_service._models import SearchResponse, SearchResult
from src.search_service.providers.anspire import AnspireSearchProvider
from src.search_service.providers.bocha import BochaSearchProvider
from src.search_service.providers.brave import BraveSearchProvider
from src.search_service.providers.minimax import MiniMaxSearchProvider
from src.search_service.providers.searxng import SearXNGSearchProvider
from src.search_service.providers.serpapi import SerpAPISearchProvider
from src.search_service.providers.tavily import TavilySearchProvider

logger = logging.getLogger(__name__)


from ._service_methods1 import _SearchServiceMethods1
from ._service_methods2 import _SearchServiceMethods2
from ._service_methods3 import _SearchServiceMethods3

class SearchService(_SearchServiceMethods1, _SearchServiceMethods2, _SearchServiceMethods3):
        """
        搜索服务

        功能：
        1. 管理多个搜索引擎
        2. 自动故障转移
        3. 结果聚合和格式化
        4. 数据源失败时的增强搜索（股价、走势等）
        5. 港股/美股自动使用英文搜索关键词
        """
        ENHANCED_SEARCH_KEYWORDS = [
            "{name} 股票 今日 股价",
            "{name} {code} 最新 行情 走势",
            "{name} 股票 分析 走势图",
            "{name} K线 技术分析",
            "{name} {code} 涨跌 成交量",
        ]
        ENHANCED_SEARCH_KEYWORDS_EN = [
            "{name} stock price today",
            "{name} {code} latest quote trend",
            "{name} stock analysis chart",
            "{name} technical analysis",
            "{name} {code} performance volume",
        ]
        NEWS_OVERSAMPLE_FACTOR = 2
        NEWS_OVERSAMPLE_MAX = 10
        FUTURE_TOLERANCE_DAYS = 1
        _CHINESE_TEXT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
        _US_STOCK_RE = re.compile(r"^[A-Za-z]{1,5}(\.[A-Za-z])?$")
        _A_ETF_PREFIXES = ("51", "52", "56", "58", "15", "16", "18")
        _ETF_NAME_KEYWORDS = ("ETF", "FUND", "TRUST", "INDEX", "TRACKER", "UNIT")  # US/HK ETF name hints


# === 便捷函数 ===
_search_service: Optional[SearchService] = None
_search_service_lock = threading.Lock()


def get_search_service() -> SearchService:
    """获取搜索服务单例"""
    global _search_service

    if _search_service is None:
        with _search_service_lock:
            if _search_service is None:
                from src.config import get_config

                config = get_config()

                _search_service = _ss.SearchService(
                    bocha_keys=config.bocha_api_keys,
                    tavily_keys=config.tavily_api_keys,
                    anspire_keys=config.anspire_api_keys,
                    brave_keys=config.brave_api_keys,
                    serpapi_keys=config.serpapi_keys,
                    minimax_keys=config.minimax_api_keys,
                    searxng_base_urls=config.searxng_base_urls,
                    searxng_public_instances_enabled=config.searxng_public_instances_enabled,
                    news_max_age_days=config.news_max_age_days,
                    news_strategy_profile=getattr(config, "news_strategy_profile", "short"),
                )

    return _search_service


def reset_search_service() -> None:
    """重置搜索服务（用于测试）"""
    global _search_service
    with _search_service_lock:
        _search_service = None
