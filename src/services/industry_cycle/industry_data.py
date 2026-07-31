# -*- coding: utf-8 -*-
"""External data fetchers and snapshot builders for industry/peer/lhb/flow/trading data."""

from __future__ import annotations

import logging
from datetime import datetime
from io import StringIO
from typing import Any, Optional

from .cache import _stock_flow_cache_get, _stock_flow_cache_put
from .normalization import (
    _as_dict,
    _as_list,
    _compact_text,
    _list_of_dicts,
    _normalize_symbol,
    _normalize_text,
    _prune_none,
    _safe_float,
    _safe_int,
)

logger = logging.getLogger(__name__)


def _find_industry_board(
    industry_name: str, sector_items: list[dict[str, Any]]
) -> tuple[Optional[dict[str, Any]], Optional[int], int]:
    if not sector_items:
        return None, None, 0
    target = _compact_text(industry_name)
    for idx, item in enumerate(sector_items, start=1):
        name = _compact_text(item.get("name"))
        if not name:
            continue
        if name == target:
            return item, idx, len(sector_items)
    return None, None, len(sector_items)


def _find_exact_name(target_name: str, candidate_names: list[str]) -> Optional[str]:
    normalized_candidates = [(name, _compact_text(name)) for name in candidate_names if _normalize_text(name)]
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
            return {"source_ok": False, "error": f"ths board not matched for {industry_name}"}

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
                    _safe_float(row.get("总成交额")) * 1e8 if _safe_float(row.get("总成交额")) is not None else None
                ),
                "net_flow": (
                    _safe_float(row.get("净流入")) * 1e8 if _safe_float(row.get("净流入")) is not None else None
                ),
                "rank": board_rank,
                "total": total,
                "source": "stock_board_industry_summary_ths",
            },
        }
    except Exception as exc:
        logger.warning("ths industry summary failed for %s: %s", industry_name, exc)
        return {"source_ok": False, "error": str(exc)}


def _find_sector_flow(
    industry_name: str, flow_records: list[dict[str, Any]]
) -> tuple[Optional[dict[str, Any]], Optional[int], int]:
    if not flow_records:
        return None, None, 0
    target = _compact_text(industry_name)
    for idx, item in enumerate(flow_records, start=1):
        name = _compact_text(item.get("name"))
        if not name:
            continue
        if name == target:
            return item, idx, len(flow_records)
    return None, None, len(flow_records)


def _fetch_peer_snapshot(industry_name: str, *, board_name: str = "", board_code: str = "") -> dict[str, Any]:
    if not industry_name and not board_name:
        return {"sample_size": 0, "sample_names": [], "source": None, "source_ok": False}
    if board_name and board_code:
        try:
            import pandas as pd
            import requests

            url = f"https://q.10jqka.com.cn/thshy/detail/code/{board_code}/"
            response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
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
            logger.warning("ths peer snapshot failed for %s/%s: %s", board_name, board_code, exc)
    try:
        import akshare as ak

        df = ak.stock_board_industry_cons_em(symbol=industry_name)
        if df is None or df.empty:
            return {"sample_size": 0, "sample_names": [], "source": "stock_board_industry_cons_em", "source_ok": True}

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
        return {"sample_size": 0, "sample_names": [], "source": None, "source_ok": False, "error": str(exc)}


def _fetch_lhb_snapshot(symbol: str) -> dict[str, Any]:
    code = _normalize_symbol(symbol)
    try:
        import akshare as ak

        df = ak.stock_lhb_stock_statistic_em(symbol="近一月")
        if df is None or df.empty:
            return {"source_ok": False, "source": "stock_lhb_stock_statistic_em", "error": "empty dataframe"}

        code_col = "代码" if "代码" in df.columns else None
        if not code_col:
            return {"source_ok": False, "source": "stock_lhb_stock_statistic_em", "error": "code column missing"}

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
        return {"source_ok": False, "source": "stock_lhb_stock_statistic_em", "error": str(exc)}


