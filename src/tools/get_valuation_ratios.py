# -*- coding: utf-8 -*-
"""``get_valuation_ratios`` — current, historical and relative valuation."""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from typing import Any

import httpx

from data_provider.utils import is_bse_code
from src.tools._akshare import bare_symbol, cached_call, exchange_prefix, frame_records
from src.tools.base import ToolSpec, object_schema

_QUOTE_URL = "https://push2delay.eastmoney.com/api/qt/stock/get"
_COMPARISON_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_text(value: Any) -> str | None:
    text = str(value or "")[:10]
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _secid(code: str) -> str:
    return f"{0 if is_bse_code(code) or not code.startswith(('6', '9')) else 1}.{code}"


def _fetch_history(code: str):
    import akshare as ak

    frame = ak.stock_value_em(symbol=code)
    if frame is None or frame.empty:
        raise RuntimeError("AKShare stock_value_em 没有估值历史")
    return frame


def _fetch_quote(code: str) -> dict[str, Any]:
    response = httpx.get(
        _QUOTE_URL,
        params={
            "secid": _secid(code),
            "fields": "f43,f57,f58,f116,f117,f124,f162,f163,f164,f167",
            "fltt": 2,
            "invt": 2,
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
        headers={"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"},
        timeout=httpx.Timeout(10.0, connect=3.0),
    )
    response.raise_for_status()
    data = response.json().get("data") or {}
    if str(data.get("f57") or "").zfill(6) != code:
        raise RuntimeError("东方财富估值快照返回了其他证券")
    timestamp = _number(data.get("f124"))
    return {
        "name": str(data.get("f58") or "").strip() or None,
        "price": _number(data.get("f43")),
        "pe_dynamic": _number(data.get("f162")),
        "pe_static": _number(data.get("f163")),
        "pe_ttm": _number(data.get("f164")),
        # Eastmoney's quote PB uses the annual-report denominator.  The MRQ PB
        # from stock_value_em is exposed separately and preferred below.
        "pb_annual": _number(data.get("f167")),
        "total_market_cap": _number(data.get("f116")),
        "circulating_market_cap": _number(data.get("f117")),
        "quote_time": datetime.fromtimestamp(timestamp).astimezone().isoformat() if timestamp else None,
    }


def _fetch_comparison(code: str) -> dict[str, Any]:
    prefixed = exchange_prefix(code, upper=True)
    response = httpx.get(
        _COMPARISON_URL,
        params={
            "reportName": "RPT_PCF10_INDUSTRY_CVALUE",
            "columns": "ALL",
            "quoteColumns": "",
            "filter": f'(SECUCODE="{code}.{prefixed[:2]}")',
            "pageNumber": "",
            "pageSize": "",
            "sortTypes": "1",
            "sortColumns": "PAIMING",
            "source": "HSF10",
            "client": "PC",
        },
        headers={"User-Agent": _UA, "Referer": "https://emweb.securities.eastmoney.com/"},
        timeout=httpx.Timeout(12.0, connect=3.0),
    )
    response.raise_for_status()
    rows = ((response.json().get("result") or {}).get("data") or [])
    if not rows:
        raise RuntimeError("东方财富没有返回同行估值比较")
    target = next((row for row in rows if str(row.get("CORRE_SECURITY_CODE")) == code), {})
    average = next((row for row in rows if row.get("CORRE_SECURITY_CODE") == "行业平均"), {})
    median = next((row for row in rows if row.get("CORRE_SECURITY_CODE") == "行业中值"), {})
    if not target:
        raise RuntimeError("同行估值比较中没有目标证券")
    report_date = _date_text(target.get("REPORT_DATE"))
    base_year = datetime.fromisoformat(report_date).year if report_date else None
    forward_pe = []
    forward_ps = []
    for offset, pe_field, ps_field in (
        (0, "PE_1Y", "PS_1Y"),
        (1, "PE_2Y", "PS_2Y"),
        (2, "PE_3Y", "PS_3Y"),
    ):
        year = base_year + offset if base_year else None
        pe = _number(target.get(pe_field))
        ps = _number(target.get(ps_field))
        if pe is not None:
            forward_pe.append({"year": year, "value": pe})
        if ps is not None:
            forward_ps.append({"year": year, "value": ps})

    def benchmark(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "pe_ttm": _number(row.get("PE_TTM")),
            "pb_mrq": _number(row.get("PB_MRQ")),
            "ps_ttm": _number(row.get("PS_TTM")),
            "pcf_ttm": _number(row.get("PCE_TTM")),
            "peg_forward": _number(row.get("PEG")),
        }

    total = int(_number(target.get("TOTAL_COUNT")) or 0)
    rank = int(_number(target.get("PAIMING")) or 0) or None
    return {
        "report_date": report_date,
        "forward_pe": forward_pe,
        "forward_ps": forward_ps,
        "peg_forward": _number(target.get("PEG")),
        "rank": rank,
        "total": total,
        "percentile_rank_pct": round(rank / total * 100, 4) if rank and total else None,
        "average": benchmark(average),
        "median": benchmark(median),
    }


def _fetch_dividends(code: str):
    import akshare as ak

    frame = ak.stock_fhps_detail_em(symbol=code)
    if frame is None or frame.empty:
        raise RuntimeError("AKShare stock_fhps_detail_em 没有分红记录")
    return frame


def _latest_history(frame) -> dict[str, Any]:
    rows = frame_records(frame)
    normalized = []
    for row in rows:
        trade_date = _date_text(row.get("数据日期"))
        if not trade_date:
            continue
        normalized.append({
            "trade_date": trade_date,
            "price": _number(row.get("当日收盘价")),
            "total_market_cap": _number(row.get("总市值")),
            "circulating_market_cap": _number(row.get("流通市值")),
            "total_shares": _number(row.get("总股本")),
            "circulating_shares": _number(row.get("流通股本")),
            "pe_ttm": _number(row.get("PE(TTM)")),
            "pe_static": _number(row.get("PE(静)")),
            "pb_mrq": _number(row.get("市净率")),
            "peg_trailing": _number(row.get("PEG值")),
            "pcf_ttm": _number(row.get("市现率")),
            "ps_ttm": _number(row.get("市销率")),
        })
    if not normalized:
        raise RuntimeError("估值历史没有可解析交易日")
    return max(normalized, key=lambda item: item["trade_date"])


def _history_statistics(frame, current_pe: float | None, as_of: date) -> tuple[dict[str, float], dict[str, Any]]:
    import pandas as pd

    if current_pe is None or current_pe <= 0 or "PE(TTM)" not in frame.columns:
        return {}, {}
    work = frame.copy()
    work["_date"] = pd.to_datetime(work.get("数据日期"), errors="coerce")
    work["_pe"] = pd.to_numeric(work["PE(TTM)"], errors="coerce")
    # Negative/zero PE represents a loss-making period and is not comparable
    # to a positive earnings multiple.
    work = work[(work["_date"].notna()) & (work["_pe"] > 0)]
    percentiles: dict[str, float] = {}
    stats: dict[str, Any] = {}
    for years in (1, 3, 5):
        start = pd.Timestamp(as_of - timedelta(days=365 * years))
        window = work[(work["_date"] >= start) & (work["_date"] <= pd.Timestamp(as_of))]
        if window.empty:
            continue
        values = window["_pe"]
        percentile = round(float((values <= current_pe).sum()) / len(values) * 100, 2)
        key = f"{years}y"
        percentiles[key] = percentile
        stats[key] = {
            "percentile_pct": percentile,
            "observations": int(len(values)),
            "min": round(float(values.min()), 4),
            "median": round(float(values.median()), 4),
            "max": round(float(values.max()), 4),
        }
    return percentiles, stats


def _ttm_dividend(frame, current_price: float | None, as_of: date) -> dict[str, Any]:
    items = []
    cutoff = as_of - timedelta(days=365)
    for row in frame_records(frame):
        ex_date = _date_text(row.get("除权除息日"))
        cash_per_10 = _number(row.get("现金分红-现金分红比例"))
        if not ex_date or cash_per_10 is None or "实施" not in str(row.get("方案进度") or ""):
            continue
        parsed = datetime.fromisoformat(ex_date).date()
        if cutoff < parsed <= as_of:
            items.append({
                "report_date": _date_text(row.get("报告期")),
                "ex_dividend_date": ex_date,
                "cash_dividend_per_10_shares": cash_per_10,
                "cash_dividend_per_share": round(cash_per_10 / 10, 6),
            })
    items.sort(key=lambda item: item["ex_dividend_date"])
    cash_per_share = round(sum(item["cash_dividend_per_share"] for item in items), 6)
    return {
        "cash_dividend_per_share_ttm": cash_per_share,
        "dividend_yield_ttm_pct": round(cash_per_share / current_price * 100, 4) if current_price else None,
        "dividend_count_ttm": len(items),
        "dividends_ttm": items,
        "window_start_exclusive": cutoff.isoformat(),
        "window_end_inclusive": as_of.isoformat(),
    }


def _scale(value: float | None, current_price: float | None, history_price: float | None) -> float | None:
    if value is None:
        return None
    if not current_price or not history_price:
        return value
    return round(value * current_price / history_price, 8)


def _expected_completed_trade_day(now: datetime) -> date:
    try:
        from src.tools._trading_calendar import _fetch_trade_dates

        dates = [day for day in _fetch_trade_dates() if day <= now.date()]
        if now.date() in dates and now.time() >= time(15, 30):
            return now.date()
        prior = [day for day in dates if day < now.date()]
        if prior:
            return prior[-1]
    except Exception:
        pass
    day = now.date() if now.weekday() < 5 and now.time() >= time(15, 30) else now.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _select_forward(items: list[dict[str, Any]], year: int) -> tuple[float | None, float | None]:
    usable = _active_forward_series(items, year)
    usable.sort(key=lambda item: item["year"])
    current = usable[0]["value"] if usable else None
    following = usable[1]["value"] if len(usable) > 1 else None
    return current, following


def _active_forward_series(
    items: list[dict[str, Any]],
    as_of_year: int,
) -> list[dict[str, Any]]:
    """Exclude estimates whose forecast year is already in the past."""
    usable = [
        item
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("year"), int)
        and item["year"] >= as_of_year
        and item.get("value") is not None
    ]
    return sorted(usable, key=lambda item: item["year"])


