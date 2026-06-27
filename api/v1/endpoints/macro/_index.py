# -*- coding: utf-8 -*-
"""Index (/index) endpoint — market index OHLCV data.

Data sources (in fallback order):
  1. ak.stock_zh_index_daily()     — 新浪财经（日线）
  2. ak.stock_zh_index_spot_sina() — 新浪财经（快照，补成交额/涨跌幅）

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

# ---------------------------------------------------------------------------
# Index map
# ---------------------------------------------------------------------------

INDEX_MAP = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
    "000688": "科创50",
}

# ---------------------------------------------------------------------------
# Background-refresh lock
# ---------------------------------------------------------------------------

_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Data source: 新浪日线（primary — OHLCV）
# ---------------------------------------------------------------------------


def _fetch_index_sina_daily(index_code: str, days: int):
    """Fetch index daily OHLCV from Sina."""
    import akshare as ak
    prefix_map = {"000001": "sh", "399001": "sz", "399006": "sz", "000688": "sh"}
    prefix = prefix_map.get(index_code, "sh")
    sina_symbol = f"{prefix}{index_code}"
    try:
        df = ak.stock_zh_index_daily(symbol=sina_symbol)
        if df is not None and not df.empty:
            df = df.tail(days).reset_index(drop=True)
            return df
        logger.warning(f"[Macro-新浪日线] 空数据 for {sina_symbol}")
    except Exception as e:
        logger.warning(f"[Macro-新浪日线] 失败: {e}")
    return None


# ---------------------------------------------------------------------------
# Data source: 新浪快照（补最新涨跌幅 + 成交额）
# ---------------------------------------------------------------------------


def _fetch_index_spot():
    """Fetch index spot data from Sina (all indices at once)."""
    import akshare as ak
    try:
        df = ak.stock_zh_index_spot_sina()
        if df is not None and not df.empty:
            return df
    except Exception as e:
        logger.warning(f"[Macro-新浪快照] 失败: {e}")
    return None


# ---------------------------------------------------------------------------
# Fetch with fallback → normalize → return records
# ---------------------------------------------------------------------------


def _fetch_index_with_fallback(index_code: str, days: int) -> list[dict]:
    """Fetch index data: daily OHLCV + spot pct_chg/amount."""
    daily_df = _fetch_index_sina_daily(index_code, days)
    if daily_df is None or daily_df.empty:
        return []

    prefix_map = {"000001": "sh", "399001": "sz", "399006": "sz", "000688": "sh"}
    spot_code = f"{prefix_map.get(index_code, 'sh')}{index_code}"
    spot_df = _fetch_index_spot()
    spot_row = None
    if spot_df is not None and not spot_df.empty:
        mask = spot_df["代码"] == spot_code
        matched = spot_df[mask]
        if not matched.empty:
            spot_row = matched.iloc[0]

    records = []
    for i, (_, row) in enumerate(daily_df.iterrows()):
        rec = {}
        rec["date"] = str(row.get("date", "")).strip()
        rec["open"] = safe_float(row.get("open"))
        rec["high"] = safe_float(row.get("high"))
        rec["low"] = safe_float(row.get("low"))
        rec["close"] = safe_float(row.get("close"))
        rec["volume"] = safe_float(row.get("volume"))

        if i > 0:
            prev_close = safe_float(daily_df.iloc[i - 1].get("close"))
            curr_close = rec["close"]
            if prev_close and prev_close > 0 and curr_close:
                rec["pct_chg"] = round((curr_close - prev_close) / prev_close * 100, 2)
            else:
                rec["pct_chg"] = None
        else:
            rec["pct_chg"] = None

        records.append(rec)

    if spot_row is not None:
        latest = records[-1] if records else {}
        pct = safe_float(spot_row.get("涨跌幅"))
        if pct is not None:
            latest["pct_chg"] = pct
        amount = safe_float(spot_row.get("成交额"))
        if amount is not None:
            latest["amount"] = amount
        change_amt = safe_float(spot_row.get("涨跌额"))
        if change_amt is not None:
            latest["change_amount"] = change_amt

    return records


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------


def _build_index_response(index_code: str, records: list[dict], cached: bool, source: str) -> dict:
    index_name = INDEX_MAP.get(index_code, index_code)
    latest = records[-1] if records else {}

    return {
        "index_code": index_code,
        "index_name": index_name,
        "latest": latest,
        "history": records,
        "pe_ttm": None,
        "pb": None,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": cached,
        "source": source,
        "errors": [],
        "data_time": latest_series_date(records, "date"),
        "is_stale": is_series_stale(records, "date", 7),
        "fallback_used": cached,
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/index", summary="获取大盘指数数据")
def get_index_data(
    index_code: str = Query("000001", description="指数代码（000001 | 399001 | 399006 | 000688）"),
    days: int = Query(20, ge=1, le=500, description="返回最近N天数据"),
):
    """获取指定指数的行情数据。

    优先从数据库读取，后台异步刷新。
    """
    index_code = index_code.strip()
    if index_code not in INDEX_MAP:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_index_code",
            "message": f"不支持的指数代码: {index_code}，支持的代码: {list(INDEX_MAP.keys())}",
        })

    db = get_db()
    records = db.get_macro_index_daily(index_code, limit=days)

    if records and is_fresh_today(records):
        return _build_index_response(index_code, records, cached=True, source="新浪")

    if records:
        bg_refresh_if_stale(
            lock=_lock,
            fetcher=lambda: _fetch_index_with_fallback(index_code, days),
            on_success=lambda data: db.save_macro_index_daily(index_code, data, data_source="新浪"),
            log_tag="[Macro-Index]",
        )
        return _build_index_response(index_code, records, cached=True, source="新浪")

    new_records = _fetch_index_with_fallback(index_code, days)
    if not new_records:
        raise HTTPException(status_code=502, detail={
            "error": "empty_data",
            "message": f"无法获取 {index_code} 的指数数据",
        })
    db.save_macro_index_daily(index_code, new_records, data_source="新浪")
    return _build_index_response(index_code, new_records, cached=False, source="新浪")