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
        logger.warning("[StockInfo] _cache_get failed for symbol=%s", symbol, exc_info=True)
    return None


def _cache_put(symbol: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        DatabaseManager.get_instance().save_kline_snapshot(
            _cache_key(symbol), json.dumps(data, ensure_ascii=False))
    except Exception:
        logger.warning("[StockInfo] _cache_put failed for symbol=%s", symbol, exc_info=True)


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
    """Fetch business composition (主营构成, 东方财富 stock_zygc_em)."""
    import akshare as ak
    try:
        code = _normalize_symbol(symbol).zfill(6)
        if code.startswith(("6", "5", "9")):
            em_symbol = f"SH{code}"
        elif code.startswith(("8", "4")):
            em_symbol = f"BJ{code}"
        else:
            em_symbol = f"SZ{code}"
        df = ak.stock_zygc_em(symbol=em_symbol)
        if df is None or df.empty:
            return {}
        records = df.to_dict('records')
        # 主营构成按收入占比排序（列名兼容 主营收入/营业收入/收入比例）
        pct_col = next((c for c in df.columns if "比例" in str(c) or "占比" in str(c)), None)
        if pct_col:
            try:
                records = sorted(records, key=lambda x: float(x.get(pct_col) or 0), reverse=True)
            except (TypeError, ValueError):
                pass
        return {"_revenue_breakdown": records}
    except Exception as exc:
        logger.warning("[StockInfo] business analysis failed for %s: %s", symbol, exc)
        return {}


def _fetch_all(symbol: str) -> dict:
    """Fetch from cninfo / eastmoney / ths concurrently with per-source timeout.

    Each source runs in its own thread with a hard timeout; a slow or hanging
    source cannot block the others or blow past the client's request timeout.
    """
    import concurrent.futures

    sources = {
        'cninfo': _fetch_from_cninfo,
        'eastmoney': _fetch_from_em,
        'ths': _fetch_from_ths_business,
    }
    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(fn, symbol): name
            for name, fn in sources.items()
        }
        try:
            for future in concurrent.futures.as_completed(futures, timeout=20):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as exc:
                    logger.warning("[StockInfo] %s fetch failed for %s: %s", name, symbol, exc)
                    results[name] = {}
        except concurrent.futures.TimeoutError:
            # Sources still running past the deadline are abandoned; collect
            # whatever has already finished so a slow source can't fail the lot.
            for fut, name in futures.items():
                if name not in results and fut.done():
                    try:
                        results[name] = fut.result()
                    except Exception:
                        results[name] = {}
                elif name not in results:
                    logger.warning("[StockInfo] %s timed out for %s", name, symbol)
                    results[name] = {}

    cninfo = results.get('cninfo', {})
    em = results.get('eastmoney', {})
    ths = results.get('ths', {})

    merged: dict = {'_sources': []}
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


_lock = threading.Lock()


@router.get("/info", summary="获取个股基本资料")
def get_stock_info(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """Get basic stock profile from combined sources with cache."""
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