def _build_trading_snapshot(symbol: str) -> dict[str, Any]:
    try:
        from src.tools._kline import _fetch_kline_with_fallback
        from src.tools.get_realtime_quotes import _get_fetcher
        from datetime import timedelta

        quote = None
        quote_source = None
        try:
            raw_quote = _get_fetcher().get_realtime_quote(symbol)
            if raw_quote and raw_quote.has_basic_data():
                quote = raw_quote.to_dict()
                quote_source = quote.get("source")
        except Exception as exc:
            logger.warning("realtime quote snapshot failed for %s: %s", symbol, exc)

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
        kline_records, kline_source = _fetch_kline_with_fallback(symbol, start_date, end_date)
        recent = kline_records[-60:] if len(kline_records) > 60 else kline_records
        latest_bar = recent[-1] if recent else {}
        prev_bar = recent[-2] if len(recent) >= 2 else {}
        latest_close = _safe_float((quote or {}).get("price")) or _safe_float(latest_bar.get("close"))

        close_20 = _safe_float(recent[-20].get("close")) if len(recent) >= 20 else None
        close_60 = (
            _safe_float(recent[0].get("close"))
            if len(recent) >= 60
            else (_safe_float(recent[0].get("close")) if recent else None)
        )
        high_20 = max(
            (_safe_float(item.get("high")) for item in recent[-20:] if _safe_float(item.get("high")) is not None),
            default=None,
        )
        low_20 = min(
            (_safe_float(item.get("low")) for item in recent[-20:] if _safe_float(item.get("low")) is not None),
            default=None,
        )
        avg_turnover_5 = None
        turnover_values = [
            _safe_float(item.get("turnover_rate"))
            for item in recent[-5:]
            if _safe_float(item.get("turnover_rate")) is not None
        ]
        if turnover_values:
            avg_turnover_5 = round(sum(turnover_values) / len(turnover_values), 2)

        pct_20d = None
        if latest_close is not None and close_20 not in (None, 0):
            pct_20d = round((latest_close - close_20) / abs(close_20) * 100, 2)
        pct_60d = None
        if latest_close is not None and close_60 not in (None, 0):
            pct_60d = round((latest_close - close_60) / abs(close_60) * 100, 2)

        return _prune_none(
            {
                "quote_source": quote_source,
                "kline_source": kline_source,
                "latest_quote": {
                    "price": _safe_float((quote or {}).get("price")),
                    "change_pct": _safe_float((quote or {}).get("change_pct")),
                    "change_amount": _safe_float((quote or {}).get("change_amount")),
                    "volume": _safe_float((quote or {}).get("volume")),
                    "amount": _safe_float((quote or {}).get("amount")),
                    "volume_ratio": _safe_float((quote or {}).get("volume_ratio")),
                    "turnover_rate": _safe_float((quote or {}).get("turnover_rate")),
                    "amplitude": _safe_float((quote or {}).get("amplitude")),
                    "high": _safe_float((quote or {}).get("high")),
                    "low": _safe_float((quote or {}).get("low")),
                    "open_price": _safe_float((quote or {}).get("open_price")),
                    "pre_close": _safe_float((quote or {}).get("pre_close")),
                    "pe_ratio": _safe_float((quote or {}).get("pe_ratio")),
                    "pb_ratio": _safe_float((quote or {}).get("pb_ratio")),
                    "total_mv": _safe_float((quote or {}).get("total_mv")),
                    "circ_mv": _safe_float((quote or {}).get("circ_mv")),
                },
                "latest_bar": {
                    "date": latest_bar.get("date"),
                    "open": _safe_float(latest_bar.get("open")),
                    "close": _safe_float(latest_bar.get("close")),
                    "high": _safe_float(latest_bar.get("high")),
                    "low": _safe_float(latest_bar.get("low")),
                    "pct_chg": _safe_float(latest_bar.get("pct_chg")),
                    "turnover_rate": _safe_float(latest_bar.get("turnover_rate")),
                    "amount": _safe_float(latest_bar.get("amount")),
                    "volume": _safe_float(latest_bar.get("volume")),
                },
                "previous_bar": {
                    "date": prev_bar.get("date"),
                    "close": _safe_float(prev_bar.get("close")),
                    "pct_chg": _safe_float(prev_bar.get("pct_chg")),
                    "turnover_rate": _safe_float(prev_bar.get("turnover_rate")),
                },
                "momentum": {
                    "pct_chg_20d": pct_20d,
                    "pct_chg_60d": pct_60d,
                    "high_20d": high_20,
                    "low_20d": low_20,
                    "avg_turnover_5d": avg_turnover_5,
                },
                "sample_size": len(recent),
                "source_ok": bool(recent),
            }
        )
    except Exception as exc:
        logger.warning("trading snapshot failed for %s: %s", symbol, exc)
        return {"source_ok": False, "error": str(exc)}


