# -*- coding: utf-8 -*-
"""History K-line data fetchers — A-share (EM/Sina/Tencent), ETF, HK, US."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log,
)

from ..utils import DataFetchError, RateLimitError, STANDARD_COLUMNS, is_bse_code, is_st_stock, is_kc_cy_stock, normalize_stock_code
from ..constants import USER_AGENTS

logger = logging.getLogger(__name__)


def _is_us_code(stock_code: str) -> bool:
    from ..us_index_mapping import is_us_stock_code
    return is_us_stock_code(stock_code)


def _is_hk_code(stock_code: str) -> bool:
    code = stock_code.strip().lower()
    if code.endswith('.hk'):
        numeric_part = code[:-3]
        return numeric_part.isdigit() and 1 <= len(numeric_part) <= 5
    if code.startswith('hk'):
        numeric_part = code[2:]
        return numeric_part.isdigit() and 1 <= len(numeric_part) <= 5
    return code.isdigit() and len(code) == 5


def _is_etf_code(stock_code: str) -> bool:
    etf_prefixes = ('51', '52', '56', '58', '15', '16', '18')
    code = stock_code.strip().split('.')[0]
    return code.startswith(etf_prefixes) and len(code) == 6


def _to_sina_tx_symbol(stock_code: str) -> str:
    code = stock_code.strip()
    if code.startswith(('6', '5', '90')):
        return f"sh{code}"
    elif code.startswith(('8', '4', '9')):
        return f"bj{code}"
    else:
        return f"sz{code}"


def _normalize_data(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    """标准化 akshare K 线数据列名到英文标准格式。"""
    df = df.copy()
    column_mapping = {
        '日期': 'date', '开盘': 'open', '收盘': 'close',
        '最高': 'high', '最低': 'low', '成交量': 'volume',
        '成交额': 'amount', '涨跌幅': 'pct_chg',
    }
    df = df.rename(columns=column_mapping)
    df['code'] = stock_code
    keep_cols = ['code'] + STANDARD_COLUMNS
    existing_cols = [col for col in keep_cols if col in df.columns]
    df = df[existing_cols]
    return df


# ── Retry wrapper ────────────────────────────────────────────────────────


def _rate_limited_fetch(fetch_func, *args, **kwargs):
    """Execute a fetch function with rate-limit enforcement.

    The caller passes a fetcher instance (or callable) so each subclass
    can supply its own sleep/rate-limit strategy.
    """
    return fetch_func(*args, **kwargs)


# ── Kline fetcher (standalone functions taking a fetcher callback) ───────
# These are designed to be called from AkshareFetcher methods which provide
# _enforce_rate_limit() and _set_random_user_agent().


def fetch_stock_data_em(
    stock_code: str, start_date: str, end_date: str,
    enforce_rate_limit=None, set_user_agent=None,
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
            start_date=start_date.replace('-', ''),
            end_date=end_date.replace('-', ''),
            adjust="qfq",
        )
        api_elapsed = _time.time() - api_start
        if df is not None and not df.empty:
            logger.info("[API返回] ak.stock_zh_a_hist 成功: %d 行, 耗时 %.2fs", len(df), api_elapsed)
            return df
        return pd.DataFrame()
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ['banned', 'blocked', '频率', 'rate', '限制']):
            raise RateLimitError(f"Akshare(EM) 可能被限流: {e}") from e
        raise e


def fetch_stock_data_sina(
    stock_code: str, start_date: str, end_date: str,
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
            start_date=start_date.replace('-', ''),
            end_date=end_date.replace('-', ''),
            adjust="qfq",
        )
        if df is not None and not df.empty:
            rename_map = {
                'date': '日期', 'open': '开盘', 'high': '最高',
                'low': '最低', 'close': '收盘', 'volume': '成交量', 'amount': '成交额',
            }
            df = df.rename(columns=rename_map)
            if '收盘' in df.columns:
                df['涨跌幅'] = df['收盘'].pct_change() * 100
                df['涨跌幅'] = df['涨跌幅'].fillna(0)
            return df
        return pd.DataFrame()
    except Exception as e:
        raise e


def fetch_stock_data_tx(
    stock_code: str, start_date: str, end_date: str,
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
            start_date=start_date.replace('-', ''),
            end_date=end_date.replace('-', ''),
            adjust="qfq",
        )
        if df is not None and not df.empty:
            rename_map = {
                'date': '日期', 'open': '开盘', 'high': '最高',
                'low': '最低', 'close': '收盘', 'volume': '成交量', 'amount': '成交额',
            }
            df = df.rename(columns=rename_map)
            if 'pct_chg' in df.columns:
                df = df.rename(columns={'pct_chg': '涨跌幅'})
            elif '收盘' in df.columns:
                df['涨跌幅'] = df['收盘'].pct_change() * 100
                df['涨跌幅'] = df['涨跌幅'].fillna(0)
            return df
        return pd.DataFrame()
    except Exception as e:
        raise e


def fetch_etf_data(
    stock_code: str, start_date: str, end_date: str,
    enforce_rate_limit=None, set_user_agent=None,
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
            symbol=stock_code, period="daily",
            start_date=start_date.replace('-', ''),
            end_date=end_date.replace('-', ''),
            adjust="qfq",
        )
        api_elapsed = _time.time() - api_start
        if df is not None and not df.empty:
            logger.info("[API返回] ak.fund_etf_hist_em 成功: %d 行, 耗时 %.2fs", len(df), api_elapsed)
            return df
        return pd.DataFrame()
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ['banned', 'blocked', '频率', 'rate', '限制']):
            raise RateLimitError(f"Akshare 可能被限流: {e}") from e
        raise DataFetchError(f"Akshare 获取 ETF 数据失败: {e}") from e


def fetch_us_data(
    stock_code: str, start_date: str, end_date: str,
    enforce_rate_limit=None, set_user_agent=None,
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
            df['date'] = pd.to_datetime(df['date'])
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)
            df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]
            rename_map = {
                'date': '日期', 'open': '开盘', 'high': '最高',
                'low': '最低', 'close': '收盘', 'volume': '成交量',
            }
            df = df.rename(columns=rename_map)
            if '收盘' in df.columns:
                df['涨跌幅'] = df['收盘'].pct_change() * 100
                df['涨跌幅'] = df['涨跌幅'].fillna(0)
            if '成交量' in df.columns and '收盘' in df.columns:
                df['成交额'] = df['成交量'] * df['收盘']
            else:
                df['成交额'] = 0
            return df
        return pd.DataFrame()
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ['banned', 'blocked', '频率', 'rate', '限制']):
            raise RateLimitError(f"Akshare 可能被限流: {e}") from e
        raise DataFetchError(f"Akshare 获取美股数据失败: {e}") from e


def fetch_hk_data(
    stock_code: str, start_date: str, end_date: str,
    enforce_rate_limit=None, set_user_agent=None,
) -> pd.DataFrame:
    """港股历史数据。"""
    import akshare as ak

    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()

    code = stock_code.lower().replace('hk', '').zfill(5)
    logger.info("[API调用] ak.stock_hk_hist(symbol=%s, ...)", code)
    try:
        import time as _time
        api_start = _time.time()
        df = ak.stock_hk_hist(
            symbol=code, period="daily",
            start_date=start_date.replace('-', ''),
            end_date=end_date.replace('-', ''),
            adjust="qfq",
        )
        api_elapsed = _time.time() - api_start
        if df is not None and not df.empty:
            logger.info("[API返回] ak.stock_hk_hist 成功: %d 行, 耗时 %.2fs", len(df), api_elapsed)
            return df
        return pd.DataFrame()
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ['banned', 'blocked', '频率', 'rate', '限制']):
            raise RateLimitError(f"Akshare 可能被限流: {e}") from e
        raise DataFetchError(f"Akshare 获取港股数据失败: {e}") from e


def fetch_raw_data(
    stock_code: str, start_date: str, end_date: str,
    enforce_rate_limit=None, set_user_agent=None,
) -> pd.DataFrame:
    """根据代码类型自动选择 API 获取原始K线数据。"""
    if _is_us_code(stock_code):
        raise DataFetchError(
            f"AkshareFetcher 不支持美股 {stock_code}，请使用 YfinanceFetcher 获取正确的复权价格"
        )
    elif _is_hk_code(stock_code):
        return fetch_hk_data(stock_code, start_date, end_date, enforce_rate_limit, set_user_agent)
    elif _is_etf_code(stock_code):
        return fetch_etf_data(stock_code, start_date, end_date, enforce_rate_limit, set_user_agent)
    else:
        return fetch_stock_data(stock_code, start_date, end_date, enforce_rate_limit, set_user_agent)


def fetch_stock_data(
    stock_code: str, start_date: str, end_date: str,
    enforce_rate_limit=None, set_user_agent=None,
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


# ── Convenience helpers for AkshareFetcher ──────────────────────────────


def fetch_stock_kline_history(
    code: str, days: int = 365,
    enforce_rate_limit=None,
) -> Optional[pd.DataFrame]:
    """获取单只股票近 N 天日线历史（前复权）。"""
    from datetime import timedelta
    import akshare as ak

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

    if enforce_rate_limit:
        enforce_rate_limit()
    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq",
        )
        if df is not None and not df.empty:
            col_map = {
                '日期': 'date', '开盘': 'open', '收盘': 'close',
                '最高': 'high', '最低': 'low', '成交量': 'volume',
                '成交额': 'amount', '涨跌幅': 'pct_chg',
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            keep_cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
            df = df[[c for c in keep_cols if c in df.columns]]
            df = df.tail(days)
            return df
    except Exception as e:
        logger.debug("[K线历史] %s 东财失败: %s", code, e)

    from ..utils import normalize_stock_code
    norm = normalize_stock_code(code)
    pref = 'sh' if norm.startswith(('6', '5', '90')) else 'bj' if norm.startswith(('8', '4', '9')) else 'sz'
    symbol = f"{pref}{norm}"
    if enforce_rate_limit:
        enforce_rate_limit()
    try:
        df = ak.stock_zh_a_daily(
            symbol=symbol, start_date=start_date, end_date=end_date, adjust="qfq",
        )
        if df is not None and not df.empty:
            keep = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount']
            df = df[[c for c in keep if c in df.columns]]
            if 'close' in df.columns and 'pct_chg' not in df.columns:
                df['pct_chg'] = df['close'].pct_change() * 100
            df = df.tail(days)
            return df
    except Exception as e:
        logger.debug("[K线历史] %s 新浪失败: %s", code, e)

    if enforce_rate_limit:
        enforce_rate_limit()
    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=symbol, start_date=start_date, end_date=end_date, adjust="qfq",
        )
        if df is not None and not df.empty:
            keep = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount']
            df = df[[c for c in keep if c in df.columns]]
            if 'close' in df.columns and 'pct_chg' not in df.columns:
                df['pct_chg'] = df['close'].pct_change() * 100
            df = df.tail(days)
            return df
    except Exception as e:
        logger.debug("[K线历史] %s 腾讯失败: %s", code, e)

    return None


def get_main_indices(region: str = "cn") -> Optional[List[Dict[str, Any]]]:
    """主要指数实时行情（新浪接口）。"""
    if region != "cn":
        return None
    import akshare as ak

    indices_map = {
        'sh000001': '上证指数', 'sz399001': '深证成指', 'sz399006': '创业板指',
        'sh000688': '科创50', 'sh000016': '上证50', 'sh000300': '沪深300',
    }

    try:
        df = ak.stock_zh_index_spot_sina()
        results = []
        if df is not None and not df.empty:
            for code, name in indices_map.items():
                row = df[df['代码'] == code]
                if row.empty:
                    row = df[df['代码'].str.contains(code)]
                if not row.empty:
                    row = row.iloc[0]
                    prev_close = safe_float(row.get('昨收', 0))
                    high = safe_float(row.get('最高', 0))
                    low = safe_float(row.get('最低', 0))
                    amplitude = (high - low) / prev_close * 100 if prev_close > 0 else 0.0
                    results.append({
                        'code': code, 'name': name,
                        'current': safe_float(row.get('最新价', 0)),
                        'change': safe_float(row.get('涨跌额', 0)),
                        'change_pct': safe_float(row.get('涨跌幅', 0)),
                        'open': safe_float(row.get('今开', 0)),
                        'high': high, 'low': low,
                        'prev_close': prev_close,
                        'volume': safe_float(row.get('成交量', 0)),
                        'amount': safe_float(row.get('成交额', 0)),
                        'amplitude': amplitude,
                    })
        return results
    except Exception as e:
        logger.error("[Akshare] 获取指数行情失败: %s", e)
        return None


def safe_float(value) -> Optional[float]:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value) -> int:
    try:
        if pd.isna(value):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0