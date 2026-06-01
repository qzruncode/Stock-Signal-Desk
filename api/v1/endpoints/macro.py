# -*- coding: utf-8 -*-
"""Macro data endpoints — index/macro economic data.

Index data sources (in fallback order):
  1. ak.stock_zh_index_daily()     — 新浪财经（日线）
  2. ak.stock_zh_index_spot_sina() — 新浪财经（快照，补成交额/涨跌幅）

Bond yield source:
  ak.bond_china_yield() — 中债国债收益率曲线

Macro indicator source:
  ak.macro_china_pmi/cpi/ppi/gdp/money_supply/shrzgm/lpr()

数据入库，接口从数据库读取，后台异步刷新。
"""

from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

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
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip().replace(",", "").replace("%", "")
        if not val or val.lower() in ("false", "none", "nan", "-"):
            return None
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (ValueError, TypeError):
        return None


def _get_db():
    from src.storage import DatabaseManager
    return DatabaseManager.get_instance()


def _is_fresh_today(records: list[dict]) -> bool:
    """判断数据库记录是否包含今天的数据（已刷新）。"""
    if not records:
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    return records[0].get("date") == today


# ---------------------------------------------------------------------------
# Data source: 新浪日线（primary — OHLCV）
# ---------------------------------------------------------------------------

def _fetch_index_sina_daily(index_code: str, days: int):
    """Fetch index daily OHLCV from Sina."""
    import akshare as ak
    prefix_map = {
        "000001": "sh",
        "399001": "sz",
        "399006": "sz",
        "000688": "sh",
    }
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
        rec["open"] = _safe_float(row.get("open"))
        rec["high"] = _safe_float(row.get("high"))
        rec["low"] = _safe_float(row.get("low"))
        rec["close"] = _safe_float(row.get("close"))
        rec["volume"] = _safe_float(row.get("volume"))

        if i > 0:
            prev_close = _safe_float(daily_df.iloc[i - 1].get("close"))
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
        pct = _safe_float(spot_row.get("涨跌幅"))
        if pct is not None:
            latest["pct_chg"] = pct
        amount = _safe_float(spot_row.get("成交额"))
        if amount is not None:
            latest["amount"] = amount
        change_amt = _safe_float(spot_row.get("涨跌额"))
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
    }


# ---------------------------------------------------------------------------
# Background refresh lock
# ---------------------------------------------------------------------------

_lock = None

def _get_lock():
    global _lock
    if _lock is None:
        _lock = threading.Lock()
    return _lock


# ---------------------------------------------------------------------------
# Index endpoint
# ---------------------------------------------------------------------------

@router.get("/index", summary="获取大盘指数数据")
def get_index_data(
    index_code: str = Query("000001", description="指数代码（000001 | 399001 | 399006 | 000688）"),
    days: int = Query(20, ge=1, le=500, description="返回最近N天数据"),
):
    """获取指定指数的行情数据。

    优先从数据库读取，后台异步刷新数据。
    """
    index_code = index_code.strip()
    if index_code not in INDEX_MAP:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_index_code",
            "message": f"不支持的指数代码: {index_code}，支持的代码: {list(INDEX_MAP.keys())}",
        })

    # 1. 从数据库读取
    db = _get_db()
    records = db.get_macro_index_daily(index_code, limit=days)

    if records and _is_fresh_today(records):
        # 今天已刷新，直接返回
        return _build_index_response(index_code, records, cached=True, source="新浪")

    if records:
        # 有历史数据但非今日，返回并后台刷新
        lock = _get_lock()
        if lock.acquire(blocking=False):
            def _bg_refresh():
                try:
                    new_records = _fetch_index_with_fallback(index_code, days)
                    if new_records:
                        db.save_macro_index_daily(index_code, new_records, data_source="新浪")
                except Exception as e:
                    logger.warning(f"[Macro] 后台刷新失败: {e}")
                finally:
                    lock.release()
            threading.Thread(target=_bg_refresh, daemon=True).start()
        return _build_index_response(index_code, records, cached=True, source="新浪")

    # 2. 数据库为空，同步拉取 + 入库
    new_records = _fetch_index_with_fallback(index_code, days)
    if not new_records:
        raise HTTPException(status_code=502, detail={
            "error": "empty_data",
            "message": f"无法获取 {index_code} 的指数数据",
        })
    db.save_macro_index_daily(index_code, new_records, data_source="新浪")
    return _build_index_response(index_code, new_records, cached=False, source="新浪")


