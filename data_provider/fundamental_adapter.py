# -*- coding: utf-8 -*-
"""
AkShare fundamental adapter (fail-open).

This adapter intentionally uses capability probing against multiple AkShare
endpoint candidates. It should never raise to caller; partial data is allowed.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_DIVIDEND_KEYWORD_MAP: Dict[str, List[str]] = {
    "per_share": [
        "每股派息",
        "每股现金红利",
        "每股分红",
        "每股派现",
        "派现(元/股)",
        "派息(元/股)",
        "税前派息(元/股)",
        "现金分红(税前)",
    ],
    "plan_text": [
        "分配方案",
        "分红方案",
        "实施方案",
        "派息方案",
        "方案",
        "预案",
        "方案说明",
    ],
    "ex_dividend_date": ["除权除息日", "除息日", "除权日", "除权除息", "除息日期"],
    "record_date": ["股权登记日", "登记日"],
    "announce_date": ["公告日期", "公告日", "实施公告日", "预案公告日"],
    "report_date": ["报告期", "报告日期", "截止日期", "统计截止日期"],
}



class AkshareFundamentalAdapter:
    """AkShare adapter for fundamentals, capital flow and dragon-tiger signals."""

    def _call_df_candidates(
        self,
        candidates: List[Tuple[str, Dict[str, Any]]],
    ) -> Tuple[Optional[pd.DataFrame], Optional[str], List[str]]:
        errors: List[str] = []
        try:
            import akshare as ak
        except Exception as exc:
            return None, None, [f"import_akshare:{type(exc).__name__}"]

        for func_name, kwargs in candidates:
            fn = getattr(ak, func_name, None)
            if fn is None:
                continue
            try:
                df = fn(**kwargs)
                if isinstance(df, pd.Series):
                    df = df.to_frame().T
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return df, func_name, errors
            except Exception as exc:
                errors.append(f"{func_name}:{type(exc).__name__}")
                continue
        return None, None, errors

    def get_fundamental_bundle(self, stock_code: str) -> Dict[str, Any]:
        """
        Return normalized fundamental blocks from AkShare with partial tolerance.
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }

        # Financial indicators
        fin_df, fin_source, fin_errors = self._call_df_candidates(
            [
                ("stock_financial_abstract", {"symbol": stock_code}),
                ("stock_financial_analysis_indicator", {"symbol": stock_code}),
                ("stock_financial_analysis_indicator", {}),
            ]
        )
        result["errors"].extend(fin_errors)
        if fin_df is not None:
            row = _extract_latest_row(fin_df, stock_code)
            if row is not None:
                revenue_yoy = _safe_float(_pick_by_keywords(row, ["营业收入同比", "营收同比", "收入同比", "同比增长"]))
                profit_yoy = _safe_float(_pick_by_keywords(row, ["净利润同比", "净利同比", "归母净利润同比"]))
                roe = _safe_float(_pick_by_keywords(row, ["净资产收益率", "ROE", "净资产收益"]))
                gross_margin = _safe_float(_pick_by_keywords(row, ["毛利率"]))
                report_date = _normalize_report_date(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["report_date"]))
                revenue = _safe_float(_pick_by_keywords(row, ["营业总收入", "营业收入", "营收"]))
                net_profit_parent = _safe_float(_pick_by_keywords(row, ["归母净利润", "母公司股东净利润", "净利润"]))
                operating_cash_flow = _safe_float(
                    _pick_by_keywords(row, ["经营活动产生的现金流量净额", "经营现金流", "经营活动现金流"])
                )
                result["growth"] = {
                    "revenue_yoy": revenue_yoy,
                    "net_profit_yoy": profit_yoy,
                    "roe": roe,
                    "gross_margin": gross_margin,
                }
                financial_report_payload = {
                    "report_date": report_date,
                    "revenue": revenue,
                    "net_profit_parent": net_profit_parent,
                    "operating_cash_flow": operating_cash_flow,
                    "roe": roe,
                }
                if any(v is not None for v in financial_report_payload.values()):
                    result["earnings"]["financial_report"] = financial_report_payload
                result["source_chain"].append(f"growth:{fin_source}")

        # Earnings forecast (业绩预告/快报为全市场接口，按报告期拉取后按代码过滤)
        forecast_candidates = []
        for period in _recent_report_periods(4):
            forecast_candidates.append(("stock_yjyg_em", {"date": period}))
            forecast_candidates.append(("stock_yjbb_em", {"date": period}))
        forecast_df, forecast_source, forecast_errors = self._call_df_candidates(forecast_candidates)
        result["errors"].extend(forecast_errors)
        if forecast_df is not None:
            row = _extract_latest_row(forecast_df, stock_code)
            if row is not None:
                result["earnings"]["forecast_summary"] = _safe_str(
                    _pick_by_keywords(row, ["预告", "业绩变动", "内容", "摘要", "公告"])
                )[:200]
                result["source_chain"].append(f"earnings_forecast:{forecast_source}")

        # Earnings quick report
        quick_candidates = [("stock_yjkb_em", {"date": period}) for period in _recent_report_periods(4)]
        quick_df, quick_source, quick_errors = self._call_df_candidates(quick_candidates)
        result["errors"].extend(quick_errors)
        if quick_df is not None:
            row = _extract_latest_row(quick_df, stock_code)
            if row is not None:
                result["earnings"]["quick_report_summary"] = _safe_str(
                    _pick_by_keywords(row, ["快报", "摘要", "公告", "说明"])
                )[:200]
                result["source_chain"].append(f"earnings_quick:{quick_source}")

        # Dividend details (cash dividend, pre-tax)
        dividend_df, dividend_source, dividend_errors = self._call_df_candidates(
            [
                ("stock_fhps_detail_em", {"symbol": stock_code}),
                ("stock_history_dividend_detail", {"symbol": stock_code, "indicator": "分红", "date": ""}),
                ("stock_dividend_cninfo", {"symbol": stock_code}),
            ]
        )
        result["errors"].extend(dividend_errors)
        if dividend_df is not None:
            dividend_payload = _build_dividend_payload(dividend_df, stock_code, max_events=5)
            if dividend_payload:
                result["earnings"]["dividend"] = dividend_payload
                result["source_chain"].append(f"dividend:{dividend_source}")

        # Institution / top shareholders
        # stock_institute_hold(symbol=) 的 symbol 是"年份+季度"报告期选择串（如 20241），
        # stock_institute_recommend(symbol=) 是评级类别；两者均为全市场接口，按代码过滤。
        _recent_hold_periods = [f"{d[:4]}{d[4:6].lstrip('0') or '0'}" for d in _recent_report_periods(4)]
        inst_candidates = [("stock_institute_hold", {"symbol": p}) for p in _recent_hold_periods]
        inst_candidates.append(("stock_institute_recommend", {"symbol": "机构关注度"}))
        inst_df, inst_source, inst_errors = self._call_df_candidates(inst_candidates)
        result["errors"].extend(inst_errors)
        if inst_df is not None:
            row = _extract_latest_row(inst_df, stock_code)
            if row is not None:
                inst_change = _safe_float(_pick_by_keywords(row, ["增减", "变化", "变动", "持股变化"]))
                result["institution"]["institution_holding_change"] = inst_change
                result["source_chain"].append(f"institution:{inst_source}")

        # stock_gdfx_top_10_em 需带市场前缀的 symbol + 报告期 date
        em_prefixed = _to_em_prefixed_symbol(stock_code)
        top10_candidates = []
        for period in _recent_report_periods(4):
            top10_candidates.append(("stock_gdfx_top_10_em", {"symbol": em_prefixed, "date": period}))
        # 兜底：股东户数详情（纯 6 位代码，单股）
        top10_candidates.append(("stock_zh_a_gdhs_detail_em", {"symbol": _normalize_code(stock_code)}))
        top10_df, top10_source, top10_errors = self._call_df_candidates(top10_candidates)
        result["errors"].extend(top10_errors)
        if top10_df is not None:
            row = _extract_latest_row(top10_df, stock_code)
            if row is not None:
                holder_change = _safe_float(_pick_by_keywords(row, ["增减", "变化", "持股变化", "变动"]))
                result["institution"]["top10_holder_change"] = holder_change
                result["source_chain"].append(f"top10:{top10_source}")

        has_content = bool(result["growth"] or result["earnings"] or result["institution"])
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def get_capital_flow(self, stock_code: str, top_n: int = 5) -> Dict[str, Any]:
        """
        Return stock + sector capital flow.
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "stock_flow": {},
            "sector_rankings": {"top": [], "bottom": []},
            "source_chain": [],
            "errors": [],
        }

        # stock_individual_fund_flow(stock, market) 需指定市场；stock_main_fund_flow
        # 是全市场主力净流入排名（symbol 为板块选择串），按代码过滤。
        _mkt = _market_prefix(stock_code)
        _code = _normalize_code(stock_code)
        stock_df, stock_source, stock_errors = self._call_df_candidates(
            [
                ("stock_individual_fund_flow", {"stock": _code, "market": _mkt}),
                ("stock_main_fund_flow", {"symbol": "全部股票"}),
            ]
        )
        result["errors"].extend(stock_errors)
        if stock_df is not None:
            row = _extract_latest_row(stock_df, stock_code)
            if row is not None:
                net_inflow = _safe_float(_pick_by_keywords(row, ["主力净流入", "净流入", "净额"]))
                inflow_5d = _safe_float(_pick_by_keywords(row, ["5日", "五日"]))
                inflow_10d = _safe_float(_pick_by_keywords(row, ["10日", "十日"]))
                result["stock_flow"] = {
                    "main_net_inflow": net_inflow,
                    "inflow_5d": inflow_5d,
                    "inflow_10d": inflow_10d,
                }
                result["source_chain"].append(f"capital_stock:{stock_source}")

        # 板块资金流排名（无 symbol 参数，indicator+sector_type 走默认今日/行业资金流）
        sector_df, sector_source, sector_errors = self._call_df_candidates(
            [
                ("stock_sector_fund_flow_rank", {"indicator": "今日", "sector_type": "行业资金流"}),
            ]
        )
        result["errors"].extend(sector_errors)
        if sector_df is not None:
            name_col = next(
                (c for c in sector_df.columns if any(k in str(c) for k in ("板块", "行业", "名称", "name"))), None
            )
            flow_col = next(
                (c for c in sector_df.columns if any(k in str(c) for k in ("净流入", "主力", "flow", "净额"))), None
            )
            if name_col and flow_col:
                work_df = sector_df[[name_col, flow_col]].copy()
                work_df[flow_col] = pd.to_numeric(work_df[flow_col], errors="coerce")
                work_df = work_df.dropna(subset=[flow_col])
                top_df = work_df.nlargest(top_n, flow_col)
                bottom_df = work_df.nsmallest(top_n, flow_col)
                result["sector_rankings"] = {
                    "top": [
                        {"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in top_df.iterrows()
                    ],
                    "bottom": [
                        {"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])}
                        for _, r in bottom_df.iterrows()
                    ],
                }
                result["source_chain"].append(f"capital_sector:{sector_source}")

        has_content = bool(
            result["stock_flow"] or result["sector_rankings"]["top"] or result["sector_rankings"]["bottom"]
        )
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def get_dragon_tiger_flag(self, stock_code: str, lookback_days: int = 20) -> Dict[str, Any]:
        """
        Return dragon-tiger signal in lookback window.
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "is_on_list": False,
            "recent_count": 0,
            "latest_date": None,
            "source_chain": [],
            "errors": [],
        }

        # stock_lhb_stock_statistic_em(symbol=) 的 symbol 是统计周期串；
        # stock_lhb_detail_em / stock_lhb_jgmmtj_em 需 start_date+end_date（无 symbol）。
        _end = datetime.now().strftime("%Y%m%d")
        _start = (datetime.now() - timedelta(days=max(1, lookback_days))).strftime("%Y%m%d")
        df, source, errors = self._call_df_candidates(
            [
                ("stock_lhb_stock_statistic_em", {"symbol": "近一月"}),
                ("stock_lhb_detail_em", {"start_date": _start, "end_date": _end}),
                ("stock_lhb_jgmmtj_em", {"start_date": _start, "end_date": _end}),
            ]
        )
        result["errors"].extend(errors)
        if df is None:
            return result

        # Try code filter
        code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码"))]
        target = _normalize_code(stock_code)
        matched = pd.DataFrame()
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                cur = df[series == target]
                if not cur.empty:
                    matched = cur
                    break
            except Exception:
                continue
        if matched.empty:
            result["source_chain"].append(f"dragon_tiger:{source}")
            result["status"] = "ok" if code_cols else "partial"
            return result

        date_col = next(
            (c for c in matched.columns if any(k in str(c) for k in ("日期", "上榜", "交易日", "time"))), None
        )
        parsed_dates: List[datetime] = []
        if date_col is not None:
            for val in matched[date_col].astype(str).tolist():
                try:
                    parsed_dates.append(pd.to_datetime(val).to_pydatetime())
                except Exception:
                    continue
        now = datetime.now()
        start = now - timedelta(days=max(1, lookback_days))
        recent_dates = [d for d in parsed_dates if start <= d <= now]

        result["is_on_list"] = bool(recent_dates)
        result["recent_count"] = len(recent_dates) if recent_dates else int(len(matched))
        result["latest_date"] = (
            max(recent_dates).date().isoformat()
            if recent_dates
            else (max(parsed_dates).date().isoformat() if parsed_dates else None)
        )
        result["status"] = "ok"
        result["source_chain"].append(f"dragon_tiger:{source}")
        return result


from . import _fundamental_adapter_functions1 as _fundamental_adapter_functions1


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_fundamental_adapter_functions1,):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))
