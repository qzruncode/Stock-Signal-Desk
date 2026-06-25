# -*- coding: utf-8 -*-
"""Valuation ratios, PE percentiles, overdraft signal."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol, _to_em_symbol, _safe_float, _safe_str, _safe_pct,
    _pick_col, _row_pick, _parse_date, _clamp,
)
from ._cache import _daily_cache_get, _daily_cache_put, VALUATION_CACHE_KEY

def _fetch_lg_valuation(symbol: str):
    import akshare as ak
    import pandas as pd

    for func_name in ("stock_a_lg_indicator", "stock_a_indicator_lg"):
        fn = getattr(ak, func_name, None)
        if fn is None:
            continue
        try:
            df = fn(symbol=_normalize_symbol(symbol))
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df, func_name
        except Exception as exc:
            logger.warning(f"[Valuation] {func_name} failed for {symbol}: {exc}")
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


def _calc_dividend_yield(symbol: str, latest_close: Optional[float]) -> tuple[Optional[float], Optional[str], Optional[str]]:
    if not latest_close or latest_close <= 0:
        return None, None, None
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_fhps_detail_em(symbol=_normalize_symbol(symbol))
        if df is None or df.empty:
            return None, None, None
        date_col = "除权除息日" if "除权除息日" in df.columns else _pick_col(df.columns, ["除权", "日期"])
        cash_col = "现金分红-现金分红比例" if "现金分红-现金分红比例" in df.columns else _pick_col(df.columns, ["现金分红", "派息"])
        if cash_col is None:
            return None, None, None
        work_df = df.copy()
        if date_col is not None:
            work_df["_date"] = pd.to_datetime(work_df[date_col], errors="coerce")
        if "方案进度" in work_df.columns:
            work_df = work_df[work_df["方案进度"].astype(str).str.contains("实施", na=False)]
        # 取最近一次实施的分红
        if date_col is not None and not work_df.empty:
            work_df = work_df.sort_values("_date", ascending=False)
        cash_per_10 = pd.to_numeric(work_df[cash_col].iloc[0], errors="coerce") if not work_df.empty else None
        if cash_per_10 is None or pd.isna(cash_per_10):
            return None, None, None
        cash_per_share = float(cash_per_10) / 10.0
        latest_date = work_df["_date"].iloc[0] if date_col is not None else None
        dividend_date = latest_date.strftime("%Y-%m-%d") if latest_date is not None and not pd.isna(latest_date) else None
        return round(cash_per_share / latest_close * 100, 2), dividend_date, "stock_fhps_detail_em"
    except Exception as exc:
        logger.warning(f"[Valuation] dividend yield failed for {symbol}: {exc}")
        return None, None, None


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


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _build_price_overdraft_signal(payload: dict) -> dict:
    pe_ttm = _safe_float(payload.get("pe_ttm"))
    pe_dynamic = _safe_float(payload.get("pe_dynamic"))
    pb = _safe_float(payload.get("pb"))
    peg = _safe_float(payload.get("peg"))
    dividend_yield = _safe_float(payload.get("dividend_yield"))
    pe_percentiles = payload.get("pe_percentiles") or {}
    industry_average = payload.get("industry_average") or {}
    industry_pe = _safe_float(industry_average.get("pe"))
    industry_pb = _safe_float(industry_average.get("pb"))

    pe_1y = _safe_float(pe_percentiles.get("1y"))
    pe_3y = _safe_float(pe_percentiles.get("3y"))
    pe_5y = _safe_float(pe_percentiles.get("5y"))

    pe_premium_vs_industry = (
        round((pe_ttm - industry_pe) / industry_pe * 100, 2)
        if pe_ttm is not None and industry_pe not in (None, 0)
        else None
    )
    pb_premium_vs_industry = (
        round((pb - industry_pb) / industry_pb * 100, 2)
        if pb is not None and industry_pb not in (None, 0)
        else None
    )
    dynamic_pe_discount_vs_ttm = (
        round((pe_ttm - pe_dynamic) / pe_ttm * 100, 2)
        if pe_ttm not in (None, 0) and pe_dynamic is not None
        else None
    )

    expensive_score = 0.0
    expectation_support_score = 50.0
    signals: list[str] = []
    reasoning: list[str] = []
    limitations: list[str] = []

    percentile_anchor = max(v for v in (pe_1y, pe_3y, pe_5y) if v is not None) if any(
        v is not None for v in (pe_1y, pe_3y, pe_5y)
    ) else None
    if percentile_anchor is not None:
        expensive_score += _clamp((percentile_anchor - 50) * 0.7, 0, 35)
        if percentile_anchor >= 90:
            signals.append("pe_percentile_extremely_high")
            reasoning.append(f"PE 历史分位处于高位（最高分位 {percentile_anchor:.2f}%），说明当前定价接近历史偏贵区间。")
        elif percentile_anchor >= 75:
            signals.append("pe_percentile_high")
            reasoning.append(f"PE 历史分位偏高（最高分位 {percentile_anchor:.2f}%），估值安全边际正在收窄。")
    else:
        limitations.append("缺少足够的历史 PE 分位数据，无法完整评估当前估值所处区间。")

    if pe_premium_vs_industry is not None:
        expensive_score += _clamp(pe_premium_vs_industry * 0.25, 0, 25)
        if pe_premium_vs_industry >= 40:
            signals.append("pe_premium_vs_industry_high")
            reasoning.append(f"当前 PE 相对行业平均溢价 {pe_premium_vs_industry:.2f}%，市场已经计入更高成长预期。")
        elif pe_premium_vs_industry <= -15:
            signals.append("pe_discount_vs_industry")
            reasoning.append(f"当前 PE 低于行业平均 {abs(pe_premium_vs_industry):.2f}%，纯估值层面的透支压力有限。")
    else:
        limitations.append("缺少可比行业 PE，行业相对估值判断不完整。")

    if pb_premium_vs_industry is not None:
        expensive_score += _clamp(pb_premium_vs_industry * 0.12, 0, 10)
        if pb_premium_vs_industry >= 35:
            signals.append("pb_premium_vs_industry_high")
            reasoning.append(f"PB 相对行业也存在 {pb_premium_vs_industry:.2f}% 溢价，说明高定价不只体现在盈利倍数。")
    else:
        limitations.append("缺少可比行业 PB，资产端估值溢价无法充分验证。")

    if dynamic_pe_discount_vs_ttm is not None:
        if dynamic_pe_discount_vs_ttm >= 25:
            expectation_support_score += 20
            signals.append("forward_pe_improving")
            reasoning.append(f"动态 PE 较 TTM 下降 {dynamic_pe_discount_vs_ttm:.2f}%，说明市场预期未来盈利改善能够部分消化高估值。")
        elif dynamic_pe_discount_vs_ttm >= 10:
            expectation_support_score += 10
        elif dynamic_pe_discount_vs_ttm <= 0:
            expectation_support_score -= 15
            signals.append("forward_pe_not_improving")
            reasoning.append("动态 PE 没有明显低于 TTM PE，意味着盈利改善预期对当前高估值的消化能力有限。")
    else:
        limitations.append("缺少动态 PE 或 TTM PE，无法判断未来盈利预期是否显著改善。")

    if peg is not None:
        if peg <= 1:
            expectation_support_score += 20
            signals.append("peg_supportive")
            reasoning.append(f"PEG 为 {peg:.2f}，估值与增长匹配度较好。")
        elif peg <= 1.5:
            expectation_support_score += 5
        elif peg <= 2:
            expectation_support_score -= 10
            signals.append("peg_elevated")
            reasoning.append(f"PEG 为 {peg:.2f}，增长对估值的支撑开始偏弱。")
        else:
            expectation_support_score -= 25
            signals.append("peg_above_2")
            reasoning.append(f"PEG 为 {peg:.2f}，当前估值对增长兑现的要求较高。")
    else:
        limitations.append("缺少 PEG，无法直接衡量估值与增长预期是否匹配。")

    if dividend_yield is not None:
        if dividend_yield >= 3:
            expectation_support_score += 8
            signals.append("dividend_buffer_strong")
        elif dividend_yield < 1:
            expectation_support_score -= 8
            signals.append("dividend_buffer_weak")
    else:
        limitations.append("缺少股息率，无法评估现金回报对高估值的缓冲作用。")

    expensive_score = round(_clamp(expensive_score, 0, 100), 2)
    expectation_support_score = round(_clamp(expectation_support_score, 0, 100), 2)

    evidence_count = sum(
        metric is not None
        for metric in (
            percentile_anchor,
            pe_premium_vs_industry,
            pb_premium_vs_industry,
            dynamic_pe_discount_vs_ttm,
            peg,
            dividend_yield,
        )
    )
    confidence = round(_clamp(evidence_count / 6 * 100, 0, 100), 2)
    overdraft_score = round(_clamp(expensive_score * 0.65 + (100 - expectation_support_score) * 0.35, 0, 100), 2)

    if evidence_count < 2:
        status = "uncertain"
        reasoning.append("可用估值证据较少，当前更适合把结果视作提示信号而非明确结论。")
    elif overdraft_score >= 75:
        status = "high"
    elif overdraft_score >= 55:
        status = "medium"
    elif overdraft_score >= 35:
        status = "watch"
    else:
        status = "low"

    if not reasoning:
        reasoning.append("现有估值与预期信号没有出现明显背离，短期内未观察到强烈的透支特征。")

    return {
        "status": status,
        "score": overdraft_score,
        "confidence": confidence,
        "valuation_expensive_score": expensive_score,
        "expectation_support_score": expectation_support_score,
        "signals": signals,
        "metrics": {
            "pe_ttm": pe_ttm,
            "pe_dynamic": pe_dynamic,
            "pb": pb,
            "peg": peg,
            "dividend_yield": dividend_yield,
            "pe_percentile_1y": pe_1y,
            "pe_percentile_3y": pe_3y,
            "pe_percentile_5y": pe_5y,
            "industry_pe": industry_pe,
            "industry_pb": industry_pb,
            "pe_premium_vs_industry": pe_premium_vs_industry,
            "pb_premium_vs_industry": pb_premium_vs_industry,
            "dynamic_pe_discount_vs_ttm": dynamic_pe_discount_vs_ttm,
        },
        "reasoning": reasoning[:4],
        "limitations": limitations[:4],
    }


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
