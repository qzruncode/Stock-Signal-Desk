# -*- coding: utf-8 -*-
"""All-market ATR-relative-volatility screener.

The model never fetches or calculates these figures.  This service refreshes
the financial candidate universe, retrieves adjusted daily bars, applies the
formula exactly, and returns only rows that pass every hard condition.
"""

from __future__ import annotations

import bisect
import concurrent.futures
import csv
import hashlib
import json
import logging
import math
import re
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import requests
from pydantic import ValidationError
from sqlalchemy import text

from src.services.stock_screening.screen_spec import (
    AtrRelativeFrequencyRule,
    QuantitativeScreenSpec,
)
from src.storage import DatabaseManager
from src.tools._kline import _expected_latest_kline_date

logger = logging.getLogger(__name__)

EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/" "CN_MarketData.getKLineData"
SINA_OPENAPI_URL = "https://quotes.sina.cn/cn/api/openapi.php/CN_MarketDataService.getKLineData"
EXPORT_DIR = Path(__file__).resolve().parents[3] / "data" / "exports" / "stock-screening"
KLINE_FETCH_WORKERS = 64
KLINE_SECOND_PASS_WORKERS = 4
MAX_KLINE_SECOND_PASS_SYMBOLS = 50
FINANCIAL_FETCH_WORKERS = 8
MAX_SECONDARY_FINANCIAL_FALLBACKS = 50
_HTTP_LOCAL = threading.local()



_FIELD_META: dict[str, tuple[str, str]] = {
    "code": ("股票代码", "text"),
    "name": ("股票名称", "text"),
    "current_atr_pct": ("当前ATR相对波动率(%)", "percent"),
    "long_term_mean_pct": ("长期波动均值(%)", "percent"),
    "dynamic_warning_pct": ("动态警戒线(%)", "percent"),
    "qualified_days": ("达标天数", "integer"),
    "qualified_ratio_pct": ("达标比例(%)", "percent"),
    "revenue_ttm": ("营业收入TTM(元)", "currency_yuan"),
    "deducted_net_profit_ttm": ("扣非净利润TTM(元)", "currency_yuan"),
    "debt_ratio": ("资产负债率(%)", "percent"),
    "financial_report_period": ("财务报告期", "date"),
    "financial_source": ("财务来源", "text"),
    "latest_trade_date": ("行情日期", "date"),
}

_FINANCIAL_LABELS = {
    "revenue_ttm": "营业收入TTM",
    "deducted_net_profit_ttm": "扣非净利润TTM",
    "debt_ratio": "资产负债率",
}

_OPERATOR_LABELS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "="}

_AVERAGE_LABELS = {"sma": "简单移动平均", "ema": "指数移动平均", "wilder": "Wilder平滑"}

__all__ = [
    "calculate_atr_screen_metrics",
    "run_atr_volatility_screen",
]


from . import _atr_volatility_screener_functions1 as _atr_volatility_screener_functions1
from . import _atr_volatility_screener_functions2 as _atr_volatility_screener_functions2
from . import _atr_volatility_screener_functions3 as _atr_volatility_screener_functions3


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_atr_volatility_screener_functions1, _atr_volatility_screener_functions2, _atr_volatility_screener_functions3):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))
