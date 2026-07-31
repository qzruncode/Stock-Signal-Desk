# -*- coding: utf-8 -*-
"""Business analysis cache helpers — key building, get, and put."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from api.v1.endpoints.stock_info.profile import _normalize_symbol

logger = logging.getLogger(__name__)

BUSINESS_CACHE_KEY = "stock_business:v2"


def _business_cache_key(symbol: str) -> str:
    return f"{BUSINESS_CACHE_KEY}:{_normalize_symbol(symbol)}:{datetime.now().strftime('%Y%m%d')}"


def _business_cache_get(symbol: str) -> dict | None:
    try:
        from src.storage import DatabaseManager

        raw = DatabaseManager.get_instance().get_kline_snapshot(_business_cache_key(symbol))
        if raw:
            return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.warning("[StockBusiness] _business_cache_get failed for symbol=%s", symbol, exc_info=True)
    return None


def _business_cache_put(symbol: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager

        DatabaseManager.get_instance().save_kline_snapshot(
            _business_cache_key(symbol), json.dumps(data, ensure_ascii=False)
        )
    except Exception:
        logger.warning("[StockBusiness] _business_cache_put failed for symbol=%s", symbol, exc_info=True)
