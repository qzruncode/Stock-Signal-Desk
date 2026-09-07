"""Source selection contract; collection and provenance live in the data service."""

from datetime import datetime
from src.services.market_data_client import read_source
from src.tools.symbols import resolve_local_symbol


def _validate_date(value: str, field: str) -> str:
    try:
        return datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{field} 必须是有效的 YYYYMMDD 日期") from exc


def _read_source(
    *,
    symbol,
    source_key,
    start_date,
    end_date,
    requested_count,
    range_mode,
    allow_fallback=True,
):
    arguments = {
        "symbol": resolve_local_symbol(symbol),
        "source_id": source_key,
        "allow_fallback": allow_fallback,
    }
    if range_mode:
        arguments.update(start_date=start_date, end_date=end_date)
    else:
        arguments["count"] = requested_count or 500
    return read_source("kline", arguments)


__all__ = ["_read_source", "_validate_date"]