def _build_trading_signals(trading_snapshot: dict[str, Any], lhb_snapshot: dict[str, Any]) -> dict[str, Any]:
    latest_quote = _as_dict(trading_snapshot.get("latest_quote"))
    latest_bar = _as_dict(trading_snapshot.get("latest_bar"))
    momentum = _as_dict(trading_snapshot.get("momentum"))

    change_pct = _safe_float(latest_quote.get("change_pct")) or _safe_float(latest_bar.get("pct_chg"))
    volume_ratio = _safe_float(latest_quote.get("volume_ratio"))
    turnover_rate = _safe_float(latest_quote.get("turnover_rate")) or _safe_float(latest_bar.get("turnover_rate"))
    pct_20d = _safe_float(momentum.get("pct_chg_20d"))
    pct_60d = _safe_float(momentum.get("pct_chg_60d"))
    high_20d = _safe_float(momentum.get("high_20d"))
    low_20d = _safe_float(momentum.get("low_20d"))
    latest_price = _safe_float(latest_quote.get("price")) or _safe_float(latest_bar.get("close"))
    lhb_appearances = _safe_int(lhb_snapshot.get("appearance_count")) or 0

    price_position_20d = None
    if latest_price is not None and high_20d not in (None, 0) and low_20d is not None and high_20d > low_20d:
        span = high_20d - low_20d
        if span > 0:
            price_position_20d = round(
                (latest_price - low_20d) / span,
                4,
            )

    return _prune_none(
        {
            "semantic_status": "model_required",
            "trend_stage": None,
            "trend_reason": None,
            "volume_state": None,
            "position_state": None,
            "risk_flags": [],
            "positive_flags": [],
            "metrics": {
                "change_pct": change_pct,
                "volume_ratio": volume_ratio,
                "turnover_rate": turnover_rate,
                "pct_chg_20d": pct_20d,
                "pct_chg_60d": pct_60d,
                "price_position_20d": price_position_20d,
                "latest_price": latest_price,
                "high_20d": high_20d,
                "low_20d": low_20d,
                "lhb_appearances_1m": lhb_appearances,
            },
        }
    )


