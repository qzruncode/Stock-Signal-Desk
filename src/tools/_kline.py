"""Shared daily-bar adapters for technical indicators."""

from datetime import date, datetime
from src.services.market_data_client import read_source
from src.tools._trading_calendar import latest_completed_trade_day

def _expected_latest_kline_date(now=None):
    return latest_completed_trade_day(now)


def _kline_data_time(records):
    return max(
        (str(row["date"])[:10] for row in records if row.get("date")), default=None
    )


def _kline_is_stale(records):
    latest = _kline_data_time(records)
    return not latest or latest < latest_completed_trade_day().isoformat()


def _format_kline_date(value):
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y%m%d")
    return datetime.strptime(str(value).replace("-", "")[:8], "%Y%m%d").strftime(
        "%Y%m%d"
    )


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
