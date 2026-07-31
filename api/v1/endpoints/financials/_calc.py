# -*- coding: utf-8 -*-
"""Valuation calculation helpers — PE percentiles, dividend yield, clamping."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

from api.v1.endpoints.financials._symbol import (
    _normalize_symbol,
    _safe_float,
    _safe_str,
    _pick_col,
    _row_pick,
    _parse_date,
)


def _calc_pe_percentiles(df, current_pe: Optional[float]) -> dict:
    if df is None or df.empty or current_pe is None:
        return {}
    import pandas as pd

    pe_col = _pick_col(df.columns, ["pe_ttm", "市盈率ttm", "滚动市盈率", "pe"], exclude=["分位"])
    if pe_col is None:
        return {}
    date_col = _pick_col(df.columns, ["日期", "date", "trade"])
    work_df = df.copy()
    work_df["_pe"] = pd.to_numeric(work_df[pe_col], errors="coerce")
    work_df = work_df.dropna(subset=["_pe"])
    if work_df.empty:
        return {}
    if date_col is not None:
        work_df["_date"] = pd.to_datetime(work_df[date_col], errors="coerce")
    else:
        work_df["_date"] = pd.NaT

    now = datetime.now()
    result = {}
    for years in (5, 3, 1):
        window = work_df
        if work_df["_date"].notna().any():
            window = work_df[work_df["_date"] >= now - timedelta(days=365 * years)]
        if window.empty:
            continue
        result[f"{years}y"] = round(float((window["_pe"] <= current_pe).sum()) / float(len(window)) * 100, 2)
    return result


def _calc_pe_percentiles_from_em(df, current_pe: Optional[float]) -> dict:
    if df is None or df.empty or current_pe is None or "PE(TTM)" not in df.columns:
        return {}
    import pandas as pd

    work_df = df.copy()
    work_df["_pe"] = pd.to_numeric(work_df["PE(TTM)"], errors="coerce")
    work_df = work_df.dropna(subset=["_pe"])
    if work_df.empty:
        return {}
    date_col = "数据日期" if "数据日期" in work_df.columns else _pick_col(work_df.columns, ["日期", "date"])
    if date_col is not None:
        work_df["_date"] = pd.to_datetime(work_df[date_col], errors="coerce")
    else:
        work_df["_date"] = pd.NaT

    now = datetime.now()
    result = {}
    for years in (5, 3, 1):
        window = work_df
        if work_df["_date"].notna().any():
            window = work_df[work_df["_date"] >= now - timedelta(days=365 * years)]
        if not window.empty:
            result[f"{years}y"] = round(float((window["_pe"] <= current_pe).sum()) / float(len(window)) * 100, 2)
    return result


def _calc_dividend_yield(
    symbol: str, latest_close: Optional[float]
) -> tuple[Optional[float], Optional[str], Optional[str]]:
    if not latest_close or latest_close <= 0:
        return None, None, None
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_fhps_detail_em(symbol=_normalize_symbol(symbol))
        if df is None or df.empty:
            return None, None, None
        date_col = "除权除息日" if "除权除息日" in df.columns else _pick_col(df.columns, ["除权", "日期"])
        cash_col = (
            "现金分红-现金分红比例"
            if "现金分红-现金分红比例" in df.columns
            else _pick_col(df.columns, ["现金分红", "派息"])
        )
        if cash_col is None:
            return None, None, None
        work_df = df.copy()
        if date_col is not None:
            work_df["_date"] = pd.to_datetime(work_df[date_col], errors="coerce")
        if "方案进度" in work_df.columns:
            work_df = work_df[work_df["方案进度"].astype(str).str.contains("实施", na=False)]
        if date_col is not None and not work_df.empty:
            work_df = work_df.sort_values("_date", ascending=False)
        cash_per_10 = pd.to_numeric(work_df[cash_col].iloc[0], errors="coerce") if not work_df.empty else None
        if cash_per_10 is None or pd.isna(cash_per_10):
            return None, None, None
        cash_per_share = float(cash_per_10) / 10.0
        latest_date = work_df["_date"].iloc[0] if date_col is not None else None
        dividend_date = (
            latest_date.strftime("%Y-%m-%d") if latest_date is not None and not pd.isna(latest_date) else None
        )
        return round(cash_per_share / latest_close * 100, 2), dividend_date, "stock_fhps_detail_em"
    except Exception as exc:
        logger.warning(f"[Valuation] dividend yield failed for {symbol}: {exc}")
        return None, None, None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
