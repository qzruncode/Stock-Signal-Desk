# -*- coding: utf-8 -*-
"""Stock basic info profile endpoint — company profile and static data."""

from __future__ import annotations

import json
import logging
import math
import time
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

from api.v1.endpoints.stock_info import router

logger = logging.getLogger(__name__)

CACHE_KEY = "stock_info:v2"


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in ('nan', 'None', '--', '-'):
        return None
    for suffix, multiplier in [('亿', 1e8), ('万', 1e4)]:
        if s.endswith(suffix):
            try:
                return float(s[:-len(suffix)]) * multiplier
            except (ValueError, TypeError):
                return None
    try:
        f = float(s)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in ('nan', 'None', '--', '-'):
        return None
    for suffix, multiplier in [('亿', 1e8), ('万', 1e4)]:
        if s.endswith(suffix):
            try:
                return int(float(s[:-len(suffix)]) * multiplier)
            except (ValueError, TypeError):
                return None
    try:
        f = float(s)
        if math.isnan(f) or math.isinf(f):
            return None
        return int(f)
    except (ValueError, TypeError):
        return None


def _cache_key(symbol: str) -> str:
    return f"{CACHE_KEY}:{symbol}:{datetime.now().strftime('%Y%m%d')}"


def _normalize_symbol(symbol: str) -> str:
    s = symbol.strip()
    if s.startswith(('sh', 'sz', 'SH', 'SZ')):
        return s[2:]
    return s


def _cache_get(symbol: str) -> dict | None:
    try:
        from src.storage import DatabaseManager
        raw = DatabaseManager.get_instance().get_kline_snapshot(_cache_key(symbol))
        if raw:
            return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        pass
    return None


def _cache_put(symbol: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        DatabaseManager.get_instance().save_kline_snapshot(
            _cache_key(symbol), json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def _fetch_from_cninfo(symbol: str) -> dict:
    """Fetch from cninfo (stock_profile_cninfo)."""
    import akshare as ak
    try:
        df = ak.stock_profile_cninfo(symbol=symbol)
        if df is None or df.empty:
            return {}
        row = df.iloc[-1].to_dict() if len(df) > 1 else df.iloc[0].to_dict()
        return {k: (None if (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else v) for k, v in row.items()}
    except Exception as exc:
        logger.warning("[StockInfo] cninfo fetch failed for %s: %s", symbol, exc)
        return {}


def _fetch_from_em(symbol: str) -> dict:
    """Fetch from East Money (stock_individual_info_em)."""
    import akshare as ak
    try:
        df = ak.stock_individual_info_em(symbol=symbol)
        if df is None or df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            item = row.to_dict()
            key = str(item.get('item', '')).strip()
            value = item.get('value')
            if key and value:
                result[key] = value
        result['_source'] = 'eastmoney'
        return result
    except Exception as exc:
        logger.warning("[StockInfo] East Money fetch failed for %s: %s", symbol, exc)
        return {}


def _fetch_from_ths_business(symbol: str) -> dict:
    """Fetch business composition from THS."""
    import akshare as ak
    try:
        df = ak.stock_business_analysis(symbol=symbol)
        if df is None or df.empty:
            return {}
        result = {}
        result['_revenue_breakdown'] = (
            sorted(df.to_dict('records'), key=lambda x: x.get('revenue_pct', 0), reverse=True)
            if 'revenue_pct' in df.columns else df.to_dict('records')
        )
        return result
    except Exception as exc:
        logger.warning("[StockInfo] THS business analysis failed for %s: %s", symbol, exc)
        return {}


def _fetch_all(symbol: str) -> dict:
    cninfo = _fetch_from_cninfo(symbol)
    em = _fetch_from_em(symbol)
    ths = _fetch_from_ths_business(symbol)

    merged = {}
    merged['_sources'] = []

    if cninfo:
        merged['_sources'].append('cninfo')
        merged['cninfo'] = cninfo

    if em:
        merged['_sources'].append('eastmoney')
        merged['eastmoney'] = em

    if ths:
        merged['_sources'].append('ths')
        merged['ths_business'] = ths

    merged['symbol'] = symbol
    return merged


_lock = None


@router.get("/info", summary="获取个股基本资料")
def get_stock_info(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """Get basic stock profile from combined sources with cache."""
    global _lock
    if _lock is None:
        _lock = threading.Lock()

    normalized = _normalize_symbol(symbol)

    if not force:
        cached = _cache_get(normalized)
        if cached:
            return cached

    with _lock:
        if not force:
            cached = _cache_get(normalized)
            if cached:
                return cached
        data = _fetch_all(normalized)
        _cache_put(normalized, data)
        return data