"""Completed-bar source selection over the independent service."""

from src.services.market_data_client import read_source


def read_reliable_kline(
    symbol, *, preferred_source, count, sources=None, allow_fallback=True
):
    return read_source(
        "kline",
        {
            "symbol": symbol,
            "source_id": preferred_source,
            "count": count,
            "allow_fallback": allow_fallback,
        },
    )


def read_reliable_kline_range(
    symbol, *, preferred_source, start_date, end_date, sources=None, allow_fallback=True
):
    return read_source(
        "kline",
        {
            "symbol": symbol,
            "source_id": preferred_source,
            "start_date": start_date,
            "end_date": end_date,
            "allow_fallback": allow_fallback,
        },
    )
