# -*- coding: utf-8 -*-
"""K-line data endpoints with multi-source fallback and caching.

Data source fallback chain (daily qfq only):
  1. StockDaily (local DB, synced from batch sync)
  2. KlineSnapshot cache
  3. East Money (akshare.stock_zh_a_hist)
  4. Sina Finance (akshare.stock_zh_a_daily)
  5. Tencent Finance (akshare.stock_zh_a_hist_tx)
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Query, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter()

KLINE_SOURCE_EM = "eastmoney"
KLINE_SOURCE_SINA = "sina"
KLINE_SOURCE_TENCENT = "tencent"

# Fixed defaults
DEFAULT_COUNT = 500


def _kline_data_time(records: list[dict]) -> str | None:
    if not records:
        return None
    latest = records[-1]
    return latest.get("date")


def _kline_is_stale(records: list[dict]) -> bool:
    data_time = _kline_data_time(records)
    if not data_time:
        return True
    try:
        latest_date = datetime.strptime(str(data_time)[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return latest_date < (datetime.now().date() - timedelta(days=7))


def _latest_kline_cache_key(symbol: str, count: int) -> str:
    """Build cache key for latest-count K-line requests."""
    return f"kline:latest:{symbol}:{count}"


def _history_kline_cache_key(symbol: str, start_date: str, end_date: str) -> str:
    """Build cache key for date-range K-line requests."""
    return f"kline:history:{symbol}:{start_date}:{end_date}"


def _get_kline_from_stock_daily(symbol: str, count: int) -> list[dict] | None:
    """Read K-line data from StockDaily table (local DB)."""
    try:
        from src.storage import DatabaseManager
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            from sqlalchemy import select, desc
            from src.storage import StockDaily
            rows = (
                session.execute(
                    select(StockDaily)
                    .where(StockDaily.code == symbol)
                    .order_by(desc(StockDaily.date))
                    .limit(count)
                )
                .scalars()
                .all()
            )
            if not rows:
                return None
            # Convert to records (oldest first for chart display)
            records = []
            for row in reversed(rows):
                records.append({
                    'date': row.date.isoformat() if hasattr(row.date, 'isoformat') else str(row.date),
                    'open': row.open,
                    'high': row.high,
                    'low': row.low,
                    'close': row.close,
                    'volume': row.volume,
                    'amount': row.amount,
                    'pct_chg': row.pct_chg,
                    '_source': 'stock_daily',
                })
            logger.info(f"[K线本地] {symbol} 命中 StockDaily: {len(records)} 条")
            return records
    except Exception as e:
        logger.debug(f"[K线本地] 读取 StockDaily 失败: {e}")
        return None


def _format_kline_date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if "-" in text:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%Y%m%d")
    return text


def _get_kline_range_from_stock_daily(symbol: str, start_date: str, end_date: str) -> list[dict] | None:
    """Read a date range from StockDaily when local data already covers it."""
    try:
        from src.storage import DatabaseManager, StockDaily
        from sqlalchemy import select

        start_dt = datetime.strptime(_format_kline_date(start_date), "%Y%m%d").date()
        end_dt = datetime.strptime(_format_kline_date(end_date), "%Y%m%d").date()
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            bounds = session.execute(
                select(StockDaily.date)
                .where(StockDaily.code == symbol)
                .where(StockDaily.date >= start_dt)
                .where(StockDaily.date <= end_dt)
                .order_by(StockDaily.date)
            ).scalars().all()
            if not bounds:
                return None
            rows = session.execute(
                select(StockDaily)
                .where(StockDaily.code == symbol)
                .where(StockDaily.date >= start_dt)
                .where(StockDaily.date <= end_dt)
                .order_by(StockDaily.date)
            ).scalars().all()
        records = []
        for row in rows:
            records.append({
                'date': row.date.isoformat() if hasattr(row.date, 'isoformat') else str(row.date),
                'open': row.open,
                'high': row.high,
                'low': row.low,
                'close': row.close,
                'volume': row.volume,
                'amount': row.amount,
                'pct_chg': row.pct_chg,
                '_source': 'stock_daily',
            })
        logger.info(f"[K线本地] {symbol} 命中 StockDaily range: {len(records)} 条")
        return records
    except Exception as e:
        logger.debug(f"[K线本地] 读取 StockDaily range 失败: {e}")
        return None


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
    # Shanghai: 60xx, 68xx(科创板), 5xx(ETF), 90xx(B股); Shenzhen: 00xx, 30xx(创业板)
    if code.startswith(('6', '5', '90')):
        prefix = 'sh'
    elif code.startswith(('8', '4', '9')):
        prefix = 'bj'
    else:
        prefix = 'sz'
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
    # Shanghai: 60xx, 68xx(科创板), 5xx(ETF), 90xx(B股); Shenzhen: 00xx, 30xx(创业板)
    if code.startswith(('6', '5', '90')):
        prefix = 'sh'
    elif code.startswith(('8', '4', '9')):
        prefix = 'bj'
    else:
        prefix = 'sz'
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


def _get_kline_from_cache(cache_key: str) -> dict | None:
    try:
        from src.storage import DatabaseManager
        return DatabaseManager.get_instance().get_kline_snapshot(cache_key)
    except Exception as e:
        logger.debug(f"[K线缓存] 读取失败: {e}")
    return None


def _save_kline_to_cache(cache_key: str, symbol: str, data: list, source: str) -> None:
    try:
        from src.storage import DatabaseManager
        payload = {'symbol': symbol, 'source': source, 'data': data, 'count': len(data)}
        DatabaseManager.get_instance().save_kline_snapshot(
            cache_key, json.dumps(payload, ensure_ascii=False),
        )
    except Exception as e:
        logger.debug(f"[K线缓存] 写入失败: {e}")


def _save_to_stock_daily(symbol: str, data: list) -> None:
    """将外部 API 返回的 K 线数据同步写入 stock_daily，供验证数据源和增量同步使用。"""
    if not data:
        return
    try:
        import pandas as pd
        from src.storage import DatabaseManager
        df = pd.DataFrame(data)
        required = ['date', 'open', 'close']
        if not all(c in df.columns for c in required):
            return
        # Ensure pct_chg exists (some sources may omit it)
        if 'pct_chg' not in df.columns and 'close' in df.columns:
            df['pct_chg'] = df['close'].pct_change() * 100
        # Ensure amount exists
        if 'amount' not in df.columns:
            df['amount'] = 0.0
        db = DatabaseManager.get_instance()
        db.save_daily_data(df, symbol, source="api_fallback")
        logger.debug(f"[K线-stock_daily] 写入 {symbol} {len(df)} 条")
    except Exception as e:
        logger.debug(f"[K线-stock_daily] 写入失败 {symbol}: {e}")


def fetch_and_persist_kline(
    symbol: str,
    count: int = DEFAULT_COUNT,
    start_date: str | date | datetime | None = None,
    end_date: str | date | datetime | None = None,
    use_cache: bool = True,
) -> tuple[list[dict], str]:
    """Unified K-line entry: StockDaily → cache → multi-source fallback, then persist."""
    range_mode = start_date is not None and end_date is not None

    if range_mode:
        start = _format_kline_date(start_date)
        end = _format_kline_date(end_date)
        records = _get_kline_range_from_stock_daily(symbol, start, end)
        if records:
            return records, "stock_daily"
        cache_key = _history_kline_cache_key(symbol, start, end)
    else:
        records = _get_kline_from_stock_daily(symbol, count)
        if records and len(records) >= count:
            return records, "stock_daily"
        cache_key = _latest_kline_cache_key(symbol, count)

    if use_cache and not _is_trading_hours():
        cached = _get_kline_from_cache(cache_key)
        if cached:
            cached_records = cached.get('data') or []
            if cached_records:
                return cached_records, cached.get('source') or "cache"

    if range_mode:
        fetch_start, fetch_end = start, end
    else:
        fetch_end = datetime.now().strftime("%Y%m%d")
        fetch_start = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")

    records, source = _fetch_kline_with_fallback(symbol, fetch_start, fetch_end)
    if not range_mode and len(records) > count:
        records = records[-count:]

    _save_kline_to_cache(cache_key, symbol, records, source)
    _save_to_stock_daily(symbol, records)
    return records, source


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", summary="获取日线K线数据")
def get_kline(
    symbol: str = Query(..., description="股票代码"),
    count: int = Query(DEFAULT_COUNT, ge=1, le=1000, description="返回条数"),
    use_cache: bool = Query(True, description="是否使用缓存"),
):
    """获取日线K线数据（前复权）。优先本地 StockDaily，其次缓存，最后外部 API。"""
    records, source = fetch_and_persist_kline(symbol, count=count, use_cache=use_cache)
    now_ts = datetime.now().isoformat()

    return {
        'symbol': symbol, 'source': source,
        'count': len(records), 'data': records,
        '_fetched_at': now_ts, '_cached': source in ("stock_daily", "cache"),
        'data_time': _kline_data_time(records),
        'is_stale': _kline_is_stale(records),
        'fallback_used': source != KLINE_SOURCE_EM,
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

    records, source = fetch_and_persist_kline(
        symbol,
        start_date=start_date,
        end_date=end_date,
        use_cache=use_cache,
    )
    now_ts = datetime.now().isoformat()

    return {
        'symbol': symbol, 'source': source,
        'count': len(records), 'data': records,
        '_fetched_at': now_ts, '_cached': source in ("stock_daily", "cache"),
        'data_time': _kline_data_time(records),
        'is_stale': _kline_is_stale(records),
        'fallback_used': source != KLINE_SOURCE_EM,
    }
