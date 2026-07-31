# -*- coding: utf-8 -*-
"""Symbol / type conversion helpers for financials package."""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _to_em_symbol(symbol: str) -> str:
    code = symbol.strip()
    if code.startswith(("SH", "SZ", "BJ")):
        return code
    if code[0] in ("6", "9"):
        return f"SH{code}"
    return f"SZ{code}"


def _normalize_symbol(symbol: str) -> str:
    code = symbol.strip().upper()
    if "." in code:
        code = code.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            return code[2:]
    return code


def _to_ts_code(symbol: str) -> str:
    code = _normalize_symbol(symbol)
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("8", "4")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _to_top_holder_symbol(symbol: str) -> str:
    return _to_em_symbol(_normalize_symbol(symbol)).lower()


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip().replace(",", "").replace("%", "")
        if not val or val.lower() in ("false", "none", "nan", "-"):
            return None
    try:
        v = float(val)
    except (ValueError, TypeError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _safe_amount(val) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("false", "none", "nan", "-"):
        return None
    multiplier = 1.0
    if "亿" in s:
        s = s.replace("亿", "")
        multiplier = 1e8
    elif "万" in s:
        s = s.replace("万", "")
        multiplier = 1e4
    try:
        return float(s) * multiplier
    except (ValueError, TypeError):
        return None


def _safe_str(val) -> str:
    if val is None:
        return ""
    text = str(val).strip()
    return "" if text in ("", "-", "nan", "None", "NaT") else text


def _safe_pct(val) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip().replace("%", "")
    if not s or s.lower() in ("false", "none", "nan", "-"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _safe_int_like(val) -> Optional[int]:
    num = _safe_float(val)
    return int(num) if num is not None else None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _pick_col(columns, keywords: list[str], *, exclude: list[str] | None = None) -> Any:
    exclude = exclude or []
    for col in columns:
        col_s = str(col)
        if any(k.lower() in col_s.lower() for k in keywords) and not any(x.lower() in col_s.lower() for x in exclude):
            return col
    return None


def _row_pick(row, keywords: list[str], *, exclude: list[str] | None = None) -> Any:
    col = _pick_col(row.index, keywords, exclude=exclude)
    if col is None:
        return None
    return row.get(col)


def _parse_date(val) -> Optional[datetime]:
    if val is None:
        return None
    try:
        parsed = pd.to_datetime(val)
    except (ValueError, TypeError):
        logger.warning("[Financials] _parse_date pd.to_datetime failed for value=%s", val)
        return None
    if parsed is None:
        return None
    try:
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime()
    except (ValueError, TypeError):
        logger.warning("[Financials] _parse_date to_pydatetime failed for value=%s", val)
        return None


def _latest_quarter_dates(limit: int = 8) -> list[str]:
    today = datetime.now().date()
    candidates: list[str] = []
    for year in range(today.year, today.year - 4, -1):
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            d = datetime(year, month, day).date()
            if d <= today:
                candidates.append(d.strftime("%Y%m%d"))
    return candidates[:limit]


def _parse_chinese_share_amount(value: Any) -> Optional[float]:
    text = _safe_str(value).replace(",", "")
    if not text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return _safe_float(text)
    amount = float(match.group(1))
    if "亿" in text:
        amount *= 1e8
    elif "万" in text:
        amount *= 1e4
    return amount
