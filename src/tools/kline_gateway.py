"""Reliable, run-safe acquisition of completed daily bars.

The source operation layer still exposes a preferred ``source_id`` for audit
and debugging.  This gateway is the resilient path used by recent-bar and
technical-indicator reads: it reuses a completed-bar cache, tries the
preferred provider first, and records every provider attempt when fallback is
needed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any, Callable, Mapping

from src.tools._kline import (
    _fetch_kline_em,
    _fetch_kline_sina,
    _fetch_kline_tencent,
    _get_kline_from_cache,
    _get_kline_from_stock_daily,
    _is_beijing_exchange,
    _kline_data_time,
    _latest_kline_cache_key,
    _normalize_kline_df,
    _normalize_record_list,
    _save_kline_to_cache,
    _save_to_stock_daily,
)
from src.tools._trading_calendar import latest_completed_trade_day

logger = logging.getLogger(__name__)

SourceFetcher = Callable[[str, str, str], Any]
SourceCatalog = Mapping[str, tuple[str, SourceFetcher]]

_DEFAULT_SOURCES: dict[str, tuple[str, SourceFetcher]] = {
    "eastmoney": ("东方财富日线（AKShare）", _fetch_kline_em),
    "sina": ("新浪财经日线（AKShare）", _fetch_kline_sina),
    "tencent": ("腾讯财经日线（AKShare）", _fetch_kline_tencent),
}
_CACHE_SOURCE_LABEL = "本地 K 线缓存"
_STOCK_DAILY_SOURCE_LABEL = "本地 StockDaily"
_REQUIRED_COLUMNS = ("open", "high", "low", "close")


def _record_date(record: Mapping[str, Any]) -> date | None:
    value = str(record.get("date") or "")[:10]
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _completed_records(records: list[dict[str, Any]], as_of: date) -> list[dict[str, Any]]:
    filtered = [record for record in records if _record_date(record) is not None and _record_date(record) <= as_of]
    filtered.sort(key=lambda record: str(record.get("date") or ""))
    return filtered


def _normalize_cached_records(data: Any, source: str | None, as_of: date) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    try:
        return _completed_records(_normalize_record_list(data, source), as_of)
    except Exception:
        logger.debug("[K线网关] 缓存数据标准化失败", exc_info=True)
        return []


def _trim(records: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    return records[-count:] if len(records) > count else records


def _has_ohlc(records: list[dict[str, Any]]) -> bool:
    return bool(records) and all(any(record.get(column) is not None for record in records) for column in _REQUIRED_COLUMNS)


def _latest_date(records: list[dict[str, Any]]) -> date | None:
    return _record_date(records[-1]) if records else None


def _persisted_source(records: list[dict[str, Any]], catalog: SourceCatalog) -> str | None:
    for record in reversed(records):
        value = str(record.get("data_source") or "")
        for source_key in catalog:
            if value.startswith(f"{source_key}_"):
                return source_key
    return None


def _source_order(preferred_source: str, symbol: str, sources: SourceCatalog) -> list[str]:
    ordered: list[str] = []
    for source_key in (preferred_source, "eastmoney", "tencent", "sina"):
        if source_key in sources and source_key not in ordered:
            ordered.append(source_key)
    if _is_beijing_exchange(symbol):
        ordered = [source_key for source_key in ordered if source_key != "eastmoney"]
    return ordered


def _result(
    *,
    symbol: str,
    count: int,
    preferred_source: str,
    source_key: str | None,
    source_label: str,
    records: list[dict[str, Any]],
    as_of: date,
    attempts: list[dict[str, Any]],
    warnings: list[str],
    errors: list[str],
    cached: bool,
    partial: bool = False,
    source_origin: str | None = None,
) -> dict[str, Any]:
    actual_source = source_key or "unavailable"
    data_time = _kline_data_time(records)
    fallback_used = actual_source not in {preferred_source, "stock_daily", "cache", "unavailable"}
    if actual_source == "stock_daily":
        fallback_used = False
    return {
        "success": bool(records),
        "partial": bool(partial),
        "symbol": symbol,
        "source": source_label,
        "source_key": actual_source,
        "source_origin": source_origin or (actual_source if actual_source not in {"stock_daily", "cache"} else None),
        "source_scope": f"{actual_source}_daily_qfq_kline",
        "count": len(records),
        "data": records,
        "data_time": data_time,
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": None if data_time else "来源未返回有效日线日期。",
        "is_stale": bool(data_time and str(data_time)[:10] < as_of.isoformat()),
        "freshness_unknown": data_time is None,
        "fallback_used": fallback_used,
        "fallback_provider": actual_source if fallback_used else None,
        "adjust": "qfq",
        "period": "daily",
        "volume_unit": "股",
        "amount_unit": "元",
        "bar_complete": bool(data_time and str(data_time)[:10] <= as_of.isoformat()),
        "requested_end_date": as_of.strftime("%Y%m%d"),
        "source_attempts": attempts,
        "errors": errors,
        "warnings": warnings,
        "_cached": cached,
        "_fetched_at": datetime.now().astimezone().isoformat(),
    }


def read_reliable_kline(
    symbol: str,
    *,
    preferred_source: str,
    count: int,
    sources: SourceCatalog | None = None,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    """Read completed daily bars with cache and provider fallback."""
    catalog = sources or _DEFAULT_SOURCES
    if preferred_source not in catalog:
        raise ValueError(f"未知日线来源: {preferred_source}")

    bounded_count = max(20, min(int(count), 500))
    as_of = latest_completed_trade_day()
    cache_key = _latest_kline_cache_key(symbol, bounded_count)
    stale_candidate: tuple[list[dict[str, Any]], str, str] | None = None

    def consider_candidate(
        records: list[dict[str, Any]],
        source_key: str,
        source_label: str,
        *,
        cached: bool,
    ) -> dict[str, Any] | None:
        nonlocal stale_candidate
        records = _trim(_completed_records(records, as_of), bounded_count)
        if not records or not _has_ohlc(records):
            return None
        latest = _latest_date(records)
        if latest == as_of and len(records) >= bounded_count:
            warning_list = []
            origin = source_key if source_key != "stock_daily" else _persisted_source(records, catalog)
            if source_key == "stock_daily" and origin:
                warning_list.append(f"已复用来自{catalog[origin][0]}的本地日线缓存。")
            if "amount" not in records[-1] or any(record.get("amount") is None for record in records):
                warning_list.append("当前数据源未提供完整成交额字段，技术指标仍可正常计算。")
            return _result(
                symbol=symbol,
                count=bounded_count,
                preferred_source=preferred_source,
                source_key=source_key,
                source_label=source_label,
                source_origin=origin,
                records=records,
                as_of=as_of,
                attempts=[],
                warnings=warning_list,
                errors=[],
                cached=cached,
            )
        if latest is not None and latest < as_of and (stale_candidate is None or latest > _latest_date(stale_candidate[0])):
            stale_candidate = (records, source_key, source_label)
        return None

    if allow_fallback:
        try:
            local = _get_kline_from_stock_daily(symbol, bounded_count + 1)
        except Exception:
            local = None
        fresh = consider_candidate(local or [], "stock_daily", _STOCK_DAILY_SOURCE_LABEL, cached=True)
        if fresh is not None:
            return fresh

        try:
            cached_payload = _get_kline_from_cache(cache_key)
        except Exception:
            cached_payload = None
        if isinstance(cached_payload, dict):
            cached_source = str(cached_payload.get("source") or "cache")
            cached_label = dict(catalog).get(cached_source, (_CACHE_SOURCE_LABEL, None))[0]
            cached_records = _normalize_cached_records(cached_payload.get("data"), cached_source, as_of)
            fresh = consider_candidate(cached_records, cached_source, cached_label, cached=True)
            if fresh is not None:
                return fresh

    if not allow_fallback:
        label, fetcher = catalog[preferred_source]
        try:
            frame = fetcher(
                symbol,
                (as_of - timedelta(days=max(160, int(bounded_count * 1.7) + 45))).strftime("%Y%m%d"),
                as_of.strftime("%Y%m%d"),
            )
            records = _trim(_completed_records(_normalize_kline_df(frame, symbol, preferred_source), as_of), bounded_count)
            return _result(
                symbol=symbol,
                count=bounded_count,
                preferred_source=preferred_source,
                source_key=preferred_source,
                source_label=label,
                records=records,
                as_of=as_of,
                attempts=[],
                warnings=[],
                errors=[] if records else [f"{label}未返回 K 线数据"],
                cached=False,
                partial=len(records) < bounded_count,
            )
        except Exception as exc:
            return _result(
                symbol=symbol,
                count=bounded_count,
                preferred_source=preferred_source,
                source_key=None,
                source_label=label,
                records=[],
                as_of=as_of,
                attempts=[{"source": preferred_source, "label": label, "error_type": type(exc).__name__, "error": str(exc)}],
                warnings=[],
                errors=[f"{type(exc).__name__}: {exc}"],
                cached=False,
            )

    attempts: list[dict[str, Any]] = []
    for source_key in _source_order(preferred_source, symbol, catalog):
        label, fetcher = catalog[source_key]
        try:
            frame = fetcher(
                symbol,
                (as_of - timedelta(days=max(160, int(bounded_count * 1.7) + 45))).strftime("%Y%m%d"),
                as_of.strftime("%Y%m%d"),
            )
            records = _trim(_completed_records(_normalize_kline_df(frame, symbol, source_key), as_of), bounded_count)
            if records and _has_ohlc(records):
                attempts.append(
                    {
                        "source": source_key,
                        "label": label,
                        "status": "success",
                        "count": len(records),
                    }
                )
                warnings = []
                if source_key != preferred_source:
                    warnings.append(f"{catalog[preferred_source][0]}不可用，已切换到{label}。")
                if "amount" not in records[-1] or any(record.get("amount") is None for record in records):
                    warnings.append(f"{label}未提供完整成交额字段，技术指标仍可正常计算。")
                _save_kline_to_cache(cache_key, symbol, records, source_key)
                _save_to_stock_daily(symbol, records, source=source_key)
                return _result(
                    symbol=symbol,
                    count=bounded_count,
                    preferred_source=preferred_source,
                    source_key=source_key,
                    source_label=label,
                    source_origin=source_key,
                    records=records,
                    as_of=as_of,
                    attempts=attempts,
                    warnings=warnings,
                    errors=[],
                    cached=False,
                    partial=len(records) < bounded_count,
                )
            error = "empty_data"
        except Exception as exc:
            error = str(exc)
            attempts.append(
                {
                    "source": source_key,
                    "label": label,
                    "error_type": type(exc).__name__,
                    "error": error,
                }
            )
            continue
        attempts.append(
            {
                "source": source_key,
                "label": label,
                "error_type": "EmptyData",
                "error": error,
            }
        )

    if stale_candidate is not None:
        records, source_key, source_label = stale_candidate
        warnings = [
            "实时数据源均不可用，已使用过期日线缓存；指标结果仅供参考。",
            *[f"{item['label']}: {item['error']}" for item in attempts],
        ]
        return _result(
            symbol=symbol,
            count=bounded_count,
            preferred_source=preferred_source,
            source_key=source_key,
            source_label=source_label,
            source_origin=_persisted_source(records, catalog),
            records=records,
            as_of=as_of,
            attempts=attempts,
            warnings=warnings,
            errors=[],
            cached=True,
            partial=True,
        )

    messages = [f"{item['label']}: {item['error']}" for item in attempts]
    return _result(
        symbol=symbol,
        count=bounded_count,
        preferred_source=preferred_source,
        source_key=None,
        source_label=catalog[preferred_source][0],
        records=[],
        as_of=as_of,
        attempts=attempts,
        warnings=[],
        errors=messages or [f"{catalog[preferred_source][0]}未返回 K 线数据"],
        cached=False,
    )


__all__ = ["read_reliable_kline"]
