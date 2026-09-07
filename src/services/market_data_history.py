"""Dated, immutable price observations for research lifecycle evaluation."""

from datetime import date, timedelta
from pydantic import BaseModel, TypeAdapter
from src.services.market_data_client import MarketDataError, read_source


class DailyBar(BaseModel):
    date: date
    close: float
    high: float | None = None
    low: float | None = None
    data_source: str = "market-data-service"


def history(symbol, *, start: date, end: date):
    result = read_source(
        "kline",
        {
            "symbol": symbol,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        wait=0,
    )
    version = (result.get("data_service") or {}).get("version", "")
    rows = [
        {
            **row,
            "data_source": str(result.get("source") or "market-data-service")
            + "@"
            + version[:12],
        }
        for row in result.get("data", [])
        if start.isoformat() <= str(row.get("date"))[:10] <= end.isoformat()
        and (row.get("close") or 0) > 0
    ]
    return TypeAdapter(list[DailyBar]).validate_python(rows)


def baseline(symbol, as_of: date):
    try:
        rows = history(
            symbol, start=as_of - timedelta(days=1098), end=as_of - timedelta(days=1)
        )
        return rows[-1] if rows else None
    except MarketDataError:
        # Register research even if price collection is pending; never invent a
        # baseline. The lifecycle sweeper will retry the exact historical window.
        return None


def outcome_bars(symbol, baseline_date: date, today: date, limit: int):
    try:
        return history(
            symbol,
            start=baseline_date + timedelta(days=1),
            end=today - timedelta(days=1),
        )[:limit]
    except MarketDataError:
        return []
