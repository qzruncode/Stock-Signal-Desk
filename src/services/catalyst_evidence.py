"""Structured evidence acquisition for forward catalyst research.

This module deliberately separates *retrieval* from *judgment*:

* formal periodic-report bodies are read page by page instead of treating an
  announcement title as the document;
* passages are selected only because they contain an explicit calendar window
  inside the requested horizon — no catalyst keyword list decides semantics;
* scheduled reporting dates are returned as verification windows, not silently
  promoted to positive company catalysts.

The evaluator/LLM remains responsible for deciding what the disclosed passage
means for orders, revenue, profit or expectations.
"""

from __future__ import annotations

from io import BytesIO
import logging
import re
import time
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

import requests

logger = logging.getLogger(__name__)

_CONTENT_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
_SCHEDULE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_STOCK_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
_ART_CODE_RE = re.compile(r"(AN\d{14,24})", re.I)
_SPACE_RE = re.compile(r"\s+")
_EXPLICIT_WINDOW_RE = re.compile(
    r"(?P<year>20\d{2})\s*年\s*"
    r"(?:(?P<month>1[0-2]|0?[1-9])\s*月(?:\s*(?P<day>[0-3]?\d)\s*日)?|"
    r"第?\s*(?P<quarter>[一二三四1-4])\s*季度|"
    r"(?P<half>上半年|下半年))",
    re.I,
)



__all__ = [
    "extract_business_passages",
    "extract_forward_window_passages",
    "fetch_formal_document",
    "get_formal_business_evidence",
    "get_formal_forward_evidence",
    "get_report_schedule",
    "select_formal_documents",
]


from . import _catalyst_evidence_functions1 as _catalyst_evidence_functions1
from . import _catalyst_evidence_functions2 as _catalyst_evidence_functions2


def _bind_extracted_function(_member):
    import functools
    import types

    if not isinstance(_member, types.FunctionType):
        return _member
    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_catalyst_evidence_functions1, _catalyst_evidence_functions2):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))
