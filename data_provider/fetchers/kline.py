# -*- coding: utf-8 -*-
"""History K-line data fetchers — A-share (EM/Sina/Tencent), ETF, HK, US."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_exception,
    before_sleep_log,
)

from ..utils import (
    DataFetchError,
    RateLimitError,
    STANDARD_COLUMNS,
    is_bse_code,
    is_st_stock,
    is_kc_cy_stock,
    normalize_stock_code,
)
from ..constants import USER_AGENTS

logger = logging.getLogger(__name__)



from . import _kline_functions1 as _kline_functions1
from . import _kline_functions2 as _kline_functions2


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_kline_functions1, _kline_functions2):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))


_retry_a_stock_kline = retry(
    stop=stop_after_attempt(3),
    wait=_wait_for_kline_retry,
    retry=retry_if_exception(_is_retryable_kline_error),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
