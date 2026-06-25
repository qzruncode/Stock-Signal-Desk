# -*- coding: utf-8 -*-
"""Cache layer for financials package."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

from ._helpers import _normalize_symbol

CACHE_KEY = "financials:v2"

def _cache_key(symbol: str) -> str:
    return f"{CACHE_KEY}:{_normalize_symbol(symbol)}:{datetime.now().strftime('%Y%m%d')}"


def _cache_get(symbol: str) -> dict | None:
    try:
        from src.storage import DatabaseManager
        raw = DatabaseManager.get_instance().get_kline_snapshot(_cache_key(symbol))
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and 'symbol' in data:
                logger.info(f"[Financials] cache HIT {_cache_key(symbol)}")
                return data
    except Exception as e:
        logger.warning(f"[Financials] cache read error: {e}")
    return None


def _cache_put(symbol: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        DatabaseManager.get_instance().save_kline_snapshot(
            _cache_key(symbol), json.dumps(data, ensure_ascii=False))
        logger.info(f"[Financials] cache SAVED {_cache_key(symbol)}")
    except Exception as e:
        logger.warning(f"[Financials] cache write error: {e}")

VALUATION_CACHE_KEY = "stocks:valuation_ratios:v1"
SHAREHOLDER_CACHE_KEY = "stocks:shareholder_structure:v1"

def _daily_cache_key(prefix: str, symbol: str, *parts: Any) -> str:
    tail = ":".join(str(p) for p in parts if p is not None)
    suffix = f":{tail}" if tail else ""
    return f"{prefix}:{_normalize_symbol(symbol)}{suffix}:{datetime.now().strftime('%Y%m%d')}"


def _daily_cache_get(prefix: str, symbol: str, *parts: Any) -> dict | None:
    try:
        from src.storage import DatabaseManager
        key = _daily_cache_key(prefix, symbol, *parts)
        raw = DatabaseManager.get_instance().get_kline_snapshot(key)
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and data.get("symbol"):
                logger.info(f"[{prefix}] cache HIT {key}")
                return data
    except Exception as e:
        logger.warning(f"[{prefix}] cache read error: {e}")
    return None


def _daily_cache_put(prefix: str, symbol: str, data: dict, *parts: Any) -> None:
    try:
        from src.storage import DatabaseManager
        key = _daily_cache_key(prefix, symbol, *parts)
        DatabaseManager.get_instance().save_kline_snapshot(key, json.dumps(data, ensure_ascii=False))
        logger.info(f"[{prefix}] cache SAVED {key}")
    except Exception as e:
        logger.warning(f"[{prefix}] cache write error: {e}")

FINS_STATEMENTS_CACHE_KEY = "financials:statements:v2"
NEWS_CACHE_KEY = "stocks:news:v3:rss_structured"
ANNOUNCEMENTS_CACHE_KEY = "stocks:announcements:v4"
RISK_EVENTS_CACHE_KEY = "stocks:risk_events:v4"
SENTIMENT_CACHE_KEY = "stocks:sentiment:v3:rss_structured"
RESEARCH_CACHE_KEY = "stocks:research_report:v5"
SOCIAL_SENTIMENT_CACHE_KEY = "stocks:social_sentiment:v4"

def _fins_cache_key(symbol: str, periods: int) -> str:
    return f"{FINS_STATEMENTS_CACHE_KEY}:{_normalize_symbol(symbol)}:p{periods}:{datetime.now().strftime('%Y%m%d')}"


def _fins_cache_get(symbol: str, periods: int) -> dict | None:
    try:
        from src.storage import DatabaseManager
        key = _fins_cache_key(symbol, periods)
        raw = DatabaseManager.get_instance().get_kline_snapshot(key)
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and 'symbol' in data:
                # Validate periods match (prevent stale cache with wrong periods)
                if data.get('periods') == periods:
                    logger.info(f"[FinancialStatements] cache HIT {key}")
                    return data
                logger.info(f"[FinancialStatements] cache STALE (periods mismatch) {key}")
    except Exception as e:
        logger.warning(f"[FinancialStatements] cache read error: {e}")
    return None


def _fins_cache_put(symbol: str, periods: int, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        key = _fins_cache_key(symbol, periods)
        DatabaseManager.get_instance().save_kline_snapshot(
            key, json.dumps(data, ensure_ascii=False))
        logger.info(f"[FinancialStatements] cache SAVED {key}")
    except Exception as e:
        logger.warning(f"[FinancialStatements] cache write error: {e}")
