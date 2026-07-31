# -*- coding: utf-8 -*-
"""Shared K-line implementation for ``get_kline`` and ``get_history_data``.

工具业务逻辑统一收口到 src/tools/，与 FastAPI 路由层解耦：
api/v1/endpoints/kline.py 仅保留薄路由（Query 校验 + 委托），从此处导入
get_kline / get_history_data。

Data source fallback chain (daily qfq only):
  1. StockDaily (local DB, synced from batch sync)
  2. KlineSnapshot cache
  3. East Money (akshare.stock_zh_a_hist)
  4. Sina Finance (akshare.stock_zh_a_daily)
  5. Tencent Finance (akshare.stock_zh_a_hist_tx)

外部 API 取数成功后会双写：写 KlineSnapshot 缓存 + 回写 StockDaily 表。
"""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from data_provider.circuit_breaker import RealtimeCircuitBreaker
from data_provider.rate_limiter import akshare_rate_limiter
from src.tools._trading_calendar import is_trading_time, trade_dates

logger = logging.getLogger(__name__)

KLINE_SOURCE_EM = "eastmoney"
KLINE_SOURCE_SINA = "sina"
KLINE_SOURCE_TENCENT = "tencent"

# Fixed defaults
DEFAULT_COUNT = 500
KLINE_SINGLE_SOURCE_RETRY_ATTEMPTS = 2
KLINE_SINGLE_SOURCE_RETRY_BASE_DELAY = 0.6
KLINE_FALLBACK_DELAY_MIN_SECONDS = 0.8
KLINE_FALLBACK_DELAY_MAX_SECONDS = 1.8
KLINE_AKSHARE_MIN_INTERVAL_SECONDS = 2.0
KLINE_AKSHARE_MAX_JITTER_SECONDS = 5.0

_kline_source_circuit_breaker = RealtimeCircuitBreaker()

KLINE_DESCRIPTION = (
    "获取股票日线K线数据（前复权），包含日期、开盘价、收盘价、最高价、最低价、"
    "成交量（股）、成交额（元）、涨跌幅、换手率；盘中当日 K 线会标记为未完成。"
)

KLINE_HISTORY_DESCRIPTION = "获取指定日期范围的日线K线数据（前复权），适用于需要查看特定时间段行情的场景"


_is_trading_hours = is_trading_time


from . import __kline_functions1 as __kline_functions1
from . import __kline_functions2 as __kline_functions2


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (__kline_functions1, __kline_functions2):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))


_CHAIN = [
    (_fetch_kline_em, KLINE_SOURCE_EM, "东方财富"),
    (_fetch_kline_sina, KLINE_SOURCE_SINA, "新浪财经"),
    (_fetch_kline_tencent, KLINE_SOURCE_TENCENT, "腾讯财经"),
]
