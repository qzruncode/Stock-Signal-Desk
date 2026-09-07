"""Existing industry source collection; inference remains business-owned."""

from __future__ import annotations
from contextvars import copy_context
import logging
from datetime import datetime
from io import StringIO
from typing import Any, Optional
from .industry_normalization import (
    _compact_text,
    _normalize_symbol,
    _normalize_text,
    _prune_none,
    _safe_float,
    _safe_int,
)

logger = logging.getLogger(__name__)


def _find_exact_name(target_name: str, candidate_names: list[str]) -> Optional[str]:
    normalized_candidates = [
        (name, _compact_text(name)) for name in candidate_names if _normalize_text(name)
    ]
    compact_target = _compact_text(target_name)
    for original, compact_candidate in normalized_candidates:
        if compact_candidate == compact_target:
            return original
    return None


def _fetch_ths_industry_summary(industry_name: str) -> dict[str, Any]:
    if not industry_name:
        return {"source_ok": False, "error": "missing industry_name"}
    try:
        import akshare as ak

        name_df = ak.stock_board_industry_name_ths()
        summary_df = ak.stock_board_industry_summary_ths()
        if name_df is None or name_df.empty or summary_df is None or summary_df.empty:
            return {"source_ok": False, "error": "ths industry tables empty"}

        ths_names = [str(item) for item in name_df["name"].astype(str).tolist()]
        matched_name = _find_exact_name(industry_name, ths_names)
        if not matched_name:
            return {
                "source_ok": False,
                "error": f"ths board not matched for {industry_name}",
            }

        code_row = name_df[name_df["name"].astype(str) == matched_name]
        matched_code = str(code_row.iloc[0]["code"]) if not code_row.empty else ""

        summary_df = summary_df.copy()
        summary_df["_name"] = summary_df["板块"].astype(str)
        summary_row = summary_df[summary_df["_name"] == matched_name]
        if summary_row.empty:
            return {
                "source_ok": True,
                "matched_name": matched_name,
                "matched_code": matched_code,
                "summary": None,
            }

        row = summary_row.iloc[0]
        board_rank = _safe_int(row.get("序号"))
        total = int(len(summary_df))
        up_down = _normalize_text(row.get("涨跌家数"))
        up_count = down_count = None
        if "/" in up_down:
            left, right = up_down.split("/", 1)
            up_count = _safe_int(left)
            down_count = _safe_int(right)

        return {
            "source_ok": True,
            "matched_name": matched_name,
            "matched_code": matched_code,
            "summary": {
                "name": matched_name,
                "code": matched_code,
                "change_pct": _safe_float(row.get("涨跌幅")),
                "lead_stock": _normalize_text(row.get("领涨股")),
                "lead_stock_price": _safe_float(row.get("领涨股-最新价")),
                "lead_stock_change_pct": _safe_float(row.get("领涨股-涨跌幅")),
                "up_count": up_count,
                "down_count": down_count,
                "total_amount": (
                    _safe_float(row.get("总成交额")) * 1e8
                    if _safe_float(row.get("总成交额")) is not None
                    else None
                ),
                "net_flow": (
                    _safe_float(row.get("净流入")) * 1e8
                    if _safe_float(row.get("净流入")) is not None
                    else None
                ),
                "rank": board_rank,
                "total": total,
                "source": "stock_board_industry_summary_ths",
            },
        }
    except Exception as exc:
        logger.warning("ths industry summary failed for %s: %s", industry_name, exc)
        return {"source_ok": False, "error": str(exc)}


def _fetch_peer_snapshot(
    industry_name: str, *, board_name: str = "", board_code: str = ""
) -> dict[str, Any]:
    if not industry_name and not board_name:
        return {
            "sample_size": 0,
            "sample_names": [],
            "source": None,
            "source_ok": False,
        }
    if board_name and board_code:
        try:
            import pandas as pd
            import requests

            url = f"https://q.10jqka.com.cn/thshy/detail/code/{board_code}/"
            response = requests.get(
                url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}
            )
            response.raise_for_status()
            response.encoding = "gbk"
            dfs = pd.read_html(StringIO(response.text))
            if dfs:
                df = dfs[0]
                names: list[str] = []
                codes: list[str] = []
                for _, row in df.head(12).iterrows():
                    name = _normalize_text(row.get("名称"))
                    code = _normalize_text(row.get("代码"))
                    if name:
                        names.append(name)
                    if code:
                        codes.append(code.zfill(6))
                return {
                    "sample_size": int(len(df)),
                    "sample_names": names,
                    "sample_codes": codes,
                    "board_name": board_name,
                    "board_code": board_code,
                    "source": "ths_industry_detail_page",
                    "source_ok": True,
                }
        except Exception as exc:
            logger.warning(
                "ths peer snapshot failed for %s/%s: %s", board_name, board_code, exc
            )
    try:
        import akshare as ak

        df = ak.stock_board_industry_cons_em(symbol=industry_name)
        if df is None or df.empty:
            return {
                "sample_size": 0,
                "sample_names": [],
                "source": "stock_board_industry_cons_em",
                "source_ok": True,
            }

        names = []
        for _, row in df.head(8).iterrows():
            name = _normalize_text(row.get("名称") or row.get("股票名称"))
            if name:
                names.append(name)
        return {
            "sample_size": int(len(df)),
            "sample_names": names,
            "sample_codes": [
                _normalize_text(row.get("代码") or row.get("股票代码")).zfill(6)
                for _, row in df.head(12).iterrows()
                if _normalize_text(row.get("代码") or row.get("股票代码"))
            ],
            "source": "stock_board_industry_cons_em",
            "source_ok": True,
        }
    except Exception as exc:
        logger.warning("industry peer snapshot failed for %s: %s", industry_name, exc)
        return {
            "sample_size": 0,
            "sample_names": [],
            "source": None,
            "source_ok": False,
            "error": str(exc),
        }


