# -*- coding: utf-8 -*-
"""Core financial indicators fetcher (THS + Sina fallback)."""
from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol,
    _safe_float,
    _safe_amount,
    _safe_str,
    _safe_pct,
)
from ._cache import _cache_get, _cache_put, CACHE_KEY
from ._fetch_statements_fallback import _enrich_financial_items_with_statements

# Late-bound reference for test monkey-patching compatibility
_pkg = sys.modules[__package__]


def _fetch_from_ths(symbol: str, periods: int) -> list[dict]:
    """Fetch from 同花顺 (stock_financial_abstract_new_ths).

    The new API returns a long table (one row per metric) with English
    ``metric_name`` keys and already provides single-quarter values via the
    ``single`` column plus YoY ratios, so no manual cumulative→single-quarter
    diffing is needed (unlike the deprecated stock_financial_abstract_ths).
    """
    import akshare as ak

    df = ak.stock_financial_abstract_new_ths(symbol=symbol, indicator="按报告期")
    if df is None or df.empty:
        raise ValueError("同花顺返回空数据")

    # metric_name → FinancialItem field
    metric_map = {
        "operating_income_total": "revenue",
        "parent_holder_net_profit": "net_profit",
        "index_deduct_holder_net_profit": "deducted_profit",
        "basic_eps": "eps",
        "calc_per_net_assets": "bps",
        "sale_net_interest_ratio": "net_margin",
        "sale_gross_margin": "gross_margin",
        "index_weighted_avg_roe": "roe",
        "index_full_diluted_roe": "roe_diluted",
        "current_ratio": "current_ratio",
        "quick_ratio": "quick_ratio",
        "assets_debt_ratio": "debt_ratio",
        "calculate_operating_income_total_yoy_growth_ratio": "revenue_yoy",
        "calculate_parent_holder_net_profit_yoy_growth_ratio": "net_profit_yoy",
        "deduct_net_profit_yoy_growth_ratio": "deducted_profit_yoy",
    }
    flow_fields = {"revenue", "net_profit", "deducted_profit", "eps"}
    yoy_fields = {"revenue_yoy", "net_profit_yoy", "deducted_profit_yoy"}

    # Pivot long → wide: one dict per report_date.
    by_date: dict[str, dict] = {}
    for _, row in df.iterrows():
        metric = str(row.get("metric_name", "")).strip()
        dst = metric_map.get(metric)
        if not dst:
            continue
        date = str(row.get("report_date", "")).strip()
        if not date:
            continue
        item = by_date.setdefault(date, {"report_date": date})

        if dst in flow_fields:
            # prefer single-quarter value; fall back to cumulative value
            val = _safe_float(row.get("single"))
            if val is None:
                val = _safe_amount(row.get("value"))
            item[dst] = val
        elif dst in yoy_fields:
            # value/single already hold the yoy as a percent (-46.58 == -46.58%);
            # the yoy/single_yoy columns are a different (decimal) ratio, not the
            # direct growth rate, so do not use them here.
            item[dst] = _safe_float(row.get("value"))
        else:
            item[dst] = _safe_float(row.get("value"))

    items = sorted(by_date.values(), key=lambda x: x.get("report_date", ""))
    return items[-periods:] if periods else items


# Mapping: (选项, 指标) → response field
_SINA_INDICATOR_MAP = {
    ("常用指标", "营业总收入"): "revenue",
    ("常用指标", "净利润"): "net_profit",
    ("常用指标", "扣非净利润"): "deducted_profit",
    ("常用指标", "基本每股收益"): "eps",
    ("常用指标", "每股净资产"): "bps",
    ("常用指标", "净资产收益率(ROE)"): "roe",
    ("常用指标", "毛利率"): "gross_margin",
    ("常用指标", "销售净利率"): "net_margin",
    ("常用指标", "资产负债率"): "debt_ratio",
    ("盈利能力", "净资产收益率(ROE)"): "roe",
    ("盈利能力", "摊薄净资产收益率"): "roe_diluted",
    ("成长能力", "营业总收入增长率"): "revenue_yoy",
    ("成长能力", "归属母公司净利润增长率"): "net_profit_yoy",
    ("财务风险", "流动比率"): "current_ratio",
    ("财务风险", "速动比率"): "quick_ratio",
    ("财务风险", "资产负债率"): "debt_ratio",
    ("每股指标", "基本每股收益"): "eps",
    ("每股指标", "每股净资产_最新股数"): "bps",
}


