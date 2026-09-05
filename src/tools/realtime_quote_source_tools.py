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

_AUTO_SOURCE_ID = "auto"


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


def _read_auto_source(symbol: str) -> dict[str, Any]:
    """Read a quote through the trading-session-aware multi-source gateway."""
    code = _resolve_a_share_symbol(symbol)
    from src.tools.get_realtime_quotes import get_realtime_quotes

    result = dict(get_realtime_quotes([code]))
    # A successful transport response is not enough: a quote with no source
    # timestamp or a provider-marked stale quote cannot satisfy a current-price
    # request.  Give the completed-bar gateway one explicit opportunity to
    # replace it, while preserving the original observation if that gateway
    # also cannot produce a usable dated value.
    if (
        result.get("success") is not True
        or result.get("freshness_unknown") is True
        or result.get("is_stale") is True
    ):
        result = _fallback_to_completed_close(code, result)
    result.update(
        {
            "symbol": code,
            "source_scope": "realtime_quote_auto",
            "requested_symbols": [code],
            "fallback_used": bool(result.get("fallback_used")),
        }
    )
    result.setdefault("partial", bool(result.get("success") and result.get("errors")))
    result.setdefault("warnings", [])
    result.setdefault("errors", [])
    return result


def _fallback_to_completed_close(symbol: str, failed_result: dict[str, Any]) -> dict[str, Any]:
    """Use the latest completed daily bar when no quote provider has a price."""

    def fallback_unavailable(reason: str) -> dict[str, Any]:
        warnings = [
            str(value)
            for value in list(failed_result.get("warnings") or [])
            if str(value).strip()
        ]
        warnings.append(f"已尝试最近完成交易日收盘快照，但未能替换原行情：{reason}")
        return {
            **failed_result,
            "fallback_attempted": True,
            "fallback_recommended": True,
            "warnings": warnings,
        }

    try:
        from src.tools.kline_gateway import read_reliable_kline
        from src.tools.kline_source_tools import _SOURCES as kline_sources

        kline = read_reliable_kline(
            symbol,
            preferred_source="eastmoney",
            count=20,
            sources=kline_sources,
            allow_fallback=True,
        )
    except Exception as exc:
        return fallback_unavailable(f"{type(exc).__name__}: {exc}")
    records = list(kline.get("data") or [])
    if not kline.get("success") or not records:
        return fallback_unavailable("没有可用的已完成日线")
    latest = records[-1]
    close = latest.get("close")
    try:
        close_value = float(close)
    except (TypeError, ValueError):
        return fallback_unavailable("最近日线收盘价无效")
    if close_value <= 0:
        return fallback_unavailable("最近日线收盘价非正数")
    previous_close = records[-2].get("close") if len(records) > 1 else None
    item: dict[str, Any] = {
        "code": symbol,
        "name": "",
        "source": str(kline.get("source") or "最近完成交易日 K 线收盘快照"),
        "trade_time": str(latest.get("date") or "") or None,
        "price": close_value,
        "quote_mode": "latest_completed_bar",
        "quote_mode_label": "非交易时段的最近完成交易日收盘快照，不是当前时刻实时成交",
        "_cached": bool(kline.get("_cached")),
        "_fetched_at": kline.get("_fetched_at"),
    }
    if previous_close not in (None, ""):
        try:
            previous_value = float(previous_close)
        except (TypeError, ValueError):
            previous_value = None
        if previous_value and previous_value > 0:
            item["pre_close"] = previous_value
            item["change_pct"] = round((close_value / previous_value - 1) * 100, 4)
    for source_key, target_key in (("volume", "volume"), ("amount", "amount"), ("turnover_rate", "turnover_rate"), ("pct_chg", "change_pct")):
        if target_key not in item and latest.get(source_key) not in (None, ""):
            item[target_key] = latest.get(source_key)
    data_time = str(kline.get("data_time") or latest.get("date") or "").strip() or None
    prior_warnings = [str(value) for value in list(failed_result.get("warnings") or []) if str(value).strip()]
    prior_errors = [str(value) for value in list(failed_result.get("errors") or []) if str(value).strip()]
    return {
        **failed_result,
        "success": True,
        "partial": False,
        "items": [item],
        "total": 1,
        "data_time": data_time,
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": (
            f"实时行情未返回有效报价，已降级为最近完成交易日 {data_time} 的收盘价；不是当前时刻实时成交。"
            if data_time
            else "实时行情未返回有效报价，且最近完成交易日收盘数据没有有效日期。"
        ),
        "is_stale": kline.get("is_stale") if data_time else None,
        "freshness_unknown": data_time is None,
        "quote_mode": "latest_completed_bar",
        "quote_mode_label": "非交易时段的最近完成交易日收盘快照，不是当前时刻实时成交",
        "fallback_used": True,
        "fallback_attempted": True,
        "fallback_recommended": False,
        "source": item["source"],
        "source_scope": "completed_daily_close_snapshot",
        "source_attempts": list(kline.get("source_attempts") or []),
        "missing_symbols": [],
        "errors": [],
        "warnings": [
            *prior_warnings,
            *(prior_errors[:3]),
            "实时行情 provider 未返回有效报价，已使用最近完成交易日收盘快照。",
            *[str(value) for value in list(kline.get("warnings") or []) if str(value).strip()],
        ],
        "_cached": bool(kline.get("_cached")),
        "_fetched_at": kline.get("_fetched_at"),
    }


__all__ = ["_read_source", "_read_auto_source"]
