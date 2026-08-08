"""Internal one-provider A-share quote adapter.

Only ``source_operations.read_realtime_quote`` is model-callable.  This
module deliberately contains source I/O and normalization only, so a provider
cannot quietly become a second tool-registration surface.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Any, Callable

from data_provider.fetchers import realtime as realtime_fetchers

from src.tools._trading_calendar import (
    expected_trade_day,
    is_trading_time,
    trade_dates,
)
from src.tools.symbols import resolve_local_symbol


_SourceFetcher = Callable[[str], Any]

_SOURCES: dict[str, tuple[str, str, _SourceFetcher]] = {
    "eastmoney_push": (
        "东方财富 Push 实时行情",
        "eastmoney_push_quote",
        realtime_fetchers._get_stock_realtime_quote_em_push,
    ),
    "sina": (
        "新浪财经实时行情",
        "sina_quote",
        realtime_fetchers._get_stock_realtime_quote_sina,
    ),
    "tencent": (
        "腾讯财经实时行情",
        "tencent_quote",
        realtime_fetchers._get_stock_realtime_quote_tencent,
    ),
    "xueqiu": (
        "雪球实时行情",
        "xueqiu_quote",
        realtime_fetchers._get_stock_realtime_quote_xueqiu,
    ),
}


def _resolve_a_share_symbol(symbol: str) -> str:
    code = resolve_local_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"无法识别 A 股证券代码或名称: {symbol}")
    return code


def _last_trading_day(now: datetime) -> datetime:
    try:
        day = expected_trade_day(now, trade_dates())
    except Exception:
        day = now.date()
        if day.weekday() >= 5 or now.time() < time(9, 15):
            day -= timedelta(days=1)
            while day.weekday() >= 5:
                day -= timedelta(days=1)
    return datetime.combine(day, time(0, 0), tzinfo=now.tzinfo)


def _quote_staleness(data_time: str | None, *, now: datetime) -> bool | None:
    if not data_time:
        return None
    try:
        observed = datetime.fromisoformat(str(data_time).replace("Z", "+00:00"))
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=now.tzinfo)
    expected_day = _last_trading_day(now).date()
    if observed.date() < expected_day:
        return True
    if is_trading_time(now) and observed.date() == expected_day:
        return (now - observed).total_seconds() > 15 * 60
    return False


def _read_source(symbol: str, source_key: str) -> dict[str, Any]:
    code = _resolve_a_share_symbol(symbol)
    source_label, source_scope, fetcher = _SOURCES[source_key]
    now = datetime.now().astimezone()
    try:
        quote = fetcher(code)
    except Exception as exc:
        quote = None
        failure = f"{type(exc).__name__}: {exc}"
    else:
        failure = ""
    if quote is None or not quote.has_basic_data():
        return {
            "success": False,
            "partial": False,
            "symbol": code,
            "items": [],
            "total": 0,
            "source": source_label,
            "source_scope": source_scope,
            "requested_symbols": [code],
            "data_time": None,
            "data_time_provenance": "unavailable",
            "data_time_note": (
                f"{source_label}未返回有效报价时间；_fetched_at 仅表示本服务获取时间。"
            ),
            "is_stale": None,
            "freshness_unknown": True,
            "fallback_used": False,
            "errors": [failure or f"{source_label}未返回有效报价"],
            "warnings": [],
            "_fetched_at": now.isoformat(),
        }

    item = quote.to_dict()
    item["_cached"] = False
    item["_fetched_at"] = now.isoformat()
    data_time = str(item.get("trade_time") or "").strip() or None
    is_stale = _quote_staleness(data_time, now=now)
    return {
        "success": True,
        "partial": False,
        "symbol": code,
        "items": [item],
        "total": 1,
        "source": source_label,
        "source_scope": source_scope,
        "requested_symbols": [code],
        "data_time": data_time,
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": (
            None
            if data_time
            else f"{source_label}未返回 trade_time；_fetched_at 仅表示本服务获取时间。"
        ),
        "is_stale": is_stale,
        "freshness_unknown": data_time is None,
        "fallback_used": False,
        "volume_unit": "股",
        "amount_unit": "元",
        "market_value_unit": "元",
        "errors": [],
        "warnings": [],
        "_cached": False,
        "_fetched_at": now.isoformat(),
    }


__all__ = ["_read_source"]
