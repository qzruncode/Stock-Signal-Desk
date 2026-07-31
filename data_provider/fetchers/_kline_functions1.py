"""Function group 1 extracted from data_provider/fetchers/kline.py."""

from __future__ import annotations

from data_provider.fetchers.kline import (
    logging,
    datetime,
    timedelta,
    Any,
    Dict,
    List,
    Optional,
    pd,
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_exception,
    before_sleep_log,
    DataFetchError,
    RateLimitError,
    STANDARD_COLUMNS,
    is_bse_code,
    is_st_stock,
    is_kc_cy_stock,
    normalize_stock_code,
    USER_AGENTS,
    logger,
 )

__all__ = ['_is_us_code', '_is_hk_code', '_is_etf_code', '_to_sina_tx_symbol', '_normalize_data', '_is_retryable_kline_error', '_wait_for_kline_retry', '_rate_limited_fetch', 'fetch_stock_data_em', 'fetch_stock_data_sina', 'fetch_stock_data_tx', 'fetch_etf_data', 'fetch_us_data', 'fetch_hk_data', 'fetch_raw_data', 'fetch_stock_data']

def _is_us_code(stock_code: str) -> bool:
    from ..us_index_mapping import is_us_stock_code

    return is_us_stock_code(stock_code)

def _is_hk_code(stock_code: str) -> bool:
    code = stock_code.strip().lower()
    if code.endswith(".hk"):
        numeric_part = code[:-3]
        return numeric_part.isdigit() and 1 <= len(numeric_part) <= 5
    if code.startswith("hk"):
        numeric_part = code[2:]
        return numeric_part.isdigit() and 1 <= len(numeric_part) <= 5
    return code.isdigit() and len(code) == 5

def _is_etf_code(stock_code: str) -> bool:
    etf_prefixes = ("51", "52", "56", "58", "15", "16", "18")
    code = stock_code.strip().split(".")[0]
    return code.startswith(etf_prefixes) and len(code) == 6

def _to_sina_tx_symbol(stock_code: str) -> str:
    code = stock_code.strip()
    if is_bse_code(code):
        return f"bj{code}"
    if code.startswith(("6", "5", "90")):
        return f"sh{code}"
    return f"sz{code}"

def _normalize_data(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    """标准化 akshare K 线数据列名到英文标准格式。"""
    df = df.copy()
    column_mapping = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=column_mapping)
    df["code"] = stock_code
    keep_cols = ["code"] + STANDARD_COLUMNS
    existing_cols = [col for col in keep_cols if col in df.columns]
    df = df[existing_cols]
    return df

def _is_retryable_kline_error(exc: BaseException) -> bool:
    if isinstance(exc, (RateLimitError, ConnectionError, TimeoutError)):
        return True
    message = str(exc).lower()
    return any(
        kw in message
        for kw in [
            "banned",
            "blocked",
            "频率",
            "rate",
            "限制",
            "timeout",
            "timed out",
            "connection",
            "remote end closed",
            "temporarily",
            "reset by peer",
        ]
    )

def _wait_for_kline_retry(retry_state) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    attempt = retry_state.attempt_number
    if isinstance(exc, RateLimitError):
        return min(30.0, 5.0 * (2 ** (attempt - 1)))
    return min(8.0, 1.0 * (2 ** (attempt - 1)))

def _rate_limited_fetch(fetch_func, *args, **kwargs):
    """Execute a fetch function with rate-limit enforcement.

    The caller passes a fetcher instance (or callable) so each subclass
    can supply its own sleep/rate-limit strategy.
    """
    return fetch_func(*args, **kwargs)


_retry_a_stock_kline = retry(
    stop=stop_after_attempt(3),
    wait=_wait_for_kline_retry,
    retry=retry_if_exception(_is_retryable_kline_error),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

@_retry_a_stock_kline
def fetch_stock_data_em(
    stock_code: str,
    start_date: str,
    end_date: str,
    enforce_rate_limit=None,
    set_user_agent=None,
) -> pd.DataFrame:
    """普通 A 股历史数据（东方财富）。"""
    import akshare as ak

    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()

    logger.info("[API调用] ak.stock_zh_a_hist(symbol=%s, ...)", stock_code)
    try:
        import time as _time

        api_start = _time.time()
        df = ak.stock_zh_a_hist(
            symbol=stock_code,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )
        api_elapsed = _time.time() - api_start
        if df is not None and not df.empty:
            logger.info("[API返回] ak.stock_zh_a_hist 成功: %d 行, 耗时 %.2fs", len(df), api_elapsed)
            if "成交量" in df.columns:
                df["成交量"] = pd.to_numeric(df["成交量"], errors="coerce") * 100
            return df
        return pd.DataFrame()
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ["banned", "blocked", "频率", "rate", "限制"]):
            raise RateLimitError(f"Akshare(EM) 可能被限流: {e}") from e
        raise e

