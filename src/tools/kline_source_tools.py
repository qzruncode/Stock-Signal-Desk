"""Internal daily K-line adapter.

Only ``source_operations`` defines the model-visible operations.  This module
keeps the provider adapters and strict single-source path; recent-bar reads
additionally use the shared completed-bar gateway when fallback is enabled.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

from src.tools._kline import (
    _fetch_kline_em,
    _fetch_kline_sina,
    _fetch_kline_tencent,
    _kline_data_time,
    _kline_is_stale,
    _normalize_kline_df,
)
from src.tools.kline_gateway import read_reliable_kline, read_reliable_kline_range
from src.tools.symbols import resolve_local_symbol


_SourceFetcher = Callable[[str, str, str], Any]
_SOURCES: dict[str, tuple[str, _SourceFetcher]] = {
    "eastmoney": ("东方财富日线（AKShare）", _fetch_kline_em),
    "sina": ("新浪财经日线（AKShare）", _fetch_kline_sina),
    "tencent": ("腾讯财经日线（AKShare）", _fetch_kline_tencent),
}


def _resolve_a_share_symbol(symbol: str) -> str:
    code = resolve_local_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"无法识别 A 股证券代码或名称: {symbol}")
    return code


def _validate_date(value: str, field: str) -> str:
    try:
        return datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{field} 必须是有效的 YYYYMMDD 日期") from exc


def _bar_complete(records: list[dict[str, Any]], *, now: datetime) -> bool:
    return not (
        records
        and str(records[-1].get("date") or "")[:10] == now.date().isoformat()
        and now.time() < datetime.strptime("15:00", "%H:%M").time()
    )


def _read_source(
    *,
    symbol: str,
    source_key: str,
    start_date: str,
    end_date: str,
    requested_count: int | None,
    range_mode: bool,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    code = _resolve_a_share_symbol(symbol)
    source_label, fetcher = _SOURCES[source_key]
    now = datetime.now().astimezone()
    if not range_mode and requested_count is not None and allow_fallback:
        return read_reliable_kline(
            code,
            preferred_source=source_key,
            count=requested_count,
            sources=_SOURCES,
            allow_fallback=True,
        )
    if range_mode and allow_fallback:
        return read_reliable_kline_range(
            code,
            preferred_source=source_key,
            start_date=start_date,
            end_date=end_date,
            sources=_SOURCES,
            allow_fallback=True,
        )
    try:
        frame = fetcher(code, start_date, end_date)
        records = _normalize_kline_df(frame, code, source_key)
    except Exception as exc:
        records = []
        error = f"{type(exc).__name__}: {exc}"
    else:
        error = ""
    if requested_count is not None and len(records) > requested_count:
        records = records[-requested_count:]
    data_time = _kline_data_time(records)
    success = bool(records)
    return {
        "success": success,
        "partial": False,
        "symbol": code,
        "source": source_label,
        "source_scope": f"{source_key}_daily_qfq_kline",
        "count": len(records),
        "data": records,
        "data_time": data_time,
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": (
            None
            if data_time
            else f"{source_label}未返回有效日线日期；_fetched_at 仅表示本服务获取时间。"
        ),
        # Historical requests intentionally do not compare their end date with
        # today: an older requested range is not stale merely because it is old.
        "is_stale": None if range_mode or not success else _kline_is_stale(records),
        "freshness_unknown": data_time is None,
        "fallback_used": False,
        "adjust": "qfq",
        "period": "daily",
        "volume_unit": "股",
        "amount_unit": "元",
        "bar_complete": _bar_complete(records, now=now),
        "requested_start_date": start_date,
        "requested_end_date": end_date,
        "errors": [] if success else [error or f"{source_label}未返回 K 线数据"],
        "warnings": [],
        "_cached": False,
        "_fetched_at": now.isoformat(),
    }


__all__ = ["_read_source", "_validate_date"]
