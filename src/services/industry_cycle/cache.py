# -*- coding: utf-8 -*-
"""Cache key builders and DB-backed cache accessors for industry cycle service."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

INDUSTRY_CYCLE_CACHE_KEY = "stocks:industry_cycle:v1"
INDUSTRY_CYCLE_REPORT_CACHE_KEY = "stocks:industry_cycle:report:v1"
INDUSTRY_CYCLE_STOCK_FLOW_CACHE_KEY = "stocks:industry_cycle:stock_flow:v1"


def _current_report_as_of_date() -> str:
    try:
        from api.v1.endpoints.market_status import get_market_status

        market_status = get_market_status(force=False)
        data_time = (market_status or {}).get("data_time")
        if data_time:
            return str(data_time)[:10]
    except Exception:
        logger.exception("industry cycle current analysis date read failed")
    return datetime.now().date().isoformat()


def _cache_key(symbol: str) -> str:
    return f"{INDUSTRY_CYCLE_CACHE_KEY}:{symbol}:{_current_report_as_of_date()}"


def _report_cache_key(symbol: str) -> str:
    return f"{INDUSTRY_CYCLE_REPORT_CACHE_KEY}:{symbol}:{_current_report_as_of_date()}"


def _stock_flow_cache_key(symbol: str) -> str:
    return f"{INDUSTRY_CYCLE_STOCK_FLOW_CACHE_KEY}:{symbol}:{_current_report_as_of_date()}"


def uuid4_hex() -> str:
    return uuid.uuid4().hex


def _cache_get(symbol: str) -> Optional[dict[str, Any]]:
    try:
        from src.storage import DatabaseManager

        raw = DatabaseManager.get_instance().get_kline_snapshot(_cache_key(symbol))
        if not raw:
            return None
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.exception("industry cycle cache read failed")
        return None


def _cache_put(symbol: str, payload: dict[str, Any]) -> None:
    try:
        from src.storage import DatabaseManager

        DatabaseManager.get_instance().save_kline_snapshot(
            _cache_key(symbol),
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        logger.exception("industry cycle cache write failed")


def _report_cache_get(symbol: str) -> Optional[dict[str, Any]]:
    try:
        from src.storage import DatabaseManager

        raw = DatabaseManager.get_instance().get_kline_snapshot(_report_cache_key(symbol))
        if not raw:
            return None
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.exception("industry cycle report cache read failed")
        return None


def _report_cache_put(symbol: str, payload: dict[str, Any]) -> None:
    try:
        from src.storage import DatabaseManager

        DatabaseManager.get_instance().save_kline_snapshot(
            _report_cache_key(symbol),
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        logger.exception("industry cycle report cache write failed")


def _stock_flow_cache_get(symbol: str) -> Optional[dict[str, Any]]:
    try:
        from src.storage import DatabaseManager

        raw = DatabaseManager.get_instance().get_kline_snapshot(_stock_flow_cache_key(symbol))
        if not raw:
            return None
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.exception("industry cycle stock flow cache read failed")
        return None


def _stock_flow_cache_put(symbol: str, payload: dict[str, Any]) -> None:
    try:
        from src.storage import DatabaseManager

        DatabaseManager.get_instance().save_kline_snapshot(
            _stock_flow_cache_key(symbol),
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        logger.exception("industry cycle stock flow cache write failed")