@_retry_a_stock_kline
def fetch_stock_data_sina(
    stock_code: str,
    start_date: str,
    end_date: str,
    enforce_rate_limit=None,
) -> pd.DataFrame:
    """普通 A 股历史数据（新浪财经）。"""
    import akshare as ak

    symbol = _to_sina_tx_symbol(stock_code)
    if enforce_rate_limit:
        enforce_rate_limit()

    try:
        df = ak.stock_zh_a_daily(
            symbol=symbol,
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )
        if df is not None and not df.empty:
            rename_map = {
                "date": "日期",
                "open": "开盘",
                "high": "最高",
                "low": "最低",
                "close": "收盘",
                "volume": "成交量",
                "amount": "成交额",
            }
            df = df.rename(columns=rename_map)
            if "收盘" in df.columns:
                df["涨跌幅"] = df["收盘"].pct_change() * 100
                df["涨跌幅"] = df["涨跌幅"].fillna(0)
            return df
        return pd.DataFrame()
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ["banned", "blocked", "频率", "rate", "限制"]):
            raise RateLimitError(f"Akshare(新浪) 可能被限流: {e}") from e
        raise e

@_retry_a_stock_kline
def fetch_stock_data_tx(
    stock_code: str,
    start_date: str,
    end_date: str,
    enforce_rate_limit=None,
) -> pd.DataFrame:
    """普通 A 股历史数据（腾讯财经）。"""
    import akshare as ak

    symbol = _to_sina_tx_symbol(stock_code)
    if enforce_rate_limit:
        enforce_rate_limit()

    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=symbol,
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )
        if df is not None and not df.empty:
            rename_map = {
                "date": "日期",
                "open": "开盘",
                "high": "最高",
                "low": "最低",
                "close": "收盘",
                "amount": "成交量",
            }
            df = df.rename(columns=rename_map)
            if "成交量" in df.columns:
                df["成交量"] = pd.to_numeric(df["成交量"], errors="coerce") * 100
            if "pct_chg" in df.columns:
                df = df.rename(columns={"pct_chg": "涨跌幅"})
            elif "收盘" in df.columns:
                df["涨跌幅"] = df["收盘"].pct_change() * 100
                df["涨跌幅"] = df["涨跌幅"].fillna(0)
            return df
        return pd.DataFrame()
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ["banned", "blocked", "频率", "rate", "限制"]):
            raise RateLimitError(f"Akshare(腾讯) 可能被限流: {e}") from e
        raise e

def fetch_etf_data(
    stock_code: str,
    start_date: str,
    end_date: str,
    enforce_rate_limit=None,
    set_user_agent=None,
) -> pd.DataFrame:
    """ETF 历史数据（东方财富）。"""
    import akshare as ak

    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()

    logger.info("[API调用] ak.fund_etf_hist_em(symbol=%s, ...)", stock_code)
    try:
        import time as _time

        api_start = _time.time()
        df = ak.fund_etf_hist_em(
            symbol=stock_code,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )
        api_elapsed = _time.time() - api_start
        if df is not None and not df.empty:
            logger.info("[API返回] ak.fund_etf_hist_em 成功: %d 行, 耗时 %.2fs", len(df), api_elapsed)
            if "成交量" in df.columns:
                df["成交量"] = pd.to_numeric(df["成交量"], errors="coerce") * 100
            return df
        return pd.DataFrame()
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ["banned", "blocked", "频率", "rate", "限制"]):
            raise RateLimitError(f"Akshare 可能被限流: {e}") from e
        raise DataFetchError(f"Akshare 获取 ETF 数据失败: {e}") from e

