# -*- coding: utf-8 -*-
"""Shareholder structure: holder count, top-10, changes, actual controller."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol, _to_em_symbol, _to_ts_code, _to_top_holder_symbol,
    _safe_float, _safe_str, _safe_pct, _safe_int_like, _parse_chinese_share_amount,
    _pick_col, _row_pick, _parse_date, _latest_quarter_dates,
)
from ._cache import _daily_cache_get, _daily_cache_put, SHAREHOLDER_CACHE_KEY

def _fetch_holder_count_from_akshare(symbol: str) -> tuple[dict, Optional[str], list[str]]:
    import akshare as ak

    code = _normalize_symbol(symbol)
    errors: list[str] = []
    try:
        df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
        if df is None or df.empty:
            return {}, None, ["stock_zh_a_gdhs_detail_em:empty"]
        date_col = "股东户数统计截止日" if "股东户数统计截止日" in df.columns else _pick_col(df.columns, ["日期", "截止", "报告期"])
        count_col = "股东户数-本次" if "股东户数-本次" in df.columns else _pick_col(df.columns, ["股东户数", "股东人数", "户数"], exclude=["日期", "截止", "统计"])
        prev_count_col = "股东户数-上次" if "股东户数-上次" in df.columns else None
        change_count_col = "股东户数-增减" if "股东户数-增减" in df.columns else None
        change_col = "股东户数-增减比例" if "股东户数-增减比例" in df.columns else _pick_col(df.columns, ["较上期变化", "环比", "增减比例", "变化比例"])
        work_df = df.copy()
        if date_col is not None:
            work_df["_date"] = work_df[date_col].map(_parse_date)
            work_df = work_df.sort_values("_date")
        latest = work_df.iloc[-1]
        prev = work_df.iloc[-2] if len(work_df) > 1 else None
        count = _safe_int_like(latest.get(count_col)) if count_col is not None else None
        prev_count = (
            _safe_int_like(latest.get(prev_count_col))
            if prev_count_col is not None
            else _safe_int_like(prev.get(count_col)) if prev is not None and count_col is not None else None
        )
        change_count = _safe_int_like(latest.get(change_count_col)) if change_count_col is not None else None
        change_pct = _safe_pct(latest.get(change_col)) if change_col is not None else None
        if change_pct is None and count is not None and prev_count:
            change_pct = round((count - prev_count) / prev_count * 100, 2)
        return {
            "holder_count": count,
            "holder_count_previous": prev_count,
            "holder_count_change": (
                change_count
                if change_count is not None
                else count - prev_count if count is not None and prev_count is not None else None
            ),
            "holder_count_change_pct": change_pct,
            "holder_report_date": (
                latest.get("_date").date().isoformat()
                if latest.get("_date") is not None
                else _safe_str(latest.get(date_col)) if date_col is not None else None
            ),
        }, "stock_zh_a_gdhs_detail_em", errors
    except Exception as exc:
        errors.append(f"stock_zh_a_gdhs_detail_em:{type(exc).__name__}")
        logger.warning(f"[Shareholder] gdhs detail failed for {code}: {exc}")
    return {}, None, errors


def _safe_int_like(val) -> Optional[int]:
    num = _safe_float(val)
    return int(num) if num is not None else None


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


def _fetch_holder_count_from_tushare(symbol: str) -> tuple[dict, Optional[str], list[str]]:
    import os

    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        return {}, None, []
    try:
        import tushare as ts

        pro = ts.pro_api(token)
        df = pro.stk_holdernumber(ts_code=_to_ts_code(symbol))
        if df is None or df.empty:
            return {}, None, ["tushare.stk_holdernumber:empty"]
        date_col = _pick_col(df.columns, ["ann_date", "end_date", "日期", "截止"])
        count_col = _pick_col(df.columns, ["holder_num", "股东户数", "股东人数"])
        work_df = df.copy()
        if date_col is not None:
            work_df["_date"] = work_df[date_col].map(_parse_date)
            work_df = work_df.sort_values("_date")
        latest = work_df.iloc[-1]
        prev = work_df.iloc[-2] if len(work_df) > 1 else None
        count = _safe_int_like(latest.get(count_col)) if count_col is not None else None
        prev_count = _safe_int_like(prev.get(count_col)) if prev is not None and count_col is not None else None
        return {
            "holder_count": count,
            "holder_count_previous": prev_count,
            "holder_count_change": count - prev_count if count is not None and prev_count is not None else None,
            "holder_count_change_pct": (
                round((count - prev_count) / prev_count * 100, 2)
                if count is not None and prev_count else None
            ),
            "holder_report_date": (
                latest.get("_date").date().isoformat()
                if latest.get("_date") is not None
                else _safe_str(latest.get(date_col)) if date_col is not None else None
            ),
        }, "tushare.stk_holdernumber", []
    except Exception as exc:
        logger.warning(f"[Shareholder] tushare holdernumber failed for {symbol}: {exc}")
        return {}, None, [f"tushare.stk_holdernumber:{type(exc).__name__}"]


def _fetch_top10_holders(symbol: str) -> tuple[list[dict], Optional[float], Optional[str], list[str]]:
    import akshare as ak

    errors: list[str] = []
    for date in _latest_quarter_dates(limit=10):
        try:
            df = ak.stock_gdfx_top_10_em(symbol=_to_top_holder_symbol(symbol), date=date)
            if df is None or df.empty:
                continue
            name_col = _pick_col(df.columns, ["股东名称", "名称"])
            pct_col = _pick_col(df.columns, ["持股比例", "占总股本", "比例"])
            amount_col = _pick_col(df.columns, ["持股数量", "持股数", "数量"])
            nature_col = _pick_col(df.columns, ["股东性质", "性质", "类型"])
            change_col = _pick_col(df.columns, ["增减", "变动", "变化"])
            holders: list[dict] = []
            institution_pct = 0.0
            for _, row in df.head(10).iterrows():
                name = _safe_str(row.get(name_col)) if name_col is not None else ""
                nature = _safe_str(row.get(nature_col)) if nature_col is not None else ""
                pct = _safe_pct(row.get(pct_col)) if pct_col is not None else None
                institution_keywords = (
                    "公司", "基金", "银行", "保险", "社保", "QFII", "券商", "信托",
                    "法人", "国有", "机构", "结算", "汇金", "证券金融", "私募"
                )
                if pct is not None and any(k in f"{name}{nature}" for k in institution_keywords):
                    institution_pct += pct
                holders.append({
                    "name": name,
                    "holding_pct": pct,
                    "holding_amount": _safe_float(row.get(amount_col)) if amount_col is not None else None,
                    "holder_type": nature or None,
                    "change": _safe_str(row.get(change_col)) if change_col is not None else None,
                })
            return holders, round(institution_pct, 2), f"stock_gdfx_top_10_em:{date}", errors
        except Exception as exc:
            errors.append(f"stock_gdfx_top_10_em:{date}:{type(exc).__name__}")
            continue
    return [], None, None, errors


def _fetch_holder_changes(symbol: str) -> tuple[list[dict], Optional[str], list[str]]:
    import akshare as ak

    code = _normalize_symbol(symbol)
    errors: list[str] = []
    candidates = [
        ("stock_shareholder_change_ths", {"symbol": code}),
        ("stock_hold_management_detail_em", {}),
    ]
    for func_name, kwargs in candidates:
        fn = getattr(ak, func_name, None)
        if fn is None:
            continue
        try:
            df = fn(**kwargs)
            if df is None or df.empty:
                continue
            code_col = _pick_col(df.columns, ["代码", "证券代码", "股票代码"])
            if code_col is not None:
                df = df[df[code_col].astype(str).map(_normalize_symbol) == code]
            if df.empty:
                continue
            date_col = _pick_col(df.columns, ["变动日期", "公告日期", "日期"])
            holder_col = "变动股东" if "变动股东" in df.columns else _pick_col(df.columns, ["股东名称", "名称", "变动人"])
            direction_col = _pick_col(df.columns, ["变动方向", "增减", "类型", "方向"])
            shares_col = "变动数量" if "变动数量" in df.columns else _pick_col(df.columns, ["变动数量", "变动股数", "数量"])
            pct_col = _pick_col(df.columns, ["变动比例", "占总股本", "比例"])
            price_col = "交易均价" if "交易均价" in df.columns else _pick_col(df.columns, ["均价", "价格"])
            work_df = df.copy()
            if date_col is not None:
                work_df["_date"] = work_df[date_col].map(_parse_date)
                work_df = work_df.sort_values("_date", ascending=False)
            records = []
            for _, row in work_df.head(8).iterrows():
                raw_change = _safe_str(row.get(shares_col)) if shares_col is not None else ""
                direction = _safe_str(row.get(direction_col)) if direction_col is not None else None
                if not direction and raw_change:
                    if "增持" in raw_change:
                        direction = "增持"
                    elif "减持" in raw_change:
                        direction = "减持"
                records.append({
                    "date": (
                        row.get("_date").date().isoformat()
                        if row.get("_date") is not None
                        else _safe_str(row.get(date_col)) if date_col is not None else None
                    ),
                    "holder": _safe_str(row.get(holder_col)) if holder_col is not None else "",
                    "direction": direction,
                    "shares": _parse_chinese_share_amount(raw_change) if raw_change else None,
                    "pct": _safe_pct(row.get(pct_col)) if pct_col is not None else None,
                    "price": _safe_float(row.get(price_col)) if price_col is not None else None,
                })
            return records, func_name, errors
        except Exception as exc:
            errors.append(f"{func_name}:{type(exc).__name__}")
            logger.warning(f"[Shareholder] holder changes {func_name} failed for {code}: {exc}")
    return [], None, errors


def _fetch_actual_controller(symbol: str) -> tuple[Optional[str], Optional[str], list[str]]:
    import akshare as ak

    code = _normalize_symbol(symbol)
    errors: list[str] = []
    try:
        df = ak.stock_hold_control_cninfo(symbol="全部")
        if df is None or df.empty:
            return None, None, ["stock_hold_control_cninfo:empty"]
        code_col = _pick_col(df.columns, ["代码", "证券代码", "股票代码"])
        matched = df
        if code_col is not None:
            matched = df[df[code_col].astype(str).map(_normalize_symbol) == code]
        if matched.empty:
            return None, None, []
        row = matched.iloc[0]
        controller = _safe_str(_row_pick(row, ["实际控制人", "控制人", "控股股东"]))
        return controller or None, "stock_hold_control_cninfo", errors
    except Exception as exc:
        logger.warning(f"[Shareholder] actual controller failed for {code}: {exc}")
        return None, None, [f"stock_hold_control_cninfo:{type(exc).__name__}"]


def _fetch_shareholder_structure(symbol: str) -> dict:
    code = _normalize_symbol(symbol)
    result: dict = {
        "symbol": code,
        "holder_count": None,
        "holder_count_previous": None,
        "holder_count_change": None,
        "holder_count_change_pct": None,
        "holder_report_date": None,
        "top10_holders": [],
        "institution_holding_pct": None,
        "major_holder_changes": [],
        "actual_controller": None,
        "source_chain": [],
        "errors": [],
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }

    holder_payload, holder_source, holder_errors = _fetch_holder_count_from_akshare(code)
    if not holder_payload:
        holder_payload, holder_source, tushare_errors = _fetch_holder_count_from_tushare(code)
        holder_errors.extend(tushare_errors)
    result.update(holder_payload)
    result["errors"].extend(holder_errors)
    if holder_source:
        result["source_chain"].append(holder_source)

    holders, institution_pct, top_source, top_errors = _fetch_top10_holders(code)
    result["top10_holders"] = holders
    result["institution_holding_pct"] = institution_pct
    result["errors"].extend(top_errors)
    if top_source:
        result["source_chain"].append(top_source)

    changes, changes_source, changes_errors = _fetch_holder_changes(code)
    result["major_holder_changes"] = changes
    result["errors"].extend(changes_errors)
    if changes_source:
        result["source_chain"].append(changes_source)

    controller, controller_source, controller_errors = _fetch_actual_controller(code)
    result["actual_controller"] = controller
    result["errors"].extend(controller_errors)
    if controller_source:
        result["source_chain"].append(controller_source)
    return result
