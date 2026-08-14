"""Function group 1 extracted from src/tools/_kline.py."""

from __future__ import annotations

from src.tools._kline import (
    json,
    logging,
    random,
    time,
    date,
    datetime,
    timedelta,
    Any,
    HTTPException,
    RealtimeCircuitBreaker,
    akshare_rate_limiter,
    is_trading_time,
    trade_dates,
    logger,
    KLINE_SOURCE_EM,
    KLINE_SOURCE_SINA,
    KLINE_SOURCE_TENCENT,
    DEFAULT_COUNT,
    KLINE_SINGLE_SOURCE_RETRY_ATTEMPTS,
    KLINE_SINGLE_SOURCE_RETRY_BASE_DELAY,
    KLINE_FALLBACK_DELAY_MIN_SECONDS,
    KLINE_FALLBACK_DELAY_MAX_SECONDS,
    KLINE_AKSHARE_MIN_INTERVAL_SECONDS,
    KLINE_AKSHARE_MAX_JITTER_SECONDS,
    _kline_source_circuit_breaker,
    KLINE_DESCRIPTION,
    KLINE_HISTORY_DESCRIPTION,
    _is_trading_hours,
 )

__all__ = ['_kline_data_time', '_expected_latest_kline_date', '_kline_is_stale', '_normalize_record_units', '_normalize_record_list', '_latest_kline_cache_key', '_history_kline_cache_key', '_get_kline_from_stock_daily', '_format_kline_date', '_market_prefixed_symbol', '_bare_stock_code', '_is_beijing_exchange', '_get_kline_range_from_stock_daily', '_normalize_kline_df', '_wait_before_akshare_call', '_fetch_with_single_source_retry', '_sleep_before_next_source', '_fetch_kline_em', '_fetch_kline_sina']

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
        calendar = [day for day in trade_dates() if day <= now.date()]
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
    if (
        volume_value is not None
        and not detected
        and source_name
        in {
            KLINE_SOURCE_EM,
            KLINE_SOURCE_TENCENT,
            "akshare",
            "东方财富",
            "腾讯财经",
        }
    ):
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
                    select(StockDaily).where(StockDaily.code == symbol).order_by(desc(StockDaily.date)).limit(count)
                )
                .scalars()
                .all()
            )
            if not rows:
                return None
            # Convert to records (oldest first for chart display)
            records = []
            for row in reversed(rows):
                records.append(
                    _normalize_record_units(
                        {
                            "date": row.date.isoformat() if hasattr(row.date, "isoformat") else str(row.date),
                            "open": row.open,
                            "high": row.high,
                            "low": row.low,
                            "close": row.close,
                            "volume": row.volume,
                            "amount": row.amount,
                            "pct_chg": row.pct_chg,
                            "ma5": row.ma5,
                            "ma10": row.ma10,
                            "ma20": row.ma20,
                            "volume_ratio": row.volume_ratio,
                            "data_source": row.data_source,
                            "_source": "stock_daily",
                            "_updated_at": row.updated_at.isoformat() if row.updated_at else None,
                        },
                        row.data_source,
                    )
                )
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
        prefix = "bj"
    elif code.startswith(("6", "5", "90")):
        prefix = "sh"
    else:
        prefix = "sz"
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
    symbol: str,
    start_date: str,
    end_date: str,
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
            rows = (
                session.execute(
                    select(StockDaily)
                    .where(StockDaily.code == symbol)
                    .where(StockDaily.date >= start_dt)
                    .where(StockDaily.date <= end_dt)
                    .order_by(StockDaily.date)
                )
                .scalars()
                .all()
            )
        if not rows:
            return None
        records = []
        for row in rows:
            records.append(
                _normalize_record_units(
                    {
                        "date": row.date.isoformat() if hasattr(row.date, "isoformat") else str(row.date),
                        "open": row.open,
                        "high": row.high,
                        "low": row.low,
                        "close": row.close,
                        "volume": row.volume,
                        "amount": row.amount,
                        "pct_chg": row.pct_chg,
                        "ma5": row.ma5,
                        "ma10": row.ma10,
                        "ma20": row.ma20,
                        "volume_ratio": row.volume_ratio,
                        "data_source": row.data_source,
                        "_source": "stock_daily",
                        "_updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    },
                    row.data_source,
                )
            )

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

def _normalize_kline_df(df, stock_code: str, source: str) -> list[dict]:
    """Normalize akshare K-line DataFrame to standard dict list."""
    import pandas as pd

    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return []

    df = df.copy()

    col_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "涨跌幅": "pct_chg",
        "换手率": "turnover_rate",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    keep = ["date", "open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover_rate"]
    df = df[[c for c in keep if c in df.columns]]

    if "date" in df.columns:
        df["date"] = df["date"].astype(str)

    df = df.where(pd.notnull(df), None)

    records = df.to_dict(orient="records")
    normalized_records = []
    for r in records:
        r["_source"] = source
        normalized_records.append(_normalize_record_units(r, source))
    return normalized_records

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
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
            timeout=15,
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
                symbol=sina_symbol,
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            ),
        )
        elapsed = time.time() - t0
        if df is not None and not df.empty:
            logger.info(f"[K线-新浪] 成功 {len(df)} 行, {elapsed:.2f}s")
            # 新浪 stock_zh_a_daily 列: open/high/low/close/volume/amount/
            # outstanding_share/turnover，其中 turnover=volume/outstanding_share
            # 即换手率，映射到 turnover_rate 以与东财源保持一致。
            rename_map = {
                "date": "日期",
                "open": "开盘",
                "high": "最高",
                "low": "最低",
                "close": "收盘",
                "volume": "成交量",
                "amount": "成交额",
                "turnover": "换手率",
            }
            df = df.rename(columns=rename_map)
            if "收盘" in df.columns and "涨跌幅" not in df.columns:
                # 新浪无涨跌幅列，自算。注意：基于 qfq 收盘价环比，与东财服务端
                # 基于不复权价的口径在有除权日会不一致；首行无前一日基准，填 None
                # 而非 0，避免把“未知”误标成“平盘”。
                df["涨跌幅"] = df["收盘"].pct_change() * 100
            return df
        logger.warning(f"[K线-新浪] 空数据, {elapsed:.2f}s")
    except Exception as e:
        logger.warning(f"[K线-新浪] 失败: {e}")
    return None
