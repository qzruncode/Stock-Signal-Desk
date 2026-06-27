# -*- coding: utf-8 -*-
"""Macro-indicator (/indicator) endpoint — key economic indicators.

Indicators: PMI, CPI, PPI, GDP, M2, 社融, LPR.
Data source: 东方财富 (via akshare).

数据入库，接口从数据库读取，后台异步刷新。

This module also exports ``INDICATOR_FETCHERS`` for use by
``stock_info.business._fetch_macro_data``.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from ._helpers import safe_float, get_db, latest_series_date, is_series_stale
from ._cache import bg_refresh_if_stale

logger = logging.getLogger(__name__)

router = APIRouter()

INDICATOR_MAP = {
    "PMI": "制造业采购经理指数",
    "CPI": "居民消费价格指数",
    "PPI": "工业生产者出厂价格指数",
    "GDP": "国内生产总值",
    "M2": "货币和准货币(M2)",
    "社融": "社会融资规模",
    "LPR": "贷款市场报价利率",
}

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def _fetch_indicator_pmi():
    import akshare as ak
    df = ak.macro_china_pmi()
    if df is None or df.empty:
        return None
    df = df.iloc[::-1].reset_index(drop=True)
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": safe_float(row.get("制造业-指数")),
            "yoy": safe_float(row.get("制造业-同比增长")),
            "extra": {
                "非制造业-指数": safe_float(row.get("非制造业-指数")),
                "非制造业-同比增长": safe_float(row.get("非制造业-同比增长")),
            },
        })
    return records


def _fetch_indicator_cpi():
    import akshare as ak
    df = ak.macro_china_cpi()
    if df is None or df.empty:
        return None
    df = df.iloc[::-1].reset_index(drop=True)
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": safe_float(row.get("全国-当月")),
            "yoy": safe_float(row.get("全国-同比增长")),
            "mom": safe_float(row.get("全国-环比增长")),
        })
    return records


def _fetch_indicator_ppi():
    import akshare as ak
    df = ak.macro_china_ppi()
    if df is None or df.empty:
        return None
    df = df.iloc[::-1].reset_index(drop=True)
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": safe_float(row.get("当月")),
            "yoy": safe_float(row.get("当月同比增长")),
        })
    return records


def _fetch_indicator_gdp():
    import akshare as ak
    df = ak.macro_china_gdp()
    if df is None or df.empty:
        return None
    df = df.iloc[::-1].reset_index(drop=True)
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("季度", "")).strip(),
            "value": safe_float(row.get("国内生产总值-绝对值")),
            "yoy": safe_float(row.get("国内生产总值-同比增长")),
        })
    return records


def _fetch_indicator_m2():
    import akshare as ak
    df = ak.macro_china_money_supply()
    if df is None or df.empty:
        return None
    df = df.iloc[::-1].reset_index(drop=True)
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": safe_float(row.get("货币和准货币(M2)-数量(亿元)")),
            "yoy": safe_float(row.get("货币和准货币(M2)-同比增长")),
            "mom": safe_float(row.get("货币和准货币(M2)-环比增长")),
        })
    return records


def _fetch_indicator_social_finance():
    import akshare as ak
    df = ak.macro_china_shrzgm()
    if df is None or df.empty:
        return None
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": safe_float(row.get("社会融资规模增量")),
        })
    return records


def _fetch_indicator_lpr():
    import akshare as ak
    df = ak.macro_china_lpr()
    if df is None or df.empty:
        return None
    records = []
    for _, row in df.iterrows():
        value = safe_float(row.get("LPR1Y"))
        if value is None:
            continue
        records.append({
            "period": str(row.get("TRADE_DATE", "")).strip(),
            "value": value,
            "extra": {
                "LPR5Y": safe_float(row.get("LPR5Y")),
            },
        })
    return records


INDICATOR_FETCHERS = {
    "PMI": _fetch_indicator_pmi,
    "CPI": _fetch_indicator_cpi,
    "PPI": _fetch_indicator_ppi,
    "GDP": _fetch_indicator_gdp,
    "M2": _fetch_indicator_m2,
    "社融": _fetch_indicator_social_finance,
    "LPR": _fetch_indicator_lpr,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trend_label(records: list[dict]) -> str:
    for k in ("yoy", "value"):
        vals = [r.get(k) for r in records[-5:] if r.get(k) is not None]
        if len(vals) >= 2:
            if vals[-1] > vals[0] + 0.05:
                return "上升"
            if vals[-1] < vals[0] - 0.05:
                return "下降"
            return "持平"
    return "持平"


def _build_indicator_response(
    indicator: str,
    records: list[dict],
    trend: str,
    cached: bool,
) -> dict:
    return {
        "indicator": indicator,
        "indicator_name": INDICATOR_MAP.get(indicator, indicator),
        "latest": records[-1] if records else {},
        "history": records,
        "trend": trend,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": cached,
        "source": "东方财富",
        "errors": [],
        "data_time": latest_series_date(records, "period"),
        "is_stale": is_series_stale(records, "period", 120),
        "fallback_used": cached,
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/indicator", summary="获取宏观经济指标")
def get_macro_indicator(
    indicator: str = Query(..., description="指标名称: PMI | CPI | PPI | GDP | M2 | 社融 | LPR"),
    months: int = Query(12, ge=1, le=120, description="返回最近N个月数据"),
):
    """获取关键宏观经济数据。

    优先从数据库读取，后台异步刷新。
    """
    if indicator.strip() not in ("社融",):
        indicator = indicator.strip().upper()
    else:
        indicator = "社融"

    if indicator not in INDICATOR_FETCHERS:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_indicator",
            "message": f"不支持的指标: {indicator}，支持的指标: {list(INDICATOR_FETCHERS.keys())}",
        })

    db = get_db()
    records = db.get_macro_indicator(indicator, limit=months)
    if records is not None:
        records = records[-months:] if len(records) > months else records

    if records:
        trend = _trend_label(records)
        bg_refresh_if_stale(
            lock=_lock,
            fetcher=INDICATOR_FETCHERS[indicator],
            on_success=lambda data: db.save_macro_indicator(indicator, data),
            log_tag="[Macro-指标]",
        )
        return _build_indicator_response(indicator, records, trend, cached=True)

    fetcher = INDICATOR_FETCHERS[indicator]
    new_records = fetcher()
    if new_records is None:
        raise HTTPException(status_code=502, detail={
            "error": "no_data",
            "message": f"无法获取 {indicator} 数据",
        })

    new_records = new_records[-months:] if len(new_records) > months else new_records
    db.save_macro_indicator(indicator, new_records)
    trend = _trend_label(new_records)
    return _build_indicator_response(indicator, new_records, trend, cached=False)