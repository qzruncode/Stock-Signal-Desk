# -*- coding: utf-8 -*-
"""Stock basic info endpoint — fundamental data complementing realtime quotes.

Data sources:
  - Primary: ak.stock_profile_cninfo() — cninfo, 公司概况 (name, industry, listing date, business, profile)
  - Supplementary: ak.stock_individual_info_em() — East Money push API (total shares, circ shares, PE/PB)
    May fail when East Money blocks the connection; endpoint returns partial data gracefully.

The data returned here is largely static (changes at most daily), so a per-day cache is used.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()

CACHE_KEY = "stock_info:v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _cache_key(symbol: str) -> str:
    return f"{CACHE_KEY}:{_normalize_symbol(symbol)}:{datetime.now().strftime('%Y%m%d')}"


def _normalize_symbol(symbol: str) -> str:
    code = symbol.strip().upper()
    if "." in code:
        code = code.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            return code[2:]
    return code


def _cache_get(symbol: str) -> dict | None:
    try:
        from src.storage import DatabaseManager
        raw = DatabaseManager.get_instance().get_kline_snapshot(_cache_key(symbol))
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and 'symbol' in data:
                logger.info(f"[StockInfo] cache HIT {_cache_key(symbol)}")
                return data
    except Exception as e:
        logger.warning(f"[StockInfo] cache read error: {e}")
    return None


def _cache_put(symbol: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        DatabaseManager.get_instance().save_kline_snapshot(
            _cache_key(symbol), json.dumps(data, ensure_ascii=False))
        logger.info(f"[StockInfo] cache SAVED {_cache_key(symbol)}")
    except Exception as e:
        logger.warning(f"[StockInfo] cache write error: {e}")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_from_cninfo(symbol: str) -> dict:
    """Fetch stock basic info from cninfo (巨潮资讯).

    Returns fields: name, short_name, industry, market, listing_date,
    register_capital, main_business, business_scope, profile.
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {}

    try:
        df = ak.stock_profile_cninfo(symbol=symbol)
        if df is not None and not df.empty:
            row = df.iloc[0]
            result = {
                'name': str(row.get('公司名称', '') or ''),
                'short_name': str(row.get('A股简称', '') or ''),
                'industry': str(row.get('所属行业', '') or ''),
                'market': str(row.get('所属市场', '') or ''),
                'listing_date': str(row.get('上市日期', '') or ''),
                'establish_date': str(row.get('成立日期', '') or ''),
                'register_capital': _safe_float(row.get('注册资金')),
                'main_business': str(row.get('主营业务', '') or ''),
                'business_scope': str(row.get('经营范围', '') or ''),
                'profile': str(row.get('机构简介', '') or ''),
                'website': str(row.get('官方网站', '') or ''),
                '_cninfo_ok': True,
            }
            logger.info(f"[StockInfo] cninfo OK for {symbol}: {_time.time() - t0:.1f}s")
        else:
            logger.warning(f"[StockInfo] cninfo returned empty for {symbol}")
    except Exception as e:
        logger.warning(f"[StockInfo] cninfo failed for {symbol}: {e}")

    return result


def _fetch_from_em(symbol: str) -> dict:
    """Fetch supplementary data from East Money push API.

    Returns:
      - Company info (fallback for cninfo): short_name, industry, listing_date
      - Shares: total_shares, circ_shares
      - Valuation: pe_dynamic, pe_static, pb_ratio, total_mv, circ_mv

    May fail; returns empty dict on failure.
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {}

    try:
        df = ak.stock_individual_info_em(symbol=symbol, timeout=10)
        if df is not None and not df.empty:
            # Map item→value pairs to dict
            info_map: dict[str, str] = {}
            for _, row in df.iterrows():
                item = str(row.get('item', ''))
                value = str(row.get('value', ''))
                info_map[item] = value

            # Company info fields — serve as fallback when cninfo is unavailable
            em_short_name = str(info_map.get('股票简称', '') or '')
            em_industry = str(info_map.get('行业', '') or '')
            em_listing_date = str(info_map.get('上市时间', '') or '')

            result = {
                # Company info fallback (cninfo takes priority)
                'short_name': em_short_name if em_short_name else None,
                'industry': em_industry if em_industry else None,
                'listing_date': em_listing_date if em_listing_date else None,
                # Shares & valuation (EM is primary source)
                'total_shares': _safe_float(info_map.get('总股本')),
                'circ_shares': _safe_float(info_map.get('流通股')),
                'pe_dynamic': _safe_float(info_map.get('市盈率-动态')),
                'pe_static': _safe_float(info_map.get('市盈率-静态')),
                'pb_ratio': _safe_float(info_map.get('市净率')),
                'total_mv': _safe_float(info_map.get('总市值')),
                'circ_mv': _safe_float(info_map.get('流通市值')),
                '_em_ok': True,
            }
            logger.info(f"[StockInfo] EM OK for {symbol}: {_time.time() - t0:.1f}s")
        else:
            logger.warning(f"[StockInfo] EM returned empty for {symbol}")
    except Exception as e:
        logger.warning(f"[StockInfo] EM failed for {symbol}: {e}")

    return result


def _fetch_all(symbol: str) -> dict:
    """Fetch stock info from all sources and merge with fallback chain.

    Fallback strategy:
      Company info:  CNINFO ──fail──▶ EM (行业/简称/上市时间)
      Shares/PE/PB:  EM     ──fail──▶ (none; realtime quote has pe/pb already)
    """
    import time as _time

    t0 = _time.time()

    # cninfo is primary for company info (more reliable, richer data)
    cninfo_data = _fetch_from_cninfo(symbol)

    # EM is primary for shares/valuation, secondary for company info
    em_data = _fetch_from_em(symbol)

    # Merge: EM as base (shares + valuation + company fallback),
    #         cninfo overrides (takes priority for company fields)
    result: dict = {'symbol': symbol}
    result.update(em_data)       # EM: shares, valuation, company fallback
    result.update(cninfo_data)   # cninfo: overrides company fields, adds profile/business

    result['_fetched_at'] = datetime.now().isoformat()
    result['_cached'] = False

    logger.info(f"[StockInfo] total {_time.time() - t0:.1f}s for {symbol} "
                f"(cninfo={cninfo_data.get('_cninfo_ok', False)}, "
                f"em={em_data.get('_em_ok', False)})")
    return result


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

_lock = None


@router.get("/info", summary="获取个股基本资料")
def get_stock_info(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取单只股票的基本资料。

    数据源:
    - Primary: stock_profile_cninfo (巨潮资讯) — 公司名称、行业、上市日期、主营业务、公司简介
    - Supplementary: stock_individual_info_em (东方财富) — 总股本、流通股本、市盈率、市净率

    按天缓存。当 East Money 被屏蔽时，返回 cninfo 数据（部分字段可能为空）。
    """
    symbol = _normalize_symbol(symbol)

    if not force:
        cached = _cache_get(symbol)
        if cached:
            cached['_cached'] = True

            # Background refresh
            global _lock
            if _lock is None:
                _lock = threading.Lock()
            if _lock.acquire(blocking=False):
                def _bg_refresh():
                    try:
                        _cache_put(symbol, _fetch_all(symbol))
                    finally:
                        _lock.release()
                threading.Thread(target=_bg_refresh, daemon=True).start()

            return cached

    data = _fetch_all(symbol)
    _cache_put(symbol, data)
    return data