def _build(symbol: str, with_history: bool, use_cache: bool) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    now = datetime.now().astimezone()
    errors: list[str] = []
    cache_detail: dict[str, bool] = {}
    history_frame = dividend_frame = None
    quote: dict[str, Any] = {}
    comparison: dict[str, Any] = {}

    def history_call():
        if not use_cache:
            return _fetch_history(code), False
        return cached_call(f"valuation:history:v2:{code}", lambda: _fetch_history(code), ttl_seconds=30 * 60, attempts=2)

    def dividend_call():
        if not use_cache:
            return _fetch_dividends(code), False
        return cached_call(f"valuation:dividend:v2:{code}", lambda: _fetch_dividends(code), ttl_seconds=6 * 3600, attempts=2)

    def quote_call():
        if not use_cache:
            return _fetch_quote(code), False
        return cached_call(f"valuation:quote:v2:{code}", lambda: _fetch_quote(code), ttl_seconds=60, attempts=2)

    def comparison_call():
        if not use_cache:
            return _fetch_comparison(code), False
        return cached_call(f"valuation:comparison:v2:{code}", lambda: _fetch_comparison(code), ttl_seconds=30 * 60, attempts=2)

    calls = {
        "history": history_call,
        "quote": quote_call,
        "comparison": comparison_call,
        "dividend": dividend_call,
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(fn) for name, fn in calls.items()}
        for name, future in futures.items():
            try:
                value, cached = future.result()
                cache_detail[name] = cached
                if name == "history":
                    history_frame = value
                elif name == "quote":
                    quote = value
                elif name == "comparison":
                    comparison = value
                else:
                    dividend_frame = value
            except Exception as exc:
                cache_detail[name] = False
                errors.append(f"{name}: {exc}")

    history = _latest_history(history_frame) if history_frame is not None else {}
    current_price = quote.get("price") or history.get("price")
    history_price = history.get("price")
    pe_ttm = quote.get("pe_ttm") or history.get("pe_ttm")
    pe_static = quote.get("pe_static") or history.get("pe_static")
    pe_dynamic = quote.get("pe_dynamic")
    pb_mrq = _scale(history.get("pb_mrq"), current_price, history_price)
    ps_ttm = _scale(history.get("ps_ttm"), current_price, history_price)
    pcf_ttm = _scale(history.get("pcf_ttm"), current_price, history_price)
    peg_trailing = _scale(history.get("peg_trailing"), current_price, history_price)
    raw_forward_pe = comparison.get("forward_pe") or []
    raw_forward_ps = comparison.get("forward_ps") or []
    forward_pe = _active_forward_series(raw_forward_pe, now.year)
    forward_ps = _active_forward_series(raw_forward_ps, now.year)
    forward_pe_current, forward_pe_next = _select_forward(forward_pe, now.year)
    peg_forward = comparison.get("peg_forward")
    dividend = _ttm_dividend(dividend_frame, current_price, now.date()) if dividend_frame is not None else {
        "cash_dividend_per_share_ttm": None,
        "dividend_yield_ttm_pct": None,
        "dividend_count_ttm": None,
        "dividends_ttm": [],
    }
    percentiles, history_stats = _history_statistics(history_frame, pe_ttm, now.date()) if with_history and history_frame is not None else ({}, {})
    benchmark = {
        "report_date": comparison.get("report_date"),
        "sample_size": comparison.get("total") or 0,
        "median": comparison.get("median") or {},
        "average": comparison.get("average") or {},
        "preferred_for_comparison": "median",
        "warning": "算术平均值可能受亏损股和极端值显著影响，默认使用行业中值比较。",
    }
    industry_median = benchmark["median"]
    industry_compat = {
        "industry": None,
        "pe": industry_median.get("pe_ttm"),
        "pb": industry_median.get("pb_mrq"),
        "sample_size": benchmark["sample_size"],
        "basis": "industry_median",
    }

    from api.v1.endpoints.financials._signal import _build_price_overdraft_signal

    signal = _build_price_overdraft_signal({
        "pe_ttm": pe_ttm,
        "forward_pe": forward_pe_current,
        "pb": pb_mrq,
        "peg": peg_forward if peg_forward is not None and peg_forward > 0 else None,
        "dividend_yield": dividend.get("dividend_yield_ttm_pct"),
        "pe_percentiles": percentiles,
        "industry_average": industry_compat,
    })
    success = any(value is not None for value in (pe_ttm, pe_static, pb_mrq, ps_ttm, pcf_ttm))
    quote_live = bool(quote and quote.get("price") is not None)
    history_trade_date = history.get("trade_date")
    expected_completed = _expected_completed_trade_day(now)
    if quote_live:
        stale: bool | None = False
    elif history_trade_date:
        stale = datetime.fromisoformat(history_trade_date).date() < expected_completed
    else:
        stale = None
    sources = []
    if history_frame is not None:
        sources.append("东方财富估值历史/AKShare")
    if quote:
        sources.append("东方财富实时估值快照")
    if comparison:
        sources.append("东方财富同行估值比较")
    if dividend_frame is not None:
        sources.append("东方财富分红实施记录/AKShare")
    return {
        "symbol": code,
        "name": quote.get("name"),
        "trade_date": now.date().isoformat() if quote_live else history_trade_date,
        "history_trade_date": history_trade_date,
        "current_price": current_price,
        "pe_ttm": pe_ttm,
        "pe_static": pe_static,
        "pe_dynamic": pe_dynamic,
        "pb_mrq": pb_mrq,
        "pb_annual": quote.get("pb_annual"),
        "pb": pb_mrq,
        "ps_ttm": ps_ttm,
        "ps": ps_ttm,
        "pcf_ttm": pcf_ttm,
        "pcf": pcf_ttm,
        "peg_trailing": peg_trailing,
        "peg_forward": peg_forward,
        "peg": peg_forward if peg_forward is not None else peg_trailing,
        "peg_basis": "forward_growth" if peg_forward is not None else "trailing_growth",
        "forward_pe": forward_pe,
        "forward_ps": forward_ps,
        "excluded_expired_forward_years": sorted({
            item["year"]
            for item in [*raw_forward_pe, *raw_forward_ps]
            if isinstance(item, dict)
            and isinstance(item.get("year"), int)
            and item["year"] < now.year
        }),
        "forward_pe_current_year": forward_pe_current,
        "forward_pe_next_year": forward_pe_next,
        "dividend_yield_ttm_pct": dividend.get("dividend_yield_ttm_pct"),
        "dividend_yield": dividend.get("dividend_yield_ttm_pct"),
        "cash_dividend_per_share_ttm": dividend.get("cash_dividend_per_share_ttm"),
        "dividend_count_ttm": dividend.get("dividend_count_ttm"),
        "dividends_ttm": dividend.get("dividends_ttm") or [],
        "pe_percentiles": percentiles,
        "pe_history_stats": history_stats,
        "industry_rank": {
            "rank": comparison.get("rank"),
            "sample_size": comparison.get("total") or 0,
            "percentile_rank_pct": comparison.get("percentile_rank_pct"),
            "direction": "ascending_by_peg_or_provider_rank; consult benchmark metrics for valuation comparison",
        },
        "industry_benchmark": benchmark,
        "industry_average": industry_compat,
        "price_overdraft_signal": signal,
        "total_market_cap": quote.get("total_market_cap") or history.get("total_market_cap"),
        "circulating_market_cap": quote.get("circulating_market_cap") or history.get("circulating_market_cap"),
        "total_shares": history.get("total_shares"),
        "circulating_shares": history.get("circulating_shares"),
        "price_unit": "人民币元",
        "market_cap_unit": "元",
        "share_unit": "股",
        "ratio_unit": "倍",
        "percent_unit": "%",
        "valuation_basis": {
            "pe_ttm": "最近四个季度归母净利润",
            "pe_static": "最近完整年度归母净利润",
            "pe_dynamic": "最新报告期利润年化（东方财富动态PE）",
            "pb_mrq": "最近报告期净资产；盘中按价格变化同比例更新",
            "ps_ttm": "最近四个季度营业收入；盘中按价格变化同比例更新",
            "pcf_ttm": "最近四个季度经营现金流；盘中按价格变化同比例更新",
            "forward_pe": "东方财富同行比较中的盈利预测口径",
            "dividend_yield_ttm_pct": "近365天已实施现金分红合计/当前股价",
        },
        "sources": sources,
        "source": " + ".join(sources) or "none",
        "source_urls": [
            f"https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code={exchange_prefix(code, upper=True)}#/thbj/gzbj",
            f"https://quote.eastmoney.com/{'bj' if is_bse_code(code) else 'sh' if code.startswith(('6', '9')) else 'sz'}{code}.html",
        ],
        "success": success,
        "partial": success and bool(errors),
        "errors": errors,
        "data_time": quote.get("quote_time") or (now.isoformat() if quote_live else history_trade_date),
        "data_time_inferred": quote_live and not quote.get("quote_time"),
        "is_stale": stale,
        "freshness_unknown": stale is None,
        "fallback_used": history_frame is None and quote_live,
        "cache_detail": cache_detail,
        "_cached": bool(cache_detail) and all(cache_detail.values()),
        "_fetched_at": now.isoformat(),
    }


def get_valuation_ratios(
    symbol: str,
    with_history: bool = True,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    return _build(symbol, bool(with_history), use_cache)


TOOL = ToolSpec(
    name="get_valuation_ratios",
    description=(
        "获取当前 PE(TTM/静态/动态)、PB(MRQ/年报)、PS(TTM)、PCF(TTM)、远期 PE/PEG、"
        "近365天真实已实施股息率、仅使用正 PE 的历史分位，以及行业中值和均值。"
    ),
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "with_history": {"type": "boolean", "default": True, "description": "是否计算 1/3/5 年正 PE 历史分位"},
    }, ["symbol"]),
    executor=get_valuation_ratios,
    category="financials",
)