def fetch_us_data(
    stock_code: str,
    start_date: str,
    end_date: str,
    enforce_rate_limit=None,
    set_user_agent=None,
) -> pd.DataFrame:
    """美股历史数据（新浪财经接口）。"""
    import akshare as ak

    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()

    symbol = stock_code.strip().upper()
    logger.info("[API调用] ak.stock_us_daily(symbol=%s, adjust=qfq)", symbol)
    try:
        import time as _time

        api_start = _time.time()
        df = ak.stock_us_daily(symbol=symbol, adjust="qfq")
        api_elapsed = _time.time() - api_start
        if df is not None and not df.empty:
            logger.info("[API返回] ak.stock_us_daily 成功: %d 行, 耗时 %.2fs", len(df), api_elapsed)
            df["date"] = pd.to_datetime(df["date"])
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)
            df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
            rename_map = {
                "date": "日期",
                "open": "开盘",
                "high": "最高",
                "low": "最低",
                "close": "收盘",
                "volume": "成交量",
            }
            df = df.rename(columns=rename_map)
            if "收盘" in df.columns:
                df["涨跌幅"] = df["收盘"].pct_change() * 100
                df["涨跌幅"] = df["涨跌幅"].fillna(0)
            if "成交量" in df.columns and "收盘" in df.columns:
                df["成交额"] = df["成交量"] * df["收盘"]
            else:
                df["成交额"] = 0
            return df
        return pd.DataFrame()
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ["banned", "blocked", "频率", "rate", "限制"]):
            raise RateLimitError(f"Akshare 可能被限流: {e}") from e
        raise DataFetchError(f"Akshare 获取美股数据失败: {e}") from e

def fetch_hk_data(
    stock_code: str,
    start_date: str,
    end_date: str,
    enforce_rate_limit=None,
    set_user_agent=None,
) -> pd.DataFrame:
    """港股历史数据。"""
    import akshare as ak

    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()

    code = stock_code.lower().replace("hk", "").zfill(5)
    logger.info("[API调用] ak.stock_hk_hist(symbol=%s, ...)", code)
    try:
        import time as _time

        api_start = _time.time()
        df = ak.stock_hk_hist(
            symbol=code,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )
        api_elapsed = _time.time() - api_start
        if df is not None and not df.empty:
            logger.info("[API返回] ak.stock_hk_hist 成功: %d 行, 耗时 %.2fs", len(df), api_elapsed)
            return df
        return pd.DataFrame()
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ["banned", "blocked", "频率", "rate", "限制"]):
            raise RateLimitError(f"Akshare 可能被限流: {e}") from e
        raise DataFetchError(f"Akshare 获取港股数据失败: {e}") from e

def fetch_raw_data(
    stock_code: str,
    start_date: str,
    end_date: str,
    enforce_rate_limit=None,
    set_user_agent=None,
) -> pd.DataFrame:
    """根据代码类型自动选择 API 获取原始K线数据。"""
    if _is_us_code(stock_code):
        raise DataFetchError(f"AkshareFetcher 不支持美股 {stock_code}，请使用 YfinanceFetcher 获取正确的复权价格")
    elif _is_hk_code(stock_code):
        return fetch_hk_data(stock_code, start_date, end_date, enforce_rate_limit, set_user_agent)
    elif _is_etf_code(stock_code):
        return fetch_etf_data(stock_code, start_date, end_date, enforce_rate_limit, set_user_agent)
    else:
        return fetch_stock_data(stock_code, start_date, end_date, enforce_rate_limit, set_user_agent)

def fetch_stock_data(
    stock_code: str,
    start_date: str,
    end_date: str,
    enforce_rate_limit=None,
    set_user_agent=None,
) -> pd.DataFrame:
    """普通 A 股历史数据，含多个源故障切换。"""
    methods = [
        (lambda sc, sd, ed: fetch_stock_data_em(sc, sd, ed, enforce_rate_limit, set_user_agent), "东方财富"),
        (lambda sc, sd, ed: fetch_stock_data_sina(sc, sd, ed, enforce_rate_limit), "新浪财经"),
        (lambda sc, sd, ed: fetch_stock_data_tx(sc, sd, ed, enforce_rate_limit), "腾讯财经"),
    ]
    last_error = None
    for fetch_method, source_name in methods:
        try:
            logger.info("[数据源] 尝试使用 %s 获取 %s...", source_name, stock_code)
            df = fetch_method(stock_code, start_date, end_date)
            if df is not None and not df.empty:
                logger.info("[数据源] %s 获取成功", source_name)
                return df
        except Exception as e:
            last_error = e
            logger.warning("[数据源] %s 获取失败: %s", source_name, e)
    raise DataFetchError(f"Akshare 所有渠道获取失败: {last_error}")
