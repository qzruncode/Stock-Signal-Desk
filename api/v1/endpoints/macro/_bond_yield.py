# -*- coding: utf-8 -*-
"""Bond-yield (/bond-yield) endpoint — government bond yield curve.

Data source: 东方财富 bond_zh_us_rate.

数据入库，接口从数据库读取，后台异步刷新。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from ._helpers import safe_float, get_db, is_fresh_today, latest_series_date, is_series_stale
from ._cache import bg_refresh_if_stale

logger = logging.getLogger(__name__)

router = APIRouter()

BOND_COUNTRY_MAP = {
    "cn": "中国",
    "us": "美国",
}

BOND_TERM_MAP = {
    "2y": "2年",
    "5y": "5年",
    "10y": "10年",
    "30y": "30年",
}

_BOND_CN_COL_MAP = {
    "2y": "中国国债收益率2年",
    "5y": "中国国债收益率5年",
    "10y": "中国国债收益率10年",
    "30y": "中国国债收益率30年",
}

_BOND_US_COL_MAP = {
    "2y": "美国国债收益率2年",
    "5y": "美国国债收益率5年",
    "10y": "美国国债收益率10年",
    "30y": "美国国债收益率30年",
}

_BOND_SPREAD_COL = {
    "cn": "中国国债收益率10年-2年",
    "us": "美国国债收益率10年-2年",
}

_lock = threading.Lock()


def _fetch_bond_yield(country: str = "cn"):
    """Fetch government bond yield curve from 东方财富 (ak.bond_zh_us_rate)."""
    import akshare as ak
    if country not in BOND_COUNTRY_MAP:
        raise HTTPException(status_code=400, detail={
            "error": "unsupported_country",
            "message": f"不支持的国家: {country}，支持: {list(BOND_COUNTRY_MAP.keys())}",
        })
    try:
        df = ak.bond_zh_us_rate()
        if df is None or df.empty:
            return None
        return df
    except Exception as e:
        logger.warning(f"[Macro-债券] 失败: {e}")
        return None


def _build_bond_response(
    latest_yield: float | None,
    history: list[dict],
    spread: float | None,
    country: str,
    term: str,
    cached: bool,
) -> dict:
    return {
        "country": country,
        "term": term,
        "latest_yield": latest_yield,
        "history": history,
        "spread": spread,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": cached,
        "source": "东方财富",
        "errors": [],
        "data_time": latest_series_date(history, "date"),
        "is_stale": is_series_stale(history, "date", 14),
        "fallback_used": cached,
    }


def _save_bond_from_df(db, df, country: str, term: str) -> tuple[list[dict], float | None]:
    col_map = _BOND_CN_COL_MAP if country == "cn" else _BOND_US_COL_MAP
    term_col = col_map.get(term)
    if term_col is None:
        return [], None

    df_sorted = df.sort_values("日期").reset_index(drop=True)
    valid = df_sorted[df_sorted[term_col].notna()].copy()

    last_22 = valid.tail(22)
    history = []
    for _, row in last_22.iterrows():
        date_str = str(row.get("日期", "")).strip()
        val = safe_float(row[term_col])
        if val is not None:
            history.append({"date": date_str, "value": val})

    if history:
        db.save_bond_yield_daily(country, term, history)

    spread = None
    spread_col = _BOND_SPREAD_COL.get(country)
    if spread_col and spread_col in valid.columns and not valid.empty:
        latest = valid.iloc[-1]
        s = safe_float(latest.get(spread_col))
        if s is not None:
            spread = round(s, 4)

    return history, spread


def _calc_bond_spread(db, country: str) -> float | None:
    try:
        y10 = db.get_bond_yield_daily(country, "10y", limit=1)
        y2 = db.get_bond_yield_daily(country, "2y", limit=1)
        if y10 and y2:
            v10 = y10[0].get("value")
            v2 = y2[0].get("value")
            if v10 is not None and v2 is not None:
                return round(v10 - v2, 4)
    except Exception:
        logger.debug("计算利差失败", exc_info=True)
    return None


def _bg_fetch_and_save(country: str):
    """Fetch bond data for all terms and save to DB."""
    df = _fetch_bond_yield(country)
    if df is not None:
        db = get_db()
        for t in BOND_TERM_MAP:
            _save_bond_from_df(db, df, country, t)
    return df


@router.get("/bond-yield", summary="获取国债收益率")
def get_bond_yield(
    country: str = Query("cn", description="国家: cn(中国) | us(美国)"),
    term: str = Query("10y", description="期限: 2y | 5y | 10y | 30y"),
):
    """获取国债收益率。

    返回最新收益率、近一个月走势、期限利差（10y-2y）。
    数据源: 东方财富 bond_zh_us_rate。
    优先从数据库读取，后台异步刷新。
    """
    country = country.strip().lower()
    term = term.strip().lower()

    if term not in BOND_TERM_MAP:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_term",
            "message": f"不支持的期限: {term}，支持的期限: {list(BOND_TERM_MAP.keys())}",
        })

    if country not in BOND_COUNTRY_MAP:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_country",
            "message": f"不支持的国家: {country}，支持的国家: {list(BOND_COUNTRY_MAP.keys())}",
        })

    db = get_db()
    history = db.get_bond_yield_daily(country, term, limit=30)

    if history and is_fresh_today(history):
        return _build_bond_response(
            latest_yield=history[0].get("value"),
            history=history,
            spread=_calc_bond_spread(db, country),
            country=country,
            term=term,
            cached=True,
        )

    if history:
        bg_refresh_if_stale(
            lock=_lock,
            fetcher=lambda: _bg_fetch_and_save(country),
            on_success=lambda _: None,  # data already saved inside _bg_fetch_and_save
            log_tag="[Macro-债券]",
        )
        return _build_bond_response(
            latest_yield=history[0].get("value"),
            history=history,
            spread=_calc_bond_spread(db, country),
            country=country,
            term=term,
            cached=True,
        )

    df = _fetch_bond_yield(country)
    if df is None:
        raise HTTPException(status_code=502, detail={
            "error": "no_data",
            "message": f"无法获取 {country} 国债收益率数据",
        })

    for t in BOND_TERM_MAP:
        _save_bond_from_df(db, df, country, t)

    history = db.get_bond_yield_daily(country, term, limit=30)
    spread = _calc_bond_spread(db, country)
    return _build_bond_response(
        latest_yield=history[0].get("value") if history else None,
        history=history,
        spread=spread,
        country=country,
        term=term,
        cached=False,
    )