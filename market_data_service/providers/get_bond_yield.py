"""Chinese and US sovereign-yield history with same-date curve spread."""

from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any
import pandas as pd
import requests
from market_data_service.providers.common import cached_call
from market_data_service.providers._macro_common import (
    latest_date,
    number,
    ordered,
)

COUNTRIES = {"cn": "中国", "us": "美国"}
TERMS = {"2y": "2年", "5y": "5年", "10y": "10年", "30y": "30年"}
_COLUMNS = {
    "cn": {term: f"中国国债收益率{label}" for term, label in TERMS.items()},
    "us": {term: f"美国国债收益率{label}" for term, label in TERMS.items()},
}
_EASTMONEY_COLUMNS = {
    "SOLAR_DATE": "日期",
    "EMM00588704": "中国国债收益率2年",
    "EMM00166462": "中国国债收益率5年",
    "EMM00166466": "中国国债收益率10年",
    "EMM00166469": "中国国债收益率30年",
    "EMG00001306": "美国国债收益率2年",
    "EMG00001308": "美国国债收益率5年",
    "EMG00001310": "美国国债收益率10年",
    "EMG00001312": "美国国债收益率30年",
}


def _fetch_frame():
    """Fetch one recent page instead of AKShare's 19-page historical crawl.

    The tool accepts at most 250 observations. Eastmoney returns newest rows
    first and one page contains 500 observations, so requesting the remaining
    archive only increases latency and makes the assistant hit its hard timeout.
    """
    response = requests.get(
        "https://datacenter.eastmoney.com/api/data/get",
        params={
            "type": "RPTA_WEB_TREASURYYIELD",
            "sty": "ALL",
            "st": "SOLAR_DATE",
            "sr": "-1",
            "token": "894050c76af8597a853f5b408b759f5d",
            "p": "1",
            "ps": "500",
            "pageNo": "1",
            "pageNum": "1",
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=(4, 10),
    )
    response.raise_for_status()
    payload = response.json()
    records = (payload.get("result") or {}).get("data") or []
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    frame.rename(columns=_EASTMONEY_COLUMNS, inplace=True)
    required = [
        "日期",
        *[column for columns in _COLUMNS.values() for column in columns.values()],
    ]
    if any((column not in frame.columns for column in required)):
        missing = [column for column in required if column not in frame.columns]
        raise ValueError(f"债券收益率响应缺少字段: {', '.join(missing)}")
    frame = frame[required].copy()
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.date
    for column in required[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame.dropna(subset=["日期"], inplace=True)
    frame.sort_values("日期", inplace=True)
    frame.reset_index(drop=True, inplace=True)
    return frame


def _series_from_frame(
    frame: Any, country: str, term: str, limit: int
) -> list[dict[str, Any]]:
    column = _COLUMNS[country][term]
    if (
        frame is None
        or frame.empty
        or "日期" not in frame.columns
        or (column not in frame.columns)
    ):
        return []
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        value = number(row.get(column))
        if value is not None:
            rows.append({"date": str(row.get("日期") or "")[:10], "value": value})
    return ordered(rows, "date")[-limit:]


def read_bond_yield_eastmoney(
    country: str = "cn", term: str = "10y", days: int = 30, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read one Eastmoney yield series without curve calculations or DB fallback."""
    country = str(country or "").strip().lower()
    term = str(term or "").strip().lower()
    days = int(days)
    if country not in COUNTRIES:
        raise ValueError(f"不支持的国家: {country}")
    if term not in TERMS:
        raise ValueError(f"不支持的期限: {term}")
    if not 5 <= days <= 250:
        raise ValueError("days 必须在 5 到 250 之间")
    now = datetime.now().astimezone()
    errors: list[str] = []
    try:
        frame, cached = (
            cached_call(
                "bond-yield:eastmoney:source:v1",
                _fetch_frame,
                ttl_seconds=6 * 3600,
                attempts=1,
            )
            if use_cache
            else (_fetch_frame(), False)
        )
    except Exception as exc:
        frame, cached = (None, False)
        errors.append(f"中美国债收益率: {exc}")
    history = _series_from_frame(frame, country, term, days)
    data_date = latest_date(history, "date")
    if not history and (not errors):
        errors.append("东方财富没有返回可用国债收益率记录")
    return {
        "country": country,
        "country_name": COUNTRIES[country],
        "term": term,
        "term_label": TERMS[term],
        "history": history,
        "history_count": len(history),
        "latest": history[-1] if history else None,
        "units": {"yield": "%"},
        "source": "东方财富中美国债收益率",
        "source_scope": "single_sovereign_yield_series",
        "success": bool(history),
        "partial": False,
        "errors": errors,
        "warnings": [],
        "data_time": data_date.isoformat() if data_date else None,
        "is_stale": data_date < now.date() - timedelta(days=7) if data_date else None,
        "freshness_unknown": data_date is None,
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }
