# -*- coding: utf-8 -*-
"""Valuation ratios: data fetching + entry point.

Calculation helpers live in ``_calc``, signal construction in ``_signal``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from api.v1.endpoints.financials._symbol import (
    _normalize_symbol, _to_em_symbol, _safe_float, _safe_str,
    _pick_col, _row_pick, _parse_date,
)
from api.v1.endpoints.financials._calc import (
    _calc_pe_percentiles, _calc_pe_percentiles_from_em, _calc_dividend_yield,
)
from api.v1.endpoints.financials._signal import _build_price_overdraft_signal

logger = logging.getLogger(__name__)


def _fetch_lg_valuation(symbol: str):
    """乐咕单股估值历史接口。

    akshare 1.18.55 已移除 ``stock_a_lg_indicator`` / ``stock_a_indicator_lg``，
    此处保留入口但不再探测，估值历史分位统一由 ``stock_value_em`` 路径提供。
    """
    return None, None


def _fetch_em_valuation_history(symbol: str):
    import akshare as ak
    import pandas as pd

    try:
        df = ak.stock_value_em(symbol=_normalize_symbol(symbol))
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df, "stock_value_em"
    except Exception as exc:
        logger.warning(f"[Valuation] stock_value_em failed for {symbol}: {exc}")
    return None, None


def _extract_em_valuation_latest(df) -> dict:
    if df is None or df.empty:
        return {}
    work_df = df.copy()
    date_col = "数据日期" if "数据日期" in work_df.columns else _pick_col(work_df.columns, ["日期", "date"])
    if date_col is not None:
        work_df["_parsed_date"] = work_df[date_col].map(_parse_date)
        work_df = work_df.sort_values("_parsed_date")
    row = work_df.iloc[-1]
    return {
        "trade_date": (
            row.get("_parsed_date").date().isoformat()
            if row.get("_parsed_date") is not None
            else _safe_str(row.get(date_col)) if date_col is not None else None
        ),
        "pe_static": _safe_float(row.get("PE(静)")),
        "pe_ttm": _safe_float(row.get("PE(TTM)")),
        "pb": _safe_float(row.get("市净率")),
        "ps": _safe_float(row.get("市销率")),
        "pcf": _safe_float(row.get("市现率")),
        "peg": _safe_float(row.get("PEG值")),
        "_latest_close": _safe_float(row.get("当日收盘价")),
    }


def _extract_valuation_latest(df) -> dict:
    if df is None or df.empty:
        return {}
    work_df = df.copy()
    date_col = _pick_col(work_df.columns, ["日期", "date", "trade"])
    if date_col is not None:
        work_df["_parsed_date"] = work_df[date_col].map(_parse_date)
        work_df = work_df.sort_values("_parsed_date")
    row = work_df.iloc[-1]

    pe_ttm = _safe_float(_row_pick(row, ["pe_ttm", "市盈率ttm", "滚动市盈率", "pe-ttm"]))
    pe_dynamic = _safe_float(_row_pick(row, ["市盈率-动态", "动态市盈率", "pe_dynamic", "动"]))
    pe_static = _safe_float(_row_pick(row, ["静态市盈率", "市盈率", "pe"], exclude=["ttm", "动态", "分位"]))
    return {
        "trade_date": (
            row.get("_parsed_date").date().isoformat()
            if row.get("_parsed_date") is not None
            else _safe_str(_row_pick(row, ["日期", "date", "trade"])) or None
        ),
        "pe_static": pe_static,
        "pe_dynamic": pe_dynamic,
        "pe_ttm": pe_ttm or pe_static or pe_dynamic,
        "pb": _safe_float(_row_pick(row, ["市净率", "pb"])),
        "ps": _safe_float(_row_pick(row, ["市销率", "ps"])),
        "pcf": _safe_float(_row_pick(row, ["市现率", "pcf", "现金流"])),
        "peg": _safe_float(_row_pick(row, ["peg"])),
        "dividend_yield": _safe_float(_row_pick(row, ["股息率", "dv_ratio", "dividend"])),
    }


def _fill_valuation_from_em(symbol: str, payload: dict) -> tuple[dict, Optional[str]]:
    import akshare as ak

    try:
        df = ak.stock_individual_info_em(symbol=_normalize_symbol(symbol), timeout=10)
        if df is None or df.empty:
            return payload, None
        info_map = {str(row.get("item", "")): row.get("value") for _, row in df.iterrows()}
        payload["industry"] = payload.get("industry") or _safe_str(info_map.get("行业")) or None
        payload["pe_dynamic"] = payload.get("pe_dynamic") or _safe_float(info_map.get("市盈率-动态"))
        payload["pe_static"] = payload.get("pe_static") or _safe_float(info_map.get("市盈率-静态"))
        payload["pb"] = payload.get("pb") or _safe_float(info_map.get("市净率"))
        return payload, "stock_individual_info_em"
    except Exception as exc:
        logger.warning(f"[Valuation] EM fallback failed for {symbol}: {exc}")
        return payload, None


def _fill_valuation_comparison(symbol: str, payload: dict) -> tuple[dict, Optional[str]]:
    try:
        import akshare as ak

        df = ak.stock_zh_valuation_comparison_em(symbol=_to_em_symbol(_normalize_symbol(symbol)))
        if df is None or df.empty or "代码" not in df.columns:
            return payload, None

        matched = df[df["代码"].astype(str).map(_normalize_symbol) == _normalize_symbol(symbol)]
        if not matched.empty:
            stock_row = matched.iloc[0]
            payload["pe_ttm"] = payload.get("pe_ttm") or _safe_float(stock_row.get("市盈率-TTM"))
            current_year_col = f"市盈率-{str(datetime.now().year)[-2:]}E"
            forecast_pe = _safe_float(stock_row.get(current_year_col))
            if forecast_pe is None:
                forecast_col = next((col for col in stock_row.index if str(col).startswith("市盈率-") and str(col).endswith("E")), None)
                forecast_pe = _safe_float(stock_row.get(forecast_col)) if forecast_col else None
            payload["pe_dynamic"] = payload.get("pe_dynamic") or forecast_pe
            payload["pb"] = payload.get("pb") or _safe_float(stock_row.get("市净率-MRQ"))
            payload["ps"] = payload.get("ps") or _safe_float(stock_row.get("市销率-TTM"))
            payload["pcf"] = payload.get("pcf") or _safe_float(stock_row.get("市现率1-TTM"))
            payload["peg"] = payload.get("peg") or _safe_float(stock_row.get("PEG"))

        avg_df = df[df["代码"].astype(str) == "行业平均"]
        if not avg_df.empty:
            avg_row = avg_df.iloc[0]
            payload["industry_average"] = {
                "industry": "行业平均",
                "pe": _safe_float(avg_row.get("市盈率-TTM")),
                "pb": _safe_float(avg_row.get("市净率-MRQ")),
                "sample_size": max(0, len(df) - 3),
            }
        return payload, "stock_zh_valuation_comparison_em"
    except Exception as exc:
        logger.warning(f"[Valuation] comparison failed for {symbol}: {exc}")
        return payload, None


def _fill_industry_average(payload: dict) -> tuple[dict, Optional[str]]:
    industry = payload.get("industry")
    if not industry:
        return payload, None
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_board_industry_cons_em(symbol=industry)
        if df is None or df.empty:
            return payload, None
        pe_col = _pick_col(df.columns, ["市盈率-动态", "动态市盈率", "市盈率", "pe"])
        pb_col = _pick_col(df.columns, ["市净率", "pb"])
        pe_values = pd.to_numeric(df[pe_col], errors="coerce").dropna() if pe_col is not None else pd.Series(dtype=float)
        pb_values = pd.to_numeric(df[pb_col], errors="coerce").dropna() if pb_col is not None else pd.Series(dtype=float)
        payload["industry_average"] = {
            "industry": industry,
            "pe": round(float(pe_values.mean()), 2) if not pe_values.empty else None,
            "pb": round(float(pb_values.mean()), 2) if not pb_values.empty else None,
            "sample_size": int(max(len(pe_values), len(pb_values), len(df))),
        }
        return payload, "stock_board_industry_cons_em"
    except Exception as exc:
        logger.warning(f"[Valuation] industry average failed for {industry}: {exc}")
        return payload, None


def _fetch_valuation_ratios(symbol: str, with_history: bool = True) -> dict:
    code = _normalize_symbol(symbol)
    result: dict = {
        "symbol": code,
        "trade_date": None,
        "pe_static": None,
        "pe_dynamic": None,
        "pe_ttm": None,
        "pb": None,
        "ps": None,
        "pcf": None,
        "peg": None,
        "dividend_yield": None,
        "dividend_date": None,
        "pe_percentiles": {},
        "industry_average": {"industry": None, "pe": None, "pb": None, "sample_size": 0},
        "price_overdraft_signal": {},
        "source_chain": [],
        "errors": [],
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }
    latest_close = None
    em_df, value_source = _fetch_em_valuation_history(code)
    if em_df is not None:
        value_payload = _extract_em_valuation_latest(em_df)
        latest_close = value_payload.pop("_latest_close", None)
        result.update({k: v for k, v in value_payload.items() if v is not None})
        result["source_chain"].append(value_source)
        if with_history:
            result["pe_percentiles"] = _calc_pe_percentiles_from_em(em_df, result.get("pe_ttm"))
    else:
        df, source = _fetch_lg_valuation(code)
        if df is not None:
            result.update({k: v for k, v in _extract_valuation_latest(df).items() if v is not None})
            result["source_chain"].append(source)
            if with_history:
                result["pe_percentiles"] = _calc_pe_percentiles(df, result.get("pe_ttm"))
        else:
            result["errors"].append("valuation_history_unavailable")

    result, em_source = _fill_valuation_from_em(code, result)
    if em_source:
        result["source_chain"].append(em_source)
    result, comparison_source = _fill_valuation_comparison(code, result)
    if comparison_source:
        result["source_chain"].append(comparison_source)
    if not result.get("industry_average", {}).get("pe"):
        result["industry_average"]["industry"] = result.get("industry")
        result, industry_source = _fill_industry_average(result)
        if industry_source:
            result["source_chain"].append(industry_source)
    dividend_yield, dividend_date, dividend_source = _calc_dividend_yield(code, latest_close)
    if dividend_yield is not None:
        result["dividend_yield"] = dividend_yield
        result["dividend_date"] = dividend_date
    if dividend_source:
        result["source_chain"].append(dividend_source)
    result["price_overdraft_signal"] = _build_price_overdraft_signal(result)
    return result