"""Function group 2 extracted from data_provider/fetchers/kline.py."""

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

__all__ = ['fetch_stock_kline_history', 'get_main_indices', 'safe_float', 'safe_int']

def fetch_stock_kline_history(
    code: str,
    days: int = 365,
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
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        if df is not None and not df.empty:
            col_map = {
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "涨跌幅": "pct_chg",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce") * 100
            keep_cols = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
            df = df[[c for c in keep_cols if c in df.columns]]
            df = df.tail(days)
            return df
    except Exception as e:
        logger.debug("[K线历史] %s 东财失败: %s", code, e)

    from ..utils import normalize_stock_code

    norm = normalize_stock_code(code)
    pref = "sh" if norm.startswith(("6", "5", "90")) else "bj" if norm.startswith(("8", "4", "9")) else "sz"
    symbol = f"{pref}{norm}"
    if enforce_rate_limit:
        enforce_rate_limit()
    try:
        df = ak.stock_zh_a_daily(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        if df is not None and not df.empty:
            keep = ["date", "open", "close", "high", "low", "volume", "amount"]
            df = df[[c for c in keep if c in df.columns]]
            if "close" in df.columns and "pct_chg" not in df.columns:
                df["pct_chg"] = df["close"].pct_change() * 100
            df = df.tail(days)
            return df
    except Exception as e:
        logger.debug("[K线历史] %s 新浪失败: %s", code, e)

    if enforce_rate_limit:
        enforce_rate_limit()
    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        if df is not None and not df.empty:
            keep = ["date", "open", "close", "high", "low", "volume", "amount"]
            df = df[[c for c in keep if c in df.columns]]
            if "close" in df.columns and "pct_chg" not in df.columns:
                df["pct_chg"] = df["close"].pct_change() * 100
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
        "sh000001": "上证指数",
        "sz399001": "深证成指",
        "sz399006": "创业板指",
        "sh000688": "科创50",
        "sh000016": "上证50",
        "sh000300": "沪深300",
    }

    try:
        df = ak.stock_zh_index_spot_sina()
        results = []
        if df is not None and not df.empty:
            for code, name in indices_map.items():
                row = df[df["代码"] == code]
                if row.empty:
                    row = df[df["代码"].str.contains(code)]
                if not row.empty:
                    row = row.iloc[0]
                    prev_close = safe_float(row.get("昨收", 0))
                    high = safe_float(row.get("最高", 0))
                    low = safe_float(row.get("最低", 0))
                    amplitude = (high - low) / prev_close * 100 if prev_close > 0 else 0.0
                    results.append(
                        {
                            "code": code,
                            "name": name,
                            "current": safe_float(row.get("最新价", 0)),
                            "change": safe_float(row.get("涨跌额", 0)),
                            "change_pct": safe_float(row.get("涨跌幅", 0)),
                            "open": safe_float(row.get("今开", 0)),
                            "high": high,
                            "low": low,
                            "prev_close": prev_close,
                            "volume": safe_float(row.get("成交量", 0)),
                            "amount": safe_float(row.get("成交额", 0)),
                            "amplitude": amplitude,
                        }
                    )
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
