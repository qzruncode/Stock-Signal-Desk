# -*- coding: utf-8 -*-
"""Shared K-line implementation for ``get_kline`` and ``get_history_data``.

工具业务逻辑统一收口到 src/tools/，与 FastAPI 路由层解耦：
api/v1/endpoints/kline.py 仅保留薄路由（Query 校验 + 委托），从此处导入
get_kline / get_history_data。

Data source fallback chain (daily qfq only):
  1. StockDaily (local DB, synced from batch sync)
  2. KlineSnapshot cache
  3. East Money (akshare.stock_zh_a_hist)
  4. Sina Finance (akshare.stock_zh_a_daily)
  5. Tencent Finance (akshare.stock_zh_a_hist_tx)

外部 API 取数成功后会双写：写 KlineSnapshot 缓存 + 回写 StockDaily 表。
"""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from data_provider.circuit_breaker import RealtimeCircuitBreaker
from data_provider.rate_limiter import akshare_rate_limiter

logger = logging.getLogger(__name__)

KLINE_SOURCE_EM = "eastmoney"
KLINE_SOURCE_SINA = "sina"
KLINE_SOURCE_TENCENT = "tencent"

# Fixed defaults
DEFAULT_COUNT = 500
KLINE_SINGLE_SOURCE_RETRY_ATTEMPTS = 2
KLINE_SINGLE_SOURCE_RETRY_BASE_DELAY = 0.6
KLINE_FALLBACK_DELAY_MIN_SECONDS = 0.8
KLINE_FALLBACK_DELAY_MAX_SECONDS = 1.8
KLINE_AKSHARE_MIN_INTERVAL_SECONDS = 2.0
KLINE_AKSHARE_MAX_JITTER_SECONDS = 5.0

_kline_source_circuit_breaker = RealtimeCircuitBreaker()

KLINE_DESCRIPTION = (
    "获取股票日线K线数据（前复权），包含日期、开盘价、收盘价、最高价、最低价、"
    "成交量（股）、成交额（元）、涨跌幅、换手率；盘中当日 K 线会标记为未完成。"
)

KLINE_HISTORY_DESCRIPTION = (
    "获取指定日期范围的日线K线数据（前复权），适用于需要查看特定时间段行情的场景"
)


def _kline_data_time(records: list[dict]) -> str | None:
    if not records:
        return None
    latest = records[-1]
    return latest.get("date")


def _expected_latest_kline_date(now: datetime | None = None) -> date:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    try:
        from src.tools._market_snapshot import _fetch_trade_dates

        calendar = [day for day in _fetch_trade_dates() if day <= now.date()]
        if now.time() < datetime.strptime("09:30", "%H:%M").time():
            calendar = [day for day in calendar if day < now.date()]
        return calendar[-1]
    except Exception:
        expected = now.date()
        if now.weekday() >= 5 or now.time() < datetime.strptime("09:30", "%H:%M").time():
            expected -= timedelta(days=1)
            while expected.weekday() >= 5:
                expected -= timedelta(days=1)
        return expected