# ============================================================================
# Bond yield endpoint
# ============================================================================

BOND_COUNTRY_MAP = {
    "cn": "中债国债收益率曲线",
}

BOND_TERM_MAP = {
    "1y": "1年",
    "5y": "5年",
    "10y": "10年",
    "30y": "30年",
}


def _fetch_bond_yield(country: str = "cn"):
    """Fetch government bond yield curve."""
    import akshare as ak
    if country == "cn":
        try:
            df = ak.bond_china_yield()
            if df is None or df.empty:
                return None
            return df
        except Exception as e:
            logger.warning(f"[Macro-债券] 失败: {e}")
            return None
    raise HTTPException(status_code=400, detail={
        "error": "unsupported_country",
        "message": f"暂不支持 {country} 国债收益率",
    })


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
        "source": "中国债券信息网",
        "errors": [],
    }


@router.get("/bond-yield", summary="获取国债收益率")
def get_bond_yield(
    country: str = Query("cn", description="国家: cn(中国) | us(美国)"),
    term: str = Query("10y", description="期限: 1y | 5y | 10y | 30y"),
):
    """获取国债收益率。

    返回最新收益率、近一个月走势、期限利差（10y-1y）。
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

    # 1. 从数据库读取
    db = _get_db()
    history = db.get_bond_yield_daily(country, term, limit=30)

    if history and _is_fresh_today(history):
        return _build_bond_response(
            latest_yield=history[0].get("value"),
            history=history,
            spread=_calc_bond_spread(db, country),
            country=country,
            term=term,
            cached=True,
        )

    if history:
        lock = _get_lock()
        if lock.acquire(blocking=False):
            def _bg_refresh():
                try:
                    df = _fetch_bond_yield(country)
                    if df is not None:
                        _save_bond_from_df(db, df, country, term)
                except Exception as e:
                    logger.warning(f"[Macro-债券] 后台刷新失败: {e}")
                finally:
                    lock.release()
            threading.Thread(target=_bg_refresh, daemon=True).start()
        return _build_bond_response(
            latest_yield=history[0].get("value"),
            history=history,
            spread=_calc_bond_spread(db, country),
            country=country,
            term=term,
            cached=True,
        )

    # 2. 数据库为空，同步拉取 + 入库
    df = _fetch_bond_yield(country)
    if df is None:
        raise HTTPException(status_code=502, detail={
            "error": "no_data",
            "message": f"无法获取 {country} 国债收益率数据",
        })

    history, spread = _save_bond_from_df(db, df, country, term)
    return _build_bond_response(
        latest_yield=history[0].get("value") if history else None,
        history=history,
        spread=spread,
        country=country,
        term=term,
        cached=False,
    )


def _save_bond_from_df(db, df, country: str, term: str) -> tuple[list[dict], float | None]:
    """从 DataFrame 提取收益率，入库，返回 history + spread。"""
    col_name = BOND_COUNTRY_MAP[country]
    gb = df[df["曲线名称"] == col_name].sort_values("日期").reset_index(drop=True)
    term_col = BOND_TERM_MAP[term]
    valid = gb[gb[term_col].notna()].copy()

    last_22 = valid.tail(22)
    history = []
    for _, row in last_22.iterrows():
        date_str = str(row.get("日期", "")).strip()
        val = _safe_float(row[term_col])
        if val is not None:
            history.append({"date": date_str, "value": val})

    if history:
        db.save_bond_yield_daily(country, term, history)

    # 计算利差
    spread = None
    if country == "cn":
        y10_col = BOND_TERM_MAP.get("10y")
        y1_col = BOND_TERM_MAP.get("1y")
        if y10_col and y1_col and not valid.empty:
            latest = valid.iloc[-1]
            y10 = _safe_float(latest.get(y10_col))
            y1 = _safe_float(latest.get(y1_col))
            if y10 is not None and y1 is not None:
                spread = round(y10 - y1, 4)

    return history, spread


def _calc_bond_spread(db, country: str) -> float | None:
    """从数据库计算利差（10y - 1y）。"""
    if country != "cn":
        return None
    try:
        y10 = db.get_bond_yield_daily(country, "10y", limit=1)
        y1 = db.get_bond_yield_daily(country, "1y", limit=1)
        if y10 and y1:
            v10 = y10[0].get("value")
            v1 = y1[0].get("value")
            if v10 is not None and v1 is not None:
                return round(v10 - v1, 4)
    except Exception:
        pass
    return None


# ============================================================================
# Macro indicator endpoint
# ============================================================================

INDICATOR_MAP = {
    "PMI": "制造业采购经理指数",
    "CPI": "居民消费价格指数",
    "PPI": "工业生产者出厂价格指数",
    "GDP": "国内生产总值",
    "M2": "货币和准货币(M2)",
    "社融": "社会融资规模",
    "LPR": "贷款市场报价利率",
}


def _fetch_indicator_pmi():
    import akshare as ak
    df = ak.macro_china_pmi()
    if df is None or df.empty:
        return None
    # Newest first → reverse so oldest first for consistent slicing
    df = df.iloc[::-1].reset_index(drop=True)
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": _safe_float(row.get("制造业-指数")),
            "yoy": _safe_float(row.get("制造业-同比增长")),
            "extra": {
                "非制造业-指数": _safe_float(row.get("非制造业-指数")),
                "非制造业-同比增长": _safe_float(row.get("非制造业-同比增长")),
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
            "value": _safe_float(row.get("全国-当月")),
            "yoy": _safe_float(row.get("全国-同比增长")),
            "mom": _safe_float(row.get("全国-环比增长")),
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
            "value": _safe_float(row.get("当月")),
            "yoy": _safe_float(row.get("当月同比增长")),
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
            "value": _safe_float(row.get("国内生产总值-绝对值")),
            "yoy": _safe_float(row.get("国内生产总值-同比增长")),
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
            "value": _safe_float(row.get("货币和准货币(M2)-数量(亿元)")),
            "yoy": _safe_float(row.get("货币和准货币(M2)-同比增长")),
            "mom": _safe_float(row.get("货币和准货币(M2)-环比增长")),
        })
    return records


def _fetch_indicator_social_finance():
    import akshare as ak
    df = ak.macro_china_shrzgm()
    if df is None or df.empty:
        return None
    # 社融 is already oldest-first, no reversal needed
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": _safe_float(row.get("社会融资规模增量")),
        })
    return records


def _fetch_indicator_lpr():
    import akshare as ak
    df = ak.macro_china_lpr()
    if df is None or df.empty:
        return None
    # LPR is oldest first already, skip None values
    records = []
    for _, row in df.iterrows():
        value = _safe_float(row.get("LPR1Y"))
        if value is None:
            continue
        records.append({
            "period": str(row.get("TRADE_DATE", "")).strip(),
            "value": value,
            "extra": {
                "LPR5Y": _safe_float(row.get("LPR5Y")),
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


def _trend_label(records: list[dict]) -> str:
    """Determine trend from last 3 records.

    Tries yoy first, then falls back to value.
    Skips None values.
    """
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
    }


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

    # 1. 从数据库读取
    db = _get_db()
    records = db.get_macro_indicator(indicator, limit=months)
    if records is not None:
        records = records[-months:] if len(records) > months else records

    if records:
        trend = _trend_label(records)
        lock = _get_lock()
        if lock.acquire(blocking=False):
            def _bg_refresh():
                try:
                    new_records = INDICATOR_FETCHERS[indicator]()
                    if new_records:
                        db.save_macro_indicator(indicator, new_records)
                except Exception as e:
                    logger.warning(f"[Macro-指标] 后台刷新失败: {e}")
                finally:
                    lock.release()
            threading.Thread(target=_bg_refresh, daemon=True).start()
        return _build_indicator_response(indicator, records, trend, cached=True)

    # 2. 数据库为空，同步拉取 + 入库
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
