"""Daily-bar API façade. The data service is the only market-data owner."""

from datetime import date, datetime
from src.services.market_data_client import get_market_data_client, read_source
from src.tools._trading_calendar import latest_completed_trade_day
from src.tools.symbols import resolve_local_symbol
from src.tools.kline_normalization import (
    _normalize_kline_df,
    _normalize_record_list,
    _normalize_record_units,
)

DEFAULT_COUNT = 500
KLINE_SOURCE_EM, KLINE_SOURCE_SINA, KLINE_SOURCE_TENCENT = (
    "eastmoney",
    "sina",
    "tencent",
)
KLINE_DESCRIPTION = (
    "通过独立数据服务获取前复权日线，成交量为股；返回来源、数据时间和版本。"
)
KLINE_HISTORY_DESCRIPTION = "获取指定日期范围的前复权日线和可追溯数据版本。"


def _expected_latest_kline_date(now=None):
    return latest_completed_trade_day(now)


def _kline_data_time(records):
    return max(
        (str(row["date"])[:10] for row in records if row.get("date")), default=None
    )


def _kline_is_stale(records):
    latest = _kline_data_time(records)
    return not latest or latest < latest_completed_trade_day().isoformat()


def get_kline(symbol, count=DEFAULT_COUNT, use_cache=True):
    return read_source(
        "kline",
        {
            "symbol": resolve_local_symbol(symbol),
            "count": max(1, min(int(count), 5000)),
        },
    )


def get_history_data(symbol, start_date, end_date, use_cache=True):
    return read_source(
        "kline",
        {
            "symbol": resolve_local_symbol(symbol),
            "start_date": _format_kline_date(start_date),
            "end_date": _format_kline_date(end_date),
        },
    )


def _format_kline_date(value):
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y%m%d")
    return datetime.strptime(str(value).replace("-", "")[:8], "%Y%m%d").strftime(
        "%Y%m%d"
    )


def fetch_and_persist_kline(
    symbol, count=DEFAULT_COUNT, start_date=None, end_date=None, use_cache=True
):
    """Compatibility contract: persistence takes place exclusively in the service."""
    result = (
        get_history_data(symbol, start_date, end_date)
        if start_date is not None and end_date is not None
        else get_kline(symbol, count)
    )
    return result["data"], result["source"]


def _fetch_kline_with_fallback(symbol, start_date, end_date):
    result = get_history_data(symbol, start_date, end_date)
    return result["data"], result["source"]


def _get_kline_from_stock_daily(symbol, count):
    """Legacy helper name, backed by the authoritative HTTP snapshot API."""
    return get_market_data_client().snapshot([symbol], ["kline"], count=count)["items"][
        symbol
    ]["kline"]


def _read_frame(symbol, start_date, end_date, source):
    import pandas as pd

    result = read_source(
        "kline",
        {
            "symbol": symbol,
            "start_date": _format_kline_date(start_date),
            "end_date": _format_kline_date(end_date),
            "source_id": source,
            "allow_fallback": False,
        },
    )
    return pd.DataFrame(result["data"])


def _fetch_kline_em(symbol, start_date, end_date):
    return _read_frame(symbol, start_date, end_date, "eastmoney")


def _fetch_kline_sina(symbol, start_date, end_date):
    return _read_frame(symbol, start_date, end_date, "sina")


def _fetch_kline_tencent(symbol, start_date, end_date):
    return _read_frame(symbol, start_date, end_date, "tencent")