def _kline_is_stale(records: list[dict]) -> bool:
    data_time = _kline_data_time(records)
    if not data_time:
        return True
    try:
        latest_date = datetime.strptime(str(data_time)[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    return latest_date < _expected_latest_kline_date()


def _normalize_record_units(record: dict[str, Any], source: str | None = None) -> dict[str, Any]:
    """Normalize K-line volume to shares and turnover to percent.

    Eastmoney/Tencent expose volume in lots while Sina exposes shares.  Older
    StockDaily rows may contain either unit, so amount/volume/price is used as
    a source-independent detector before falling back to the source label.
    """

    normalized = dict(record)
    volume = normalized.get("volume")
    amount = normalized.get("amount")
    close = normalized.get("close")
    try:
        volume_value = float(volume) if volume is not None else None
    except (TypeError, ValueError):
        volume_value = None
    detected = str(normalized.get("volume_unit") or "") == "股"
    if volume_value and amount is not None and close not in (None, 0):
        try:
            ratio = float(amount) / volume_value / float(close)
            if 30 <= ratio <= 300:
                volume_value *= 100
                detected = True
            elif 0.3 <= ratio <= 3:
                detected = True
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    source_name = str(source or normalized.get("data_source") or normalized.get("_source") or "").lower()
    if volume_value is not None and not detected and source_name in {
        KLINE_SOURCE_EM, KLINE_SOURCE_TENCENT, "akshare", "东方财富", "腾讯财经",
    }:
        volume_value *= 100
    if volume_value is not None:
        normalized["volume"] = volume_value

    turnover = normalized.get("turnover_rate")
    if turnover is not None:
        try:
            turnover_value = float(turnover)
            if source_name in {KLINE_SOURCE_SINA, "新浪财经"} and 0 <= turnover_value <= 1:
                turnover_value *= 100
            normalized["turnover_rate"] = turnover_value
        except (TypeError, ValueError):
            pass
    normalized["volume_unit"] = "股"
    normalized["amount_unit"] = "元"
    return normalized


def _normalize_record_list(records: list[dict], source: str | None = None) -> list[dict]:
    return [_normalize_record_units(record, source) for record in records]


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
                records.append(_normalize_record_units({
                    'date': row.date.isoformat() if hasattr(row.date, 'isoformat') else str(row.date),
                    'open': row.open,
                    'high': row.high,
                    'low': row.low,
                    'close': row.close,
                    'volume': row.volume,
                    'amount': row.amount,
                    'pct_chg': row.pct_chg,
                    'ma5': row.ma5,
                    'ma10': row.ma10,
                    'ma20': row.ma20,
                    'volume_ratio': row.volume_ratio,
                    'data_source': row.data_source,
                    '_source': 'stock_daily',
                    '_updated_at': row.updated_at.isoformat() if row.updated_at else None,
                }, row.data_source))
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


def _market_prefixed_symbol(symbol: str) -> str:
    """Return akshare symbol with sh/sz/bj prefix for providers that require it."""
    code = _bare_stock_code(symbol)

    from data_provider.utils import is_bse_code

    # Shanghai: 60xx, 68xx(科创板), 5xx(ETF), 90xx(B股).
    if is_bse_code(code):
        prefix = 'bj'
    elif code.startswith(('6', '5', '90')):
        prefix = 'sh'
    else:
        prefix = 'sz'
    return f"{prefix}{code}"


def _bare_stock_code(symbol: str) -> str:
    """归一化为不带市场前缀的纯数字股票代码（sh/sz/bj 前缀都会被剥离）。"""
    from data_provider.utils import normalize_stock_code

    code = normalize_stock_code(symbol).strip()
    lower = code.lower()
    if lower.startswith(("sh", "sz", "bj")) and code[2:].isdigit():
        code = code[2:]
    return code


def _is_beijing_exchange(symbol: str) -> bool:
    """是否为北交所股票（8xx/4xx/9xx 开头）。

    akshare stock_zh_a_hist（东财）源码里 market_code = 1 if symbol.startswith("6")
    else 0，北交所股票会被拼成深市 secid（0.830xxx）而东财对此返回空数据。
    故北交所应跳过东财源，直接走新浪/腾讯。
    """
    from data_provider.utils import is_bse_code

    return is_bse_code(_bare_stock_code(symbol))


def _get_kline_range_from_stock_daily(
    symbol: str, start_date: str, end_date: str,
) -> tuple[list[dict], bool] | None:
    """Read a date range from StockDaily, reporting whether local data fully covers it.

    Returns ``(records, complete)`` 或 None（本地完全无数据）。

    ``complete`` 完整性校验优先用交易日历（上证指数日线 date 集合）做精确判断：
    本地数据的 date 集合必须 ⊇ 区间内的全部交易日，否则视为覆盖不全 → 回源取
    整段。若区间内上证指数本地无数据（交易日历不可用），回退到粗略的“首尾日期
    对齐”校验：第一条 date == start_dt 且最后一条 date == end_dt。
    """
    try:
        from src.storage import DatabaseManager, StockDaily
        from sqlalchemy import select

        start_dt = datetime.strptime(_format_kline_date(start_date), "%Y%m%d").date()
        end_dt = datetime.strptime(_format_kline_date(end_date), "%Y%m%d").date()
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            rows = session.execute(
                select(StockDaily)
                .where(StockDaily.code == symbol)
                .where(StockDaily.date >= start_dt)
                .where(StockDaily.date <= end_dt)
                .order_by(StockDaily.date)
            ).scalars().all()
        if not rows:
            return None
        records = []
        for row in rows:
            records.append(_normalize_record_units({
                'date': row.date.isoformat() if hasattr(row.date, 'isoformat') else str(row.date),
                'open': row.open,
                'high': row.high,
                'low': row.low,
                'close': row.close,
                'volume': row.volume,
                'amount': row.amount,
                'pct_chg': row.pct_chg,
                'ma5': row.ma5,
                'ma10': row.ma10,
                'ma20': row.ma20,
                'volume_ratio': row.volume_ratio,
                'data_source': row.data_source,
                '_source': 'stock_daily',
                '_updated_at': row.updated_at.isoformat() if row.updated_at else None,
            }, row.data_source))

        # 完整性校验：优先交易日历精确判断，不可用则回退首尾对齐。
        trading_days = db.get_trading_days(start_dt, end_dt)
        if trading_days is not None:
            local_dates = {row.date for row in rows}
            complete = trading_days.issubset(local_dates)
            method = "trading_calendar"
        else:
            complete = rows[0].date == start_dt and rows[-1].date == end_dt
            method = "head_tail"
        logger.info(
            f"[K线本地] {symbol} 命中 StockDaily range: {len(records)} 条, "
            f"完整覆盖={complete} ({method}) ({rows[0].date}~{rows[-1].date} vs {start_dt}~{end_dt})"
        )
        return records, complete
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
    normalized_records = []
    for r in records:
        r['_source'] = source
        normalized_records.append(_normalize_record_units(r, source))
    return normalized_records


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _wait_before_akshare_call() -> None:
    akshare_rate_limiter.wait(
        min_interval=KLINE_AKSHARE_MIN_INTERVAL_SECONDS,
        max_jitter=KLINE_AKSHARE_MAX_JITTER_SECONDS,
    )


def _fetch_with_single_source_retry(source_label: str, call):
    last_error = None
    for attempt in range(1, KLINE_SINGLE_SOURCE_RETRY_ATTEMPTS + 1):
        try:
            _wait_before_akshare_call()
            return call()
        except Exception as e:
            last_error = e
            if attempt >= KLINE_SINGLE_SOURCE_RETRY_ATTEMPTS:
                raise
            delay = KLINE_SINGLE_SOURCE_RETRY_BASE_DELAY * attempt
            logger.warning("[K线-%s] 第 %d 次调用失败，%.1fs 后重试: %s", source_label, attempt, delay, e)
            time.sleep(delay)
    raise last_error


def _sleep_before_next_source() -> None:
    time.sleep(random.uniform(KLINE_FALLBACK_DELAY_MIN_SECONDS, KLINE_FALLBACK_DELAY_MAX_SECONDS))


def _fetch_kline_em(symbol: str, start_date: str, end_date: str):
    """East Money via akshare."""
    import akshare as ak

    t0 = time.time()
    logger.info(f"[K线-东财] ak.stock_zh_a_hist({symbol}, {start_date}~{end_date})")

    df = _fetch_with_single_source_retry(
        "东财",
        lambda: ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq",
        ),
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

    sina_symbol = _market_prefixed_symbol(symbol)

    t0 = time.time()
    logger.info(f"[K线-新浪] ak.stock_zh_a_daily({sina_symbol})")

    try:
        df = _fetch_with_single_source_retry(
            "新浪",
            lambda: ak.stock_zh_a_daily(
                symbol=sina_symbol, start_date=start_date,
                end_date=end_date, adjust="qfq",
            ),
        )
        elapsed = time.time() - t0
        if df is not None and not df.empty:
            logger.info(f"[K线-新浪] 成功 {len(df)} 行, {elapsed:.2f}s")
            # 新浪 stock_zh_a_daily 列: open/high/low/close/volume/amount/
            # outstanding_share/turnover，其中 turnover=volume/outstanding_share
            # 即换手率，映射到 turnover_rate 以与东财源保持一致。
            rename_map = {
                'date': '日期', 'open': '开盘', 'high': '最高',
                'low': '最低', 'close': '收盘', 'volume': '成交量',
                'amount': '成交额', 'turnover': '换手率',
            }
            df = df.rename(columns=rename_map)
            if '收盘' in df.columns and '涨跌幅' not in df.columns:
                # 新浪无涨跌幅列，自算。注意：基于 qfq 收盘价环比，与东财服务端
                # 基于不复权价的口径在有除权日会不一致；首行无前一日基准，填 None
                # 而非 0，避免把“未知”误标成“平盘”。
                df['涨跌幅'] = df['收盘'].pct_change() * 100
            return df
        logger.warning(f"[K线-新浪] 空数据, {elapsed:.2f}s")
    except Exception as e:
        logger.warning(f"[K线-新浪] 失败: {e}")
    return None


def _fetch_kline_tencent(symbol: str, start_date: str, end_date: str):
    """Tencent Finance via akshare."""
    import akshare as ak

    tx_symbol = _market_prefixed_symbol(symbol)

    t0 = time.time()
    logger.info(f"[K线-腾讯] ak.stock_zh_a_hist_tx({tx_symbol})")

    try:
        df = _fetch_with_single_source_retry(
            "腾讯",
            lambda: ak.stock_zh_a_hist_tx(
                symbol=tx_symbol, start_date=start_date,
                end_date=end_date, adjust="qfq",
            ),
        )
        elapsed = time.time() - t0
        if df is not None and not df.empty:
            logger.info(f"[K线-腾讯] 成功 {len(df)} 行, {elapsed:.2f}s")
            # 腾讯末列 amount 实际是成交量（手），映射到 volume 后由统一层换算成股；
            # 腾讯不提供成交额，留空以免污染 StockDaily。
            rename_map = {
                'date': '日期', 'open': '开盘', 'high': '最高',
                'low': '最低', 'close': '收盘', 'amount': '成交量',
            }
            df = df.rename(columns=rename_map)
            if 'pct_chg' in df.columns:
                df = df.rename(columns={'pct_chg': '涨跌幅'})
            elif '收盘' in df.columns and '涨跌幅' not in df.columns:
                # 首行无前一日基准填 None，避免误标为平盘（与新浪一致）。
                df['涨跌幅'] = df['收盘'].pct_change() * 100
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
    """Fetch K-line with fallback: East Money → Sina → Tencent.

    北交所股票（8xx/4xx/9xx）跳过东财源：akshare stock_zh_a_hist 的 market_code
    只按“6 开头”判沪市，北交所会被当深市拼 secid 而东财恒返回空，徒增无谓
    请求并污染源级熔断计数。北交所直接从新浪起取。
    """
    chain = _CHAIN
    if _is_beijing_exchange(symbol):
        chain = [c for c in _CHAIN if c[1] != KLINE_SOURCE_EM]
        logger.info("[K线] %s 为北交所股票，跳过东财源", symbol)

    last_error = None
    for index, (fetcher, source_key, source_label) in enumerate(chain):
        if not _kline_source_circuit_breaker.is_available(source_key):
            logger.warning("[K线] %s 源级熔断中，跳过", source_label)
            continue
        try:
            logger.info(f"[K线] 尝试 {source_label}...")
            df = fetcher(symbol, start_date, end_date)
            if df is not None and not df.empty:
                records = _normalize_kline_df(df, symbol, source_key)
                if records:
                    _kline_source_circuit_breaker.record_success(source_key)
                    logger.info(f"[K线] {source_label} 成功，{len(records)} 条")
                    return records, source_key
            _kline_source_circuit_breaker.record_failure(source_key)
            if index < len(_CHAIN) - 1:
                _sleep_before_next_source()
        except Exception as e:
            last_error = e
            _kline_source_circuit_breaker.record_failure(source_key, e)
            logger.warning(f"[K线] {source_label} 失败: {e}")
            if index < len(_CHAIN) - 1:
                _sleep_before_next_source()
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
    now = datetime.now().astimezone()
    if now.weekday() >= 5:
        return False
    try:
        from src.tools._market_snapshot import _fetch_trade_dates, expected_trade_day

        if expected_trade_day(now, _fetch_trade_dates()) != now.date():
            return False
    except Exception:
        pass
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


def _timestamp_is_recent(value: Any, max_age_seconds: int = 120) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    now = datetime.now().astimezone()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return 0 <= (now - parsed).total_seconds() <= max_age_seconds


def _local_records_are_fresh(records: list[dict]) -> bool:
    if not records or _kline_is_stale(records):
        return False
    if not _is_trading_hours():
        return True
    return _timestamp_is_recent(records[-1].get("_updated_at"))


def _append_realtime_daily_bar(symbol: str, records: list[dict]) -> tuple[list[dict], bool]:
    """Append/replace today's incomplete daily bar when daily APIs lag intraday."""

    expected = _expected_latest_kline_date()
    now = datetime.now().astimezone()
    if expected != now.date() or now.time() < datetime.strptime("09:30", "%H:%M").time():
        return records, False
    latest_date = None
    if records:
        try:
            latest_date = date.fromisoformat(str(records[-1].get("date"))[:10])
        except ValueError:
            pass
    if latest_date is not None and latest_date >= expected:
        return records, False

    try:
        from src.tools.get_realtime_quotes import get_realtime_quotes

        response = get_realtime_quotes([symbol])
        quote = (response.get("items") or [None])[0]
    except Exception as exc:
        logger.warning("[K线] %s 无法补充实时日线: %s", symbol, exc)
        return records, False
    if not isinstance(quote, dict) or quote.get("price") is None:
        return records, False

    bar = _normalize_record_units(
        {
            "date": expected.isoformat(),
            "open": quote.get("open_price"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("price"),
            "volume": quote.get("volume"),
            "amount": quote.get("amount"),
            "pct_chg": quote.get("change_pct"),
            "turnover_rate": quote.get("turnover_rate"),
            "_source": f"{quote.get('source') or 'realtime'}_partial_daily",
            "_updated_at": quote.get("data_time") or quote.get("trade_time") or quote.get("_fetched_at"),
            "volume_unit": "股",
        },
        str(quote.get("source") or "realtime"),
    )
    required = ("open", "high", "low", "close")
    if any(bar.get(key) is None for key in required):
        return records, False
    return [*records, bar], True


def _save_kline_to_cache(cache_key: str, symbol: str, data: list, source: str) -> None:
    try:
        from src.storage import DatabaseManager
        payload = {'symbol': symbol, 'source': source, 'data': data, 'count': len(data)}
        DatabaseManager.get_instance().save_kline_snapshot(
            cache_key, json.dumps(payload, ensure_ascii=False),
        )
    except Exception as e:
        logger.debug(f"[K线缓存] 写入失败: {e}")


def _save_to_stock_daily(symbol: str, data: list, source: str = "api_fallback") -> None:
    """将外部 API 返回的 K 线数据同步写入 stock_daily，供验证数据源和增量同步使用。

    注意两点历史坑（曾导致回写长期静默失败 / 污染）：
    1. ``save_daily_data`` 的参数名是 ``data_source`` 不是 ``source``；SQLite 的
       Date 列只接受 ``date`` 对象、不接受字符串。``_normalize_kline_df`` 会把
       date 转成字符串，故这里要转回 ``date`` 再写。
    2. 缺成交额的源（腾讯：amount 是手数、不提供成交额）不能让 None 经 UPSERT
       覆盖同 (code,date) 已由东财/新浪回写的真实成交额。回写前用库中已有值
       回填缺失的 amount，避免“用空覆盖好数据”。
    """
    if not data:
        return
    try:
        import pandas as pd
        from datetime import datetime as _dt
        from src.storage import DatabaseManager, StockDaily
        from sqlalchemy import select

        df = pd.DataFrame(data)
        required = ['date', 'open', 'close']
        if not all(c in df.columns for c in required):
            return
        # Ensure pct_chg exists (some sources may omit it)
        if 'pct_chg' not in df.columns and 'close' in df.columns:
            df['pct_chg'] = df['close'].pct_change() * 100
        # date 列转回 date 对象（SQLite Date 列不接受字符串）
        df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date

        # 成交额缺失保护：腾讯源不提供成交额，None 经 UPSERT 会覆盖已有真实值。
        # 先查出库中这些 (code,date) 已有的 amount，回填到缺失行。
        if 'amount' not in df.columns:
            df['amount'] = pd.NA
        if df['amount'].isna().any():
            db = DatabaseManager.get_instance()
            dates = [d for d in df['date'].tolist() if d is not None]
            existing: dict = {}
            if dates:
                with db.get_session() as session:
                    rows = session.execute(
                        select(StockDaily.date, StockDaily.amount).where(
                            StockDaily.code == symbol,
                            StockDaily.date.in_(dates),
                            StockDaily.amount.isnot(None),
                        )
                    ).all()
                    existing = {row[0]: row[1] for row in rows}
            if existing:
                df['amount'] = df.apply(
                    lambda r: existing.get(r['date']) if pd.isna(r['amount']) else r['amount'],
                    axis=1,
                )

        db = DatabaseManager.get_instance()
        db.save_daily_data(df, symbol, data_source=f"{source}_shares")
        logger.debug(f"[K线-stock_daily] 写入 {symbol} {len(df)} 条 (source={source})")
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
        if use_cache:
            local = _get_kline_range_from_stock_daily(symbol, start, end)
            if local is not None:
                records, complete = local
                if records and complete:
                    return records, "stock_daily"
                # 本地有部分日期但首尾未对齐（区间覆盖不全）：放弃这部分数据，
                # 继续走外部 API 取整段，避免“以偏概全”返回截断数据。
        cache_key = _history_kline_cache_key(symbol, start, end)
    else:
        if use_cache:
            records = _get_kline_from_stock_daily(symbol, count)
            if records and len(records) >= count and _local_records_are_fresh(records):
                return records, "stock_daily"
        cache_key = _latest_kline_cache_key(symbol, count)

    if use_cache:
        cached = _get_kline_from_cache(cache_key)
        if cached:
            cached_records = _normalize_record_list(cached.get('data') or [], cached.get('source'))
            cache_recent_enough = not _is_trading_hours() or _timestamp_is_recent(cached.get('_fetched_at'))
            if cached_records and (range_mode or (not _kline_is_stale(cached_records) and cache_recent_enough)):
                return cached_records, cached.get('source') or "cache"

    if range_mode:
        fetch_start, fetch_end = start, end
    else:
        fetch_end = datetime.now().strftime("%Y%m%d")
        # Trading days are roughly 70% of calendar days.  Add margin for long
        # holiday closures so count=500 can actually return about 500 bars.
        lookback_days = max(120, int(count * 1.7) + 30)
        fetch_start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")

    records, source = _fetch_kline_with_fallback(symbol, fetch_start, fetch_end)
    if not range_mode:
        records, _ = _append_realtime_daily_bar(symbol, records)
    if not range_mode and len(records) > count:
        records = records[-count:]

    _save_kline_to_cache(cache_key, symbol, records, source)
    _save_to_stock_daily(symbol, records, source=source)
    return records, source


# ---------------------------------------------------------------------------
# Tool business functions (called by registry + thin route)
# ---------------------------------------------------------------------------

def _fallback_used(symbol: str, source: str) -> bool:
    """是否用到了非首选数据源。

    首选源：沪深/科创板为东财；北交所（akshare 东财接口恒返回空）首选为新浪。
    命中本地（stock_daily/cache）不算 fallback。这样北交所走新浪不会被误标为
    “异常回退”。
    """
    if source in ("stock_daily", "cache"):
        return False
    primary = KLINE_SOURCE_SINA if _is_beijing_exchange(symbol) else KLINE_SOURCE_EM
    return source != primary


def get_kline(symbol: str, count: int = DEFAULT_COUNT, use_cache: bool = True) -> dict[str, Any]:
    """获取日线K线数据（前复权）。优先本地 StockDaily，其次缓存，最后外部 API。

    Args:
        symbol: 股票代码。
        count: 返回最近 N 条 K 线，默认 500。
        use_cache: 是否使用缓存。
    """
    records, source = fetch_and_persist_kline(symbol, count=count, use_cache=use_cache)
    now_ts = datetime.now().astimezone().isoformat()

    return {
        'success': bool(records),
        'partial': False,
        'symbol': symbol, 'source': source,
        'count': len(records), 'data': records,
        '_fetched_at': now_ts, '_cached': source in ("stock_daily", "cache"),
        'data_time': _kline_data_time(records),
        'is_stale': _kline_is_stale(records),
        'fallback_used': _fallback_used(symbol, source),
        'adjust': 'qfq', 'period': 'daily',
        'volume_unit': '股', 'amount_unit': '元',
        'bar_complete': not (
            records
            and str(records[-1].get('date'))[:10] == datetime.now().date().isoformat()
            and datetime.now().time() < datetime.strptime("15:00", "%H:%M").time()
        ),
        'errors': [] if records else [f'{symbol} 未获取到 K 线数据'],
        'warnings': [],
    }


def get_history_data(symbol: str, start_date: str, end_date: str, use_cache: bool = True) -> dict[str, Any]:
    """获取指定日期范围的日线K线数据（前复权）。

    Args:
        symbol: 股票代码。
        start_date: 起始日期 YYYYMMDD。
        end_date: 结束日期 YYYYMMDD。
        use_cache: 是否使用缓存。
    """
    records, source = fetch_and_persist_kline(
        symbol,
        start_date=start_date,
        end_date=end_date,
        use_cache=use_cache,
    )
    now_ts = datetime.now().astimezone().isoformat()

    return {
        'success': bool(records),
        'partial': False,
        'symbol': symbol, 'source': source,
        'count': len(records), 'data': records,
        '_fetched_at': now_ts, '_cached': source in ("stock_daily", "cache"),
        'data_time': _kline_data_time(records),
        # 按日期范围取历史数据时，freshness（最新日期 vs 今天）无意义：
        # 用户要的就是一段历史，最新日期早于今天属正常，不应标记为 stale。
        # 该字段仅对 get_kline（取最新 N 条）有意义，range 模式置 None。
        'is_stale': None,
        'fallback_used': _fallback_used(symbol, source),
        'adjust': 'qfq', 'period': 'daily',
        'volume_unit': '股', 'amount_unit': '元',
        'errors': [] if records else [f'{symbol} 在请求区间内没有 K 线数据'],
        'warnings': [],
    }
