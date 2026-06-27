# -*- coding: utf-8 -*-
"""
===================================
AkshareFetcher - 主数据源 (Priority 1)
===================================

数据来源：
1. 东方财富爬虫（通过 akshare 库） - 默认数据源
2. 新浪财经接口 - 备选数据源
3. 腾讯财经接口 - 备选数据源

特点：免费、无需 Token、数据全面
风险：爬虫机制易被反爬封禁

防封禁策略：
1. 每次请求前随机休眠 2-5 秒
2. 随机轮换 User-Agent
3. 使用 tenacity 实现指数退避重试
4. 熔断器机制：连续失败后自动冷却

增强数据：
- 实时行情：量比、换手率、市盈率、市净率、总市值、流通市值
- 筹码分布：获利比例、平均成本、筹码集中度
"""

from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests  # kept at module level so tests can monkeypatch "data_provider.akshare_fetcher.requests"
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log,
)

from src.patches.eastmoney_patch import eastmoney_patch
from src.config import get_config
from .utils import DataFetchError, RateLimitError, STANDARD_COLUMNS, is_bse_code, is_st_stock, is_kc_cy_stock, normalize_stock_code
from .realtime_types import UnifiedRealtimeQuote, ChipDistribution, RealtimeSource, safe_float, safe_int
from .us_index_mapping import is_us_index_code, is_us_stock_code

# ── Re-export module-level API from split sub-modules ────────────────────
from .circuit_breaker import get_realtime_circuit_breaker, RealtimeCircuitBreaker
from .cache import realtime_cache, etf_realtime_cache
from .constants import USER_AGENTS, SINA_REALTIME_ENDPOINT, TENCENT_REALTIME_ENDPOINT
from .fetchers.realtime import get_realtime_quote as _fetch_realtime_quote
from .fetchers.kline import (
    fetch_raw_data as _fetch_raw_data,
    fetch_stock_data_em as _fetch_stock_data_em,
    fetch_stock_data_sina as _fetch_stock_data_sina,
    fetch_stock_data_tx as _fetch_stock_data_tx,
    fetch_etf_data as _fetch_etf_data,
    fetch_us_data as _fetch_us_data,
    fetch_hk_data as _fetch_hk_data,
    fetch_stock_kline_history as _fetch_stock_kline_history,
    _normalize_data as _normalize_kline_data,
    get_main_indices as _fetch_main_indices,
    _is_us_code, _is_hk_code, _is_etf_code,
    _to_sina_tx_symbol,
)
from .fetchers.market import (
    get_market_stats as _fetch_market_stats,
    get_all_a_stocks as _fetch_all_a_stocks,
    get_limit_up_pool as _fetch_limit_up_pool,
    get_hot_stocks as _fetch_hot_stocks,
    _classify_a_stock_market,
)
from .fetchers.ranking import (
    get_sector_rankings as _fetch_sector_rankings,
    get_concept_rankings as _fetch_concept_rankings,
)
from .fetchers.chip import get_chip_distribution as _fetch_chip_distribution


# 保留旧的 RealtimeQuote 别名，用于向后兼容
RealtimeQuote = UnifiedRealtimeQuote

logger = logging.getLogger(__name__)