def _build_stock_focus_snapshot(
    *,
    stock_name: str,
    main_business: str,
    company_specific_evidence: dict[str, Any],
) -> dict[str, Any]:
    financial_snapshot = _as_dict(company_specific_evidence.get("financial_snapshot"))
    financial_statements_snapshot = _as_dict(company_specific_evidence.get("financial_statements_snapshot"))
    shareholder_snapshot = _as_dict(company_specific_evidence.get("shareholder_snapshot"))
    trading_signal_snapshot = _as_dict(company_specific_evidence.get("trading_signal_snapshot"))
    stock_flow_snapshot = _as_dict(company_specific_evidence.get("stock_flow_snapshot"))
    announcements = _list_of_dicts(company_specific_evidence.get("announcements"))
    news = _list_of_dicts(company_specific_evidence.get("news"))
    research = _list_of_dicts(company_specific_evidence.get("research"))

    revenue_yoy = _safe_float(financial_snapshot.get("revenue_yoy"))
    profit_yoy = _safe_float(financial_snapshot.get("net_profit_yoy"))
    deducted_profit_yoy = _safe_float(
        _as_dict(financial_statements_snapshot.get("income_statement")).get("deducted_net_profit_yoy")
    )
    operating_cf = _safe_float(_as_dict(financial_statements_snapshot.get("cashflow")).get("operating_cf"))
    free_cashflow = _safe_float(_as_dict(financial_statements_snapshot.get("cashflow")).get("free_cashflow"))
    holder_change_pct = _safe_float(shareholder_snapshot.get("holder_count_change_pct"))
    institution_holding_pct = _safe_float(shareholder_snapshot.get("institution_holding_pct"))
    stage_change_pct = _safe_float(stock_flow_snapshot.get("stage_change_pct"))
    net_inflow = _safe_float(stock_flow_snapshot.get("net_inflow"))
    finance_points: list[str] = []
    if revenue_yoy is not None:
        finance_points.append(f"营收同比{revenue_yoy:.2f}%")
    if profit_yoy is not None:
        finance_points.append(f"净利同比{profit_yoy:.2f}%")
    if deducted_profit_yoy is not None:
        finance_points.append(f"扣非同比{deducted_profit_yoy:.2f}%")
    if free_cashflow is not None:
        finance_points.append(f"自由现金流{free_cashflow / 1e8:.2f}亿")
    holder_points: list[str] = []
    actual_controller = _normalize_text(shareholder_snapshot.get("actual_controller"))
    if actual_controller:
        holder_points.append(f"实控人: {actual_controller}")
    if holder_change_pct is not None:
        holder_points.append(f"股东人数变化{holder_change_pct:.2f}%")
    if institution_holding_pct is not None:
        holder_points.append(f"机构持股{institution_holding_pct:.2f}%")
    trading_points: list[str] = []
    if stage_change_pct is not None:
        trading_points.append(f"{stock_flow_snapshot.get('window') or '阶段'}涨跌幅{stage_change_pct:.2f}%")
    if net_inflow is not None:
        trading_points.append(f"资金净流入{net_inflow / 1e8:.2f}亿")
    direct_evidence_count = len(announcements) + len(news) + len(research)

    focus_view = (
        f"{stock_name}的主营、财务、股东、交易和公开事项原始证据已汇总；" "各项强弱与投资含义由模型结合完整证据判断。"
    )

    return _prune_none(
        {
            "focus_view": focus_view,
            "semantic_status": "model_required",
            "business_binding_strength": None,
            "finance_state": None,
            "holder_state": None,
            "trading_state": None,
            "direct_evidence_strength": None,
            "main_business": main_business,
            "product_type": company_specific_evidence.get("product_type"),
            "product_name": company_specific_evidence.get("product_name"),
            "finance_points": finance_points[:5],
            "holder_points": holder_points[:5],
            "trading_points": trading_points[:5],
            "trading_metrics": trading_signal_snapshot.get("metrics") or {},
            "major_holder_changes": shareholder_snapshot.get("major_holder_changes") or [],
            "direct_evidence_count": direct_evidence_count,
        }
    )


def _fetch_stock_flow_snapshot(symbol: str, *, force: bool = False) -> dict[str, Any]:
    if not force:
        cached = _stock_flow_cache_get(symbol)
        if cached:
            cached["_cached"] = True
            return cached

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
                    df = pool.submit(_load, window).result(timeout=25)
                if df is None or df.empty:
                    continue
                code_col = next((c for c in df.columns if "股票代码" in str(c) or "代码" in str(c)), None)
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
                            "stage_change_pct": _safe_float(stage_pct_raw.replace("%", "")) if stage_pct_raw else None,
                            "continuous_turnover_rate": (
                                _safe_float(turnover_raw.replace("%", "")) if turnover_raw else None
                            ),
                            "net_inflow": flow_value,
                        }
                    )
                )
                _stock_flow_cache_put(symbol, payload)
                return payload
            except Exception as exc:
                errors.append(f"{window}:{type(exc).__name__}")
                continue
    except Exception as exc:
        errors.append(f"ths_stock_flow:{type(exc).__name__}")
        logger.warning("stock flow snapshot failed for %s: %s", symbol, exc)

    payload["errors"] = errors
    return payload


def _fallback_sector_item_from_flow(flow_item: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not flow_item:
        return None
    return {
        "name": flow_item.get("name"),
        "code": "",
        "change_pct": _safe_float(flow_item.get("pct_chg")),
        "lead_stock": flow_item.get("leading_stock"),
        "lead_stock_price": None,
        "lead_stock_change_pct": None,
        "up_count": _safe_int(flow_item.get("up_count")),
        "down_count": _safe_int(flow_item.get("down_count")),
        "total_amount": _safe_float(flow_item.get("total_amount")),
        "net_flow": _safe_float(flow_item.get("main_net_inflow")),
        "_fallback_from_flow": True,
    }