def _fetch_from_sina(symbol: str, periods: int) -> list[dict]:
    """Fetch from 新浪财经. Data is already single-quarter."""
    import akshare as ak
    import pandas as pd

    df = ak.stock_financial_abstract(symbol=symbol)
    if df is None or df.empty:
        raise ValueError("新浪返回空数据")

    # Build lookup: (选项, 指标) → {date_col: value}
    lookup: dict[tuple, dict[str, any]] = {}
    for _, row in df.iterrows():
        key = (str(row["选项"]), str(row["指标"]))
        lookup[key] = {str(k): v for k, v in row.items() if k not in ("选项", "指标") and pd.notna(v)}

    # Collect all date columns, sort, take last `periods+1` (extra for diff)
    all_dates = sorted(
        {k for v in lookup.values() for k in v},
        reverse=True,
    )[: periods + 1]
    all_dates = sorted(all_dates)  # chronological

    # Parse all cumulative items, then diff
    all_items = []
    for date_col in all_dates:
        d = date_col.strip()
        if len(d) == 8:
            d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"

        item: dict = {"report_date": d}
        for (opt, ind), dst in _SINA_INDICATOR_MAP.items():
            if dst in item:
                continue
            row_data = lookup.get((opt, ind), {})
            val = row_data.get(date_col)
            if val is not None:
                s = str(val).strip()
                if s in ("", "--", "nan", "None"):
                    continue
                if dst in (
                    "net_margin",
                    "gross_margin",
                    "roe",
                    "roe_diluted",
                    "debt_ratio",
                    "net_profit_yoy",
                    "revenue_yoy",
                ):
                    item[dst] = _safe_pct(s)
                elif dst in ("revenue", "net_profit", "deducted_profit"):
                    item[dst] = _safe_amount(s)
                else:
                    item[dst] = _safe_float(s)

        has_data = any(v is not None for k, v in item.items() if k != "report_date")
        if has_data:
            all_items.append(item)

    # Diff cumulative amounts → single-quarter
    items = []
    for i, cur in enumerate(all_items):
        is_q1 = (cur.get("report_date") or "").endswith("03-31")
        if i == 0 or is_q1:
            items.append(cur)
            continue
        prev = all_items[i - 1]
        single = dict(cur)
        for field in ("revenue", "net_profit", "deducted_profit"):
            cv = cur.get(field)
            pv = prev.get(field)
            if cv is not None and pv is not None:
                single[field] = cv - pv
        items.append(single)

    return items[-periods:]


def _fetch_financials(symbol: str, periods: int = 12) -> dict:
    """Fetch core financial indicators with multi-source fallback."""
    import time as _time

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    result: dict = {
        "symbol": code,
        "periods": periods,
        "items": [],
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }
    errors: list[str] = []

    # Source 1: 同花顺
    try:
        items = _pkg._fetch_from_ths(code, periods)
        if items:
            try:
                _enrich_financial_items_with_statements(code, items)
            except Exception as enrich_exc:
                errors.append(f"财报明细补充: {enrich_exc}")
                logger.warning(f"[Financials] statement enrichment failed for {symbol}: {enrich_exc}")
            result["items"] = items
            result["source"] = "同花顺"
            if errors:
                result["_errors"] = errors
            logger.info(f"[Financials] 同花顺 OK for {symbol}: {len(items)} periods, {_time.time() - t0:.1f}s")
            return result
        errors.append("同花顺返回空数据")
    except Exception as e:
        errors.append(f"同花顺: {e}")
        logger.warning(f"[Financials] 同花顺 failed for {symbol}: {e}")

    # Source 2: 新浪财经
    try:
        items = _pkg._fetch_from_sina(code, periods)
        if items:
            try:
                _enrich_financial_items_with_statements(code, items)
            except Exception as enrich_exc:
                errors.append(f"财报明细补充: {enrich_exc}")
                logger.warning(f"[Financials] statement enrichment failed for {symbol}: {enrich_exc}")
            result["items"] = items
            result["source"] = "新浪财经"
            if errors:
                result["_errors"] = errors
            logger.info(f"[Financials] 新浪 OK for {symbol}: {len(items)} periods, {_time.time() - t0:.1f}s")
            return result
        errors.append("新浪返回空数据")
    except Exception as e:
        errors.append(f"新浪: {e}")
        logger.warning(f"[Financials] 新浪 failed for {symbol}: {e}")

    logger.warning(f"[Financials] all sources failed for {symbol}: {'; '.join(errors)}")
    return result
