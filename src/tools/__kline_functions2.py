"""Function group 2 extracted from src/tools/_kline.py."""

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

__all__ = ['_fetch_kline_tencent', '_fetch_kline_with_fallback', '_get_kline_from_cache', '_timestamp_is_recent', '_local_records_are_fresh', '_append_realtime_daily_bar', '_save_kline_to_cache', '_save_to_stock_daily', 'fetch_and_persist_kline', '_fallback_used', 'get_kline', 'get_history_data']

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
                symbol=tx_symbol,
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
                timeout=15,
            ),
        )
        elapsed = time.time() - t0
        if df is not None and not df.empty:
            logger.info(f"[K线-腾讯] 成功 {len(df)} 行, {elapsed:.2f}s")
            # 腾讯末列 amount 实际是成交量（手），映射到 volume 后由统一层换算成股；
            # 腾讯不提供成交额，留空以免污染 StockDaily。
            rename_map = {
                "date": "日期",
                "open": "开盘",
                "high": "最高",
                "low": "最低",
                "close": "收盘",
                "amount": "成交量",
            }
            df = df.rename(columns=rename_map)
            if "pct_chg" in df.columns:
                df = df.rename(columns={"pct_chg": "涨跌幅"})
            elif "收盘" in df.columns and "涨跌幅" not in df.columns:
                # 首行无前一日基准填 None，避免误标为平盘（与新浪一致）。
                df["涨跌幅"] = df["收盘"].pct_change() * 100
            return df
        logger.warning(f"[K线-腾讯] 空数据, {elapsed:.2f}s")
    except Exception as e:
        logger.warning(f"[K线-腾讯] 失败: {e}")
    return None

def _fetch_kline_with_fallback(
    symbol: str,
    start_date: str,
    end_date: str,
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
        raise HTTPException(
            status_code=502,
            detail={
                "error": "all_sources_failed",
                "message": f"所有数据源获取K线数据失败: {last_error}",
            },
        )
    raise HTTPException(
        status_code=502,
        detail={
            "error": "empty_data",
            "message": f"所有数据源均返回空数据 for {symbol}",
        },
    )

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

        payload = {"symbol": symbol, "source": source, "data": data, "count": len(data)}
        DatabaseManager.get_instance().save_kline_snapshot(
            cache_key,
            json.dumps(payload, ensure_ascii=False),
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
        required = ["date", "open", "close"]
        if not all(c in df.columns for c in required):
            return
        # Ensure pct_chg exists (some sources may omit it)
        if "pct_chg" not in df.columns and "close" in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100
        # date 列转回 date 对象（SQLite Date 列不接受字符串）
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date

        # 成交额缺失保护：腾讯源不提供成交额，None 经 UPSERT 会覆盖已有真实值。
        # 先查出库中这些 (code,date) 已有的 amount，回填到缺失行。
        if "amount" not in df.columns:
            df["amount"] = pd.NA
        if df["amount"].isna().any():
            db = DatabaseManager.get_instance()
            dates = [d for d in df["date"].tolist() if d is not None]
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
                df["amount"] = df.apply(
                    lambda r: existing.get(r["date"]) if pd.isna(r["amount"]) else r["amount"],
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
            cached_records = _normalize_record_list(cached.get("data") or [], cached.get("source"))
            cache_recent_enough = not _is_trading_hours() or _timestamp_is_recent(cached.get("_fetched_at"))
            if cached_records and (range_mode or (not _kline_is_stale(cached_records) and cache_recent_enough)):
                return cached_records, cached.get("source") or "cache"

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
        "success": bool(records),
        "partial": False,
        "symbol": symbol,
        "source": source,
        "count": len(records),
        "data": records,
        "_fetched_at": now_ts,
        "_cached": source in ("stock_daily", "cache"),
        "data_time": _kline_data_time(records),
        "is_stale": _kline_is_stale(records),
        "fallback_used": _fallback_used(symbol, source),
        "adjust": "qfq",
        "period": "daily",
        "volume_unit": "股",
        "amount_unit": "元",
        "bar_complete": not (
            records
            and str(records[-1].get("date"))[:10] == datetime.now().date().isoformat()
            and datetime.now().time() < datetime.strptime("15:00", "%H:%M").time()
        ),
        "errors": [] if records else [f"{symbol} 未获取到 K 线数据"],
        "warnings": [],
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
        "success": bool(records),
        "partial": False,
        "symbol": symbol,
        "source": source,
        "count": len(records),
        "data": records,
        "_fetched_at": now_ts,
        "_cached": source in ("stock_daily", "cache"),
        "data_time": _kline_data_time(records),
        # 按日期范围取历史数据时，freshness（最新日期 vs 今天）无意义：
        # 用户要的就是一段历史，最新日期早于今天属正常，不应标记为 stale。
        # 该字段仅对 get_kline（取最新 N 条）有意义，range 模式置 None。
        "is_stale": None,
        "fallback_used": _fallback_used(symbol, source),
        "adjust": "qfq",
        "period": "daily",
        "volume_unit": "股",
        "amount_unit": "元",
        "errors": [] if records else [f"{symbol} 在请求区间内没有 K 线数据"],
        "warnings": [],
    }