def _fetch_lhb_snapshot(symbol: str) -> dict[str, Any]:
    code = _normalize_symbol(symbol)
    try:
        import akshare as ak

        df = ak.stock_lhb_stock_statistic_em(symbol="近一月")
        if df is None or df.empty:
            return {
                "source_ok": False,
                "source": "stock_lhb_stock_statistic_em",
                "error": "empty dataframe",
            }

        code_col = "代码" if "代码" in df.columns else None
        if not code_col:
            return {
                "source_ok": False,
                "source": "stock_lhb_stock_statistic_em",
                "error": "code column missing",
            }

        row_df = df[df[code_col].astype(str).str.zfill(6) == code]
        if row_df.empty:
            return {
                "source_ok": True,
                "source": "stock_lhb_stock_statistic_em",
                "matched": False,
                "window": "近一月",
            }

        row = row_df.iloc[0]
        return _prune_none(
            {
                "source_ok": True,
                "source": "stock_lhb_stock_statistic_em",
                "matched": True,
                "window": "近一月",
                "latest_date": _normalize_text(row.get("最近上榜日")),
                "appearance_count": _safe_int(row.get("上榜次数")),
                "close": _safe_float(row.get("收盘价")),
                "pct_chg": _safe_float(row.get("涨跌幅")),
                "net_buy_amount": _safe_float(row.get("龙虎榜净买额")),
                "buy_amount": _safe_float(row.get("龙虎榜买入额")),
                "sell_amount": _safe_float(row.get("龙虎榜卖出额")),
                "turnover_amount": _safe_float(row.get("龙虎榜总成交额")),
                "buy_inst_count": _safe_int(row.get("买方机构次数")),
                "sell_inst_count": _safe_int(row.get("卖方机构次数")),
                "inst_net_buy_amount": _safe_float(row.get("机构买入净额")),
                "inst_buy_amount": _safe_float(row.get("机构买入总额")),
                "inst_sell_amount": _safe_float(row.get("机构卖出总额")),
                "pct_chg_1m": _safe_float(row.get("近1个月涨跌幅")),
                "pct_chg_3m": _safe_float(row.get("近3个月涨跌幅")),
                "pct_chg_6m": _safe_float(row.get("近6个月涨跌幅")),
                "pct_chg_1y": _safe_float(row.get("近1年涨跌幅")),
            }
        )
    except Exception as exc:
        logger.warning("lhb snapshot failed for %s: %s", symbol, exc)
        return {
            "source_ok": False,
            "source": "stock_lhb_stock_statistic_em",
            "error": str(exc),
        }


def _fetch_stock_flow_snapshot(symbol: str, *, force: bool = False) -> dict[str, Any]:
    code = _normalize_symbol(symbol)
    payload: dict[str, Any] = {
        "symbol": code,
        "source_ok": False,
        "source": None,
        "window": None,
        "_cached": False,
        "_fetched_at": datetime.now().isoformat(),
    }
    errors: list[str] = []

    try:
        import concurrent.futures
        import akshare as ak

        def _load(symbol_name: str):
            return ak.stock_fund_flow_individual(symbol=symbol_name)

        for window in ("3日排行", "5日排行", "10日排行"):
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    df = pool.submit(copy_context().run, _load, window).result(
                        timeout=25
                    )
                if df is None or df.empty:
                    continue
                code_col = next(
                    (c for c in df.columns if "股票代码" in str(c) or "代码" in str(c)),
                    None,
                )
                if not code_col:
                    errors.append(f"{window}:code_col_missing")
                    continue
                matched = df[df[code_col].astype(str).str.zfill(6) == code]
                if matched.empty:
                    continue
                row = matched.iloc[0]
                latest_price = _safe_float(row.get("最新价"))
                stage_pct_raw = _normalize_text(row.get("阶段涨跌幅"))
                turnover_raw = _normalize_text(row.get("连续换手率"))
                net_inflow_raw = row.get("资金流入净额")
                flow_value = None
                text = _normalize_text(net_inflow_raw)
                if text:
                    multiplier = 1.0
                    if text.endswith("亿"):
                        multiplier = 1e8
                        text = text[:-1]
                    elif text.endswith("万"):
                        multiplier = 1e4
                        text = text[:-1]
                    flow_value = _safe_float(text)
                    if flow_value is not None:
                        flow_value *= multiplier
                payload.update(
                    _prune_none(
                        {
                            "source_ok": True,
                            "source": "ths_stock_fund_flow_individual",
                            "window": window,
                            "latest_price": latest_price,
                            "stage_change_pct": _safe_float(
                                stage_pct_raw.replace("%", "")
                            )
                            if stage_pct_raw
                            else None,
                            "continuous_turnover_rate": (
                                _safe_float(turnover_raw.replace("%", ""))
                                if turnover_raw
                                else None
                            ),
                            "net_inflow": flow_value,
                        }
                    )
                )
                return payload
            except Exception as exc:
                errors.append(f"{window}:{type(exc).__name__}")
                continue
    except Exception as exc:
        errors.append(f"ths_stock_flow:{type(exc).__name__}")
        logger.warning("stock flow snapshot failed for %s: %s", symbol, exc)

    payload["errors"] = errors
    return payload
