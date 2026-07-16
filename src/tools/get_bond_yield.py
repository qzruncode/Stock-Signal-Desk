"""Chinese and US sovereign-yield history with same-date curve spread."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from src.tools._akshare import cached_call
from src.tools._macro_common import get_db, latest_date, number, ordered
from src.tools.base import ToolSpec, object_schema


COUNTRIES = {"cn": "中国", "us": "美国"}
TERMS = {"2y": "2年", "5y": "5年", "10y": "10年", "30y": "30年"}
_COLUMNS = {
    "cn": {term: f"中国国债收益率{label}" for term, label in TERMS.items()},
    "us": {term: f"美国国债收益率{label}" for term, label in TERMS.items()},
}


def _fetch_frame():
    import akshare as ak

    return ak.bond_zh_us_rate()


def _series_from_frame(frame: Any, country: str, term: str, limit: int) -> list[dict[str, Any]]:
    column = _COLUMNS[country][term]
    if frame is None or frame.empty or "日期" not in frame.columns or column not in frame.columns:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        value = number(row.get(column))
        if value is not None:
            rows.append({"date": str(row.get("日期") or "")[:10], "value": value})
    return ordered(rows, "date")[-limit:]


def _same_date_spread(frame: Any, country: str) -> tuple[float | None, str | None]:
    if frame is None or frame.empty:
        return None, None
    col10, col2 = _COLUMNS[country]["10y"], _COLUMNS[country]["2y"]
    if any(column not in frame.columns for column in ("日期", col10, col2)):
        return None, None
    rows = []
    for _, row in frame.iterrows():
        y10, y2 = number(row.get(col10)), number(row.get(col2))
        if y10 is not None and y2 is not None:
            rows.append((str(row.get("日期") or "")[:10], y10 - y2))
    if not rows:
        return None, None
    rows.sort(key=lambda item: item[0])
    return round(rows[-1][1], 4), rows[-1][0]


def _cached_spread(country: str) -> tuple[float | None, str | None]:
    db = get_db()
    tens = {row["date"]: row.get("value") for row in db.get_bond_yield_daily(country, "10y", limit=60) or []}
    twos = {row["date"]: row.get("value") for row in db.get_bond_yield_daily(country, "2y", limit=60) or []}
    dates = sorted(set(tens) & set(twos))
    if not dates:
        return None, None
    date_key = dates[-1]
    y10, y2 = number(tens[date_key]), number(twos[date_key])
    return (round(y10 - y2, 4), date_key) if y10 is not None and y2 is not None else (None, None)


def get_bond_yield(country: str = "cn", term: str = "10y", days: int = 30) -> dict[str, Any]:
    country = str(country or "").strip().lower()
    term = str(term or "").strip().lower()
    days = int(days)
    if country not in COUNTRIES:
        raise ValueError(f"不支持的国家: {country}")
    if term not in TERMS:
        raise ValueError(f"不支持的期限: {term}")
    if not 5 <= days <= 250:
        raise ValueError("days 必须在 5 到 250 之间")

    errors: list[str] = []
    warnings: list[str] = []
    frame = None
    cached = False
    try:
        frame, cached = cached_call("bond-zh-us-rate", _fetch_frame, ttl_seconds=6 * 3600)
    except Exception as exc:
        errors.append(f"中美国债收益率: {exc}")

    history = _series_from_frame(frame, country, term, days)
    spread, spread_date = _same_date_spread(frame, country)
    fallback_used = False
    if history:
        try:
            db = get_db()
            for curve_term in TERMS:
                curve = _series_from_frame(frame, country, curve_term, max(days, 60))
                if curve:
                    db.save_bond_yield_daily(country, curve_term, curve)
        except Exception as exc:
            warnings.append(f"债券本地缓存写入失败: {exc}")
    else:
        fallback_used = True
        try:
            history = ordered(get_db().get_bond_yield_daily(country, term, limit=days) or [], "date")[-days:]
            spread, spread_date = _cached_spread(country)
        except Exception as exc:
            errors.append(f"债券本地缓存: {exc}")
            history = []

    data_date = latest_date(history, "date")
    success = bool(history)
    stale = data_date is None or data_date < datetime.now().date() - timedelta(days=7)
    latest = history[-1] if history else {}
    retrieved_at = datetime.now().astimezone().isoformat()
    return {
        "country": country,
        "country_name": COUNTRIES[country],
        "term": term,
        "term_label": TERMS[term],
        "latest": latest,
        "latest_yield": latest.get("value"),
        "history": history,
        "history_count": len(history),
        "spread_10y_minus_2y": spread,
        "spread": spread,
        "spread_date": spread_date,
        "units": {"yield": "%", "spread": "percentage_point"},
        "source": "AKShare/东方财富中美国债收益率" if not fallback_used else "本地债券历史缓存",
        "success": success,
        "partial": success and bool(errors or warnings),
        "data_time": data_date.isoformat() if data_date else None,
        "retrieved_at": retrieved_at,
        "is_stale": stale,
        "freshness_unknown": data_date is None,
        "fallback_used": fallback_used,
        "fallback_recommended": not success or stale,
        "errors": errors[:10],
        "warnings": warnings[:10],
        "_cached": cached if not fallback_used else True,
        "_fetched_at": retrieved_at,
    }


TOOL = ToolSpec(
    name="get_bond_yield",
    description=(
        "获取中国或美国2年、5年、10年、30年国债收益率历史，并用同一交易日的10年和2年数据计算期限利差。"
        "缓存命中不等于降级；返回百分比单位、利差日期和陈旧状态。"
    ),
    parameters=object_schema({
        "country": {"type": "string", "enum": list(COUNTRIES), "default": "cn"},
        "term": {"type": "string", "enum": list(TERMS), "default": "10y"},
        "days": {"type": "integer", "minimum": 5, "maximum": 250, "default": 30},
    }),
    executor=get_bond_yield,
    category="macro",
)
