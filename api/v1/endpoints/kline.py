# -*- coding: utf-8 -*-
"""K-line data endpoints with multi-source fallback and caching.

Data source fallback chain (daily qfq only):
  1. East Money (akshare.stock_zh_a_hist)
  2. Sina Finance (akshare.stock_zh_a_daily)
  3. Tencent Finance (akshare.stock_zh_a_hist_tx)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter()

KLINE_SOURCE_EM = "eastmoney"
KLINE_SOURCE_SINA = "sina"
KLINE_SOURCE_TENCENT = "tencent"

# Fixed defaults
DEFAULT_COUNT = 500


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_kline_df(df, stock_code: str, source: str) -> list[dict]:
    """Normalize akshare K-line DataFrame to standard dict list."""
    import pandas as pd

    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return []

    df = df.copy()

    col_map = {
        '日期': 'date', '开盘': 'open', '收盘': 'close',
        '最高': 'high', '最低': 'low', '成交量': 'volume',
        '成交额': 'amount', '涨跌幅': 'pct_chg', '换手率': 'turnover_rate',
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    keep = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate']
    df = df[[c for c in keep if c in df.columns]]

    if 'date' in df.columns:
        df['date'] = df['date'].astype(str)

    df = df.where(pd.notnull(df), None)

    records = df.to_dict(orient='records')
    for r in records:
        r['_source'] = source
    return records


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _fetch_kline_em(symbol: str, start_date: str, end_date: str):
    """East Money via akshare."""
    import akshare as ak
    import time

    t0 = time.time()
    logger.info(f"[K线-东财] ak.stock_zh_a_hist({symbol}, {start_date}~{end_date})")

    df = ak.stock_zh_a_hist(
        symbol=symbol, period="daily",
        start_date=start_date, end_date=end_date, adjust="qfq",
    )
    elapsed = time.time() - t0
    if df is not None and not df.empty:
        logger.info(f"[K线-东财] 成功 {len(df)} 行, {elapsed:.2f}s")
        return df
    logger.warning(f"[K线-东财] 空数据, {elapsed:.2f}s")
    return None


def _fetch_kline_sina(symbol: str, start_date: str, end_date: str):
    """Sina Finance via akshare."""
    import akshare as ak
    import time

    from data_provider.utils import normalize_stock_code
    code = normalize_stock_code(symbol)
    if code.startswith(('68', '30', '00', '002', '003')):
        prefix = 'sz'
    elif code.startswith(('60',)):
        prefix = 'sh'
    elif code.startswith(('8', '4', '9')):
        prefix = 'bj'
    else:
        prefix = 'sh'
    sina_symbol = f"{prefix}{code}"

    t0 = time.time()
    logger.info(f"[K线-新浪] ak.stock_zh_a_daily({sina_symbol})")

    try:
        df = ak.stock_zh_a_daily(
            symbol=sina_symbol, start_date=start_date,
            end_date=end_date, adjust="qfq",
        )
        elapsed = time.time() - t0
        if df is not None and not df.empty:
            logger.info(f"[K线-新浪] 成功 {len(df)} 行, {elapsed:.2f}s")
            rename_map = {
                'date': '日期', 'open': '开盘', 'high': '最高',
                'low': '最低', 'close': '收盘', 'volume': '成交量',
                'amount': '成交额',
            }
            df = df.rename(columns=rename_map)
            if '收盘' in df.columns and '涨跌幅' not in df.columns:
                df['涨跌幅'] = df['收盘'].pct_change() * 100
                df['涨跌幅'] = df['涨跌幅'].fillna(0)
            return df
        logger.warning(f"[K线-新浪] 空数据, {elapsed:.2f}s")
    except Exception as e:
        logger.warning(f"[K线-新浪] 失败: {e}")
    return None


def _fetch_kline_tencent(symbol: str, start_date: str, end_date: str):
    """Tencent Finance via akshare."""
    import akshare as ak
    import time

    from data_provider.utils import normalize_stock_code
    code = normalize_stock_code(symbol)
    if code.startswith(('68', '30', '00', '002', '003')):
        prefix = 'sz'
    elif code.startswith(('60',)):
        prefix = 'sh'
    elif code.startswith(('8', '4', '9')):
        prefix = 'bj'
    else:
        prefix = 'sh'
    tx_symbol = f"{prefix}{code}"

    t0 = time.time()
    logger.info(f"[K线-腾讯] ak.stock_zh_a_hist_tx({tx_symbol})")

    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=tx_symbol, start_date=start_date,
            end_date=end_date, adjust="qfq",
        )
        elapsed = time.time() - t0
        if df is not None and not df.empty:
            logger.info(f"[K线-腾讯] 成功 {len(df)} 行, {elapsed:.2f}s")
            rename_map = {
                'date': '日期', 'open': '开盘', 'high': '最高',
                'low': '最低', 'close': '收盘', 'volume': '成交量',
                'amount': '成交额',
            }
            df = df.rename(columns=rename_map)
            if 'pct_chg' in df.columns:
                df = df.rename(columns={'pct_chg': '涨跌幅'})
            elif '收盘' in df.columns and '涨跌幅' not in df.columns:
                df['涨跌幅'] = df['收盘'].pct_change() * 100
                df['涨跌幅'] = df['涨跌幅'].fillna(0)
            return df
        logger.warning(f"[K线-腾讯] 空数据, {elapsed:.2f}s")
    except Exception as e:
        logger.warning(f"[K线-腾讯] 失败: {e}")
    return None


_CHAIN = [
    (_fetch_kline_em, KLINE_SOURCE_EM, "东方财富"),
    (_fetch_kline_sina, KLINE_SOURCE_SINA, "新浪财经"),
    (_fetch_kline_tencent, KLINE_SOURCE_TENCENT, "腾讯财经"),
]


def _fetch_kline_with_fallback(
    symbol: str, start_date: str, end_date: str,
) -> tuple[list[dict], str]:
    """Fetch K-line with fallback: East Money → Sina → Tencent."""
    last_error = None
    for fetcher, source_key, source_label in _CHAIN:
        try:
            logger.info(f"[K线] 尝试 {source_label}...")
            df = fetcher(symbol, start_date, end_date)
            if df is not None and not df.empty:
                records = _normalize_kline_df(df, symbol, source_key)
                if records:
                    logger.info(f"[K线] {source_label} 成功，{len(records)} 条")
                    return records, source_key
        except Exception as e:
            last_error = e
            logger.warning(f"[K线] {source_label} 失败: {e}")
            continue

    if last_error:
        raise HTTPException(status_code=502, detail={
            "error": "all_sources_failed",
            "message": f"所有数据源获取K线数据失败: {last_error}",
        })
    raise HTTPException(status_code=502, detail={
        "error": "empty_data",
        "message": f"所有数据源均返回空数据 for {symbol}",
    })


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _is_trading_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    from datetime import time
    t = now.time()
    return (time(9, 30) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))


def _get_kline_from_cache(symbol: str) -> dict | None:
    try:
        from src.storage import DatabaseManager
        return DatabaseManager.get_instance().get_kline_snapshot(symbol)
    except Exception as e:
        logger.debug(f"[K线缓存] 读取失败: {e}")
    return None


def _save_kline_to_cache(symbol: str, data: list, source: str) -> None:
    try:
        from src.storage import DatabaseManager
        payload = {'symbol': symbol, 'source': source, 'data': data, 'count': len(data)}
        DatabaseManager.get_instance().save_kline_snapshot(
            symbol, json.dumps(payload, ensure_ascii=False),
        )
    except Exception as e:
        logger.debug(f"[K线缓存] 写入失败: {e}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", summary="获取日线K线数据")
def get_kline(
    symbol: str = Query(..., description="股票代码"),
    count: int = Query(DEFAULT_COUNT, ge=1, le=1000, description="返回条数"),
    use_cache: bool = Query(True, description="是否使用缓存"),
):
    """获取日线K线数据（前复权）。非交易时段优先缓存，交易时段实时拉取。"""
    if use_cache and not _is_trading_hours():
        cached = _get_kline_from_cache(symbol)
        if cached:
            logger.info(f"[K线缓存] 命中 {symbol}")
            cached['_cached'] = True
            return cached

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")

    records, source = _fetch_kline_with_fallback(symbol, start_date, end_date)

    if len(records) > count:
        records = records[-count:]

    now_ts = datetime.now().isoformat()
    _save_kline_to_cache(symbol, records, source)

    return {
        'symbol': symbol, 'source': source,
        'count': len(records), 'data': records,
        '_fetched_at': now_ts, '_cached': False,
    }


@router.get("/history", summary="获取日线K线数据（按日期范围）")
def get_history_data(
    symbol: str = Query(..., description="股票代码"),
    start_date: str = Query(..., description="起始日期 YYYYMMDD"),
    end_date: str = Query(..., description="结束日期 YYYYMMDD"),
    use_cache: bool = Query(True, description="是否使用缓存"),
):
    """获取指定日期范围的日线K线数据（前复权）。"""
    try:
        datetime.strptime(start_date, "%Y%m%d")
        datetime.strptime(end_date, "%Y%m%d")
    except ValueError:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_date", "message": "日期格式错误，应为 YYYYMMDD",
        })

    if use_cache and not _is_trading_hours():
        cached = _get_kline_from_cache(symbol)
        if cached:
            logger.info(f"[K线缓存] 命中 {symbol}")
            cached['_cached'] = True
            return cached

    records, source = _fetch_kline_with_fallback(symbol, start_date, end_date)

    now_ts = datetime.now().isoformat()
    _save_kline_to_cache(symbol, records, source)

    return {
        'symbol': symbol, 'source': source,
        'count': len(records), 'data': records,
        '_fetched_at': now_ts, '_cached': False,
    }