class AkshareFetcher:
    """
    Akshare 数据源实现

    优先级：1（最高）
    数据来源：东方财富网爬虫

    关键策略：
    - 每次请求前随机休眠 2.0-5.0 秒
    - 随机 User-Agent 轮换
    - 失败后指数退避重试（最多3次）
    """

    name = "AkshareFetcher"
    priority = int(os.getenv("AKSHARE_PRIORITY", "1"))

    def __init__(self, sleep_min: float = 2.0, sleep_max: float = 5.0):
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self._last_request_time: Optional[float] = None
        if get_config().enable_eastmoney_patch:
            eastmoney_patch()

    def _set_random_user_agent(self) -> None:
        try:
            random.choice(USER_AGENTS)
        except Exception as e:
            logger.debug("设置 User-Agent 失败: %s", e)

    def random_sleep(self, min_seconds: float = 1.0, max_seconds: float = 3.0) -> None:
        sleep_time = random.uniform(min_seconds, max_seconds)
        logger.debug("随机休眠 %.2f 秒...", sleep_time)
        time.sleep(sleep_time)

    def _enforce_rate_limit(self, jitter: bool = True) -> None:
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            min_interval = self.sleep_min
            if elapsed < min_interval:
                additional_sleep = min_interval - elapsed
                logger.debug("补充休眠 %.2f 秒", additional_sleep)
                time.sleep(additional_sleep)
        if jitter and self._last_request_time is not None:
            self.random_sleep(self.sleep_min, self.sleep_max)
        self._last_request_time = time.time()

    # ── K-line history ─────────────────────────────────────────────────

    def _enforce_rate_limit_no_jitter(self):
        self._enforce_rate_limit(jitter=False)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return _fetch_raw_data(
            stock_code, start_date, end_date,
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    def _fetch_stock_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        from .fetchers.kline import fetch_stock_data
        return fetch_stock_data(
            stock_code, start_date, end_date,
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    def _fetch_stock_data_em(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return _fetch_stock_data_em(
            stock_code, start_date, end_date,
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    def _fetch_stock_data_sina(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return _fetch_stock_data_sina(
            stock_code, start_date, end_date,
            enforce_rate_limit=self._enforce_rate_limit_no_jitter,
        )

    def _fetch_stock_data_tx(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return _fetch_stock_data_tx(
            stock_code, start_date, end_date,
            enforce_rate_limit=self._enforce_rate_limit_no_jitter,
        )

    def _fetch_etf_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return _fetch_etf_data(
            stock_code, start_date, end_date,
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    def _fetch_us_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return _fetch_us_data(
            stock_code, start_date, end_date,
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    def _fetch_hk_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return _fetch_hk_data(
            stock_code, start_date, end_date,
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return _normalize_kline_data(df, stock_code)

    # ── Realtime quote ─────────────────────────────────────────────────

    def get_realtime_quote(self, stock_code: str, source: str = "em") -> Optional[UnifiedRealtimeQuote]:
        return _fetch_realtime_quote(stock_code, source)

    def _get_stock_realtime_quote_em(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        from .fetchers.realtime import _get_stock_realtime_quote_em as _fn
        return _fn(stock_code)

    def _get_stock_realtime_quote_em_push(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        from .fetchers.realtime import _get_stock_realtime_quote_em_push as _fn
        return _fn(stock_code)

    def _get_stock_realtime_quote_xueqiu(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        from .fetchers.realtime import _get_stock_realtime_quote_xueqiu as _fn
        return _fn(stock_code)

    def _get_stock_realtime_quote_sina(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        from .fetchers.realtime import _get_stock_realtime_quote_sina as _fn
        return _fn(stock_code)

    def _get_stock_realtime_quote_tencent(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        from .fetchers.realtime import _get_stock_realtime_quote_tencent as _fn
        return _fn(stock_code)

    def _get_etf_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        from .fetchers.realtime import _get_etf_realtime_quote as _fn
        return _fn(stock_code)

    def _get_hk_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        from .fetchers.realtime import _get_hk_realtime_quote as _fn
        return _fn(stock_code)

    # ── Chip distribution ──────────────────────────────────────────────

    def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        return _fetch_chip_distribution(
            stock_code,
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    # ── Enhanced data ──────────────────────────────────────────────────

    def get_enhanced_data(self, stock_code: str, days: int = 60) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'code': stock_code,
            'daily_data': None,
            'realtime_quote': None,
            'chip_distribution': None,
        }
        try:
            result['daily_data'] = self.get_daily_data(stock_code, days=days)
        except Exception as e:
            logger.error("获取 %s 日线数据失败: %s", stock_code, e)
        result['realtime_quote'] = self.get_realtime_quote(stock_code)
        result['chip_distribution'] = self.get_chip_distribution(stock_code)
        return result

    # ── Indices ────────────────────────────────────────────────────────

    def get_main_indices(self, region: str = "cn") -> Optional[List[Dict[str, Any]]]:
        return _fetch_main_indices(region)

    # ── Market stats ───────────────────────────────────────────────────

    def get_market_stats(self) -> Optional[Dict[str, Any]]:
        return _fetch_market_stats(
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    def _calc_market_stats(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        from .fetchers.market import _calc_market_stats
        return _calc_market_stats(df)

    # ── All A-share list ───────────────────────────────────────────────

    def get_all_a_stocks(self) -> Optional[List[Dict[str, Any]]]:
        try:
            try:
                eastmoney_patch()
            except Exception:
                logger.warning("[StocksSync] eastmoney_patch 应用失败（非致命）", exc_info=True)
        except Exception:
            logger.warning("[StocksSync] get_all_a_stocks outer guard failed", exc_info=True)
        return _fetch_all_a_stocks(enforce_rate_limit=self._enforce_rate_limit)

    # ── Kline history (convenience) ────────────────────────────────────

    def fetch_stock_kline_history(self, code: str, days: int = 365) -> Optional[pd.DataFrame]:
        return _fetch_stock_kline_history(code, days, enforce_rate_limit=self._enforce_rate_limit)

    def fetch_stock_kline_batch(self, codes: List[str], days: int = 365, workers: int = 5) -> Dict[str, Optional[pd.DataFrame]]:
        import concurrent.futures
        batch_fetcher = AkshareFetcher(sleep_min=0.3, sleep_max=0.5)

        results: Dict[str, Optional[pd.DataFrame]] = {}

        def _fetch_one(code: str) -> tuple:
            df = batch_fetcher.fetch_stock_kline_history(code, days=days)
            return code, df

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_one, code): code for code in codes}
            for future in concurrent.futures.as_completed(futures):
                code, df = future.result()
                results[code] = df

        return results

    # ── Sector / concept rankings ──────────────────────────────────────

    def get_sector_rankings(self, n: int = 5) -> Optional[Tuple[List[Dict], List[Dict]]]:
        return _fetch_sector_rankings(
            n,
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    def get_concept_rankings(self, n: int = 5) -> Optional[Tuple[List[Dict], List[Dict]]]:
        return _fetch_concept_rankings(
            n,
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    # ── Hot stocks ─────────────────────────────────────────────────────

    def get_hot_stocks(self, n: int = 10) -> Optional[List[Dict[str, Any]]]:
        return _fetch_hot_stocks(
            n,
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    def _get_eastmoney_hot_stocks(self, ak, n: int = 10) -> Optional[List[Dict[str, Any]]]:
        from .fetchers.market import _get_eastmoney_hot_stocks
        return _get_eastmoney_hot_stocks(ak, n, self._enforce_rate_limit, self._set_random_user_agent)

    def _get_eastmoney_hot_up_stocks(self, ak, n: int = 10) -> Optional[List[Dict[str, Any]]]:
        from .fetchers.market import _get_eastmoney_hot_up_stocks
        return _get_eastmoney_hot_up_stocks(ak, n, self._enforce_rate_limit, self._set_random_user_agent)

    def _get_xueqiu_hot_stocks(self, ak, n: int = 10) -> Optional[List[Dict[str, Any]]]:
        from .fetchers.market import _get_xueqiu_hot_stocks
        return _get_xueqiu_hot_stocks(ak, n, self._enforce_rate_limit, self._set_random_user_agent)

    # ── Limit-up pool ──────────────────────────────────────────────────

    def get_limit_up_pool(self, date: Optional[str] = None, n: int = 20) -> Optional[List[Dict[str, Any]]]:
        return _fetch_limit_up_pool(
            date, n,
            enforce_rate_limit=self._enforce_rate_limit,
            set_user_agent=self._set_random_user_agent,
        )

    # ── Classification helpers ─────────────────────────────────────────

    @staticmethod
    def _classify_a_stock_market(code: str) -> str:
        return _classify_a_stock_market(code)

    @staticmethod
    def _normalize_limit_time_value(value: Any) -> str:
        from .fetchers.market import _normalize_limit_time_value
        return _normalize_limit_time_value(value)

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        return safe_float(value)

    @staticmethod
    def _safe_int(value: Any) -> int:
        return safe_int(value)

    @staticmethod
    def _find_first_column(df: pd.DataFrame, candidates: Tuple[str, ...]) -> Optional[str]:
        from .fetchers.market import _find_first_column
        return _find_first_column(df, candidates)

    @staticmethod
    def _find_column_containing(df: pd.DataFrame, keywords: Tuple[str, ...]) -> Optional[str]:
        from .fetchers.market import _find_column_containing
        return _find_column_containing(df, keywords)

    # ── Legacy convenience (delegates to fetch_stock_kline_history internally) ──

    def get_daily_data(self, stock_code: str, days: int = 365) -> Optional[pd.DataFrame]:
        """已废弃，兼容旧调用方。"""
        return self.fetch_stock_kline_history(stock_code, days=days)


# ── Module-level re-exports for backward compatibility ──────────────────
_realtime_circuit_breaker = get_realtime_circuit_breaker()

# Legacy module-level cache references (backward compat)
_realtime_cache = realtime_cache
_etf_realtime_cache = etf_realtime_cache


# ── Legacy helpers (kept for test compatibility) ─────────────────────────

def _is_etf_code(stock_code: str) -> bool:
    etf_prefixes = ('51', '52', '56', '58', '15', '16', '18')
    code = stock_code.strip().split('.')[0]
    return code.startswith(etf_prefixes) and len(code) == 6


def _is_hk_code(stock_code: str) -> bool:
    code = stock_code.strip().lower()
    if code.endswith('.hk'):
        numeric_part = code[:-3]
        return numeric_part.isdigit() and 1 <= len(numeric_part) <= 5
    if code.startswith('hk'):
        numeric_part = code[2:]
        return numeric_part.isdigit() and 1 <= len(numeric_part) <= 5
    return code.isdigit() and len(code) == 5


def is_hk_stock_code(stock_code: str) -> bool:
    return _is_hk_code(stock_code)


def _is_us_code(stock_code: str) -> bool:
    return is_us_stock_code(stock_code)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    fetcher = AkshareFetcher()
    print("=" * 50)
    print("测试普通股票数据获取")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('600519')
        print(f"[股票] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[股票] 获取失败: {e}")