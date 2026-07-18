# -*- coding: utf-8 -*-
"""All-market ATR-relative-volatility screener.

The model never fetches or calculates these figures.  This service refreshes
the financial candidate universe, retrieves adjusted daily bars, applies the
formula exactly, and returns only rows that pass every hard condition.
"""

from __future__ import annotations

import bisect
import concurrent.futures
import csv
import logging
import math
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import requests
from sqlalchemy import text

from src.storage import DatabaseManager
from src.tools._kline import _expected_latest_kline_date

logger = logging.getLogger(__name__)

EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)
SINA_OPENAPI_URL = "https://quotes.sina.cn/cn/api/openapi.php/CN_MarketDataService.getKLineData"
EXPORT_DIR = Path(__file__).resolve().parents[3] / "data" / "exports" / "stock-screening"
MIN_BARS_FOR_FULL_WINDOW = 323
KLINE_FETCH_WORKERS = 64
FINANCIAL_FETCH_WORKERS = 8
_HTTP_LOCAL = threading.local()


def _http_session() -> requests.Session:
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        _HTTP_LOCAL.session = session
    return session


@dataclass(frozen=True)
class ScreenRules:
    atr_period: int = 14
    long_period: int = 60
    warning_divisor: float = 1.27
    lookback_days: int = 250
    min_qualified_days: int = 175
    min_qualified_ratio: float = 70.0
    min_revenue_ttm: float = 500_000_000.0
    min_deducted_profit_ttm: float = 0.0
    max_debt_ratio: float = 70.0


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _safe_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def _report_dates(reference: date | None = None) -> tuple[str, str | None, str | None]:
    today = reference or date.today()
    candidates = [
        date(year, month, day)
        for year in (today.year, today.year - 1)
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31))
        if date(year, month, day) <= today
        and (today - date(year, month, day)).days >= 25
    ]
    current = max(candidates)
    if current.month == 12 and current.day == 31:
        return current.isoformat(), None, None
    annual = date(current.year - 1, 12, 31)
    prior_same = date(current.year - 1, current.month, current.day)
    return current.isoformat(), annual.isoformat(), prior_same.isoformat()


def _fetch_financial_page(period: str, page: int) -> tuple[list[dict[str, Any]], int]:
    response = _http_session().get(
        EASTMONEY_URL,
        params={
            "reportName": "RPT_F10_FINANCE_MAINFINADATA",
            "columns": (
                "SECURITY_CODE,REPORT_DATE,UPDATE_DATE,TOTALOPERATEREVE,"
                "KCFJCXSYJLR,ZCFZL"
            ),
            "filter": f"(REPORT_DATE='{period}')",
            "pageNumber": page,
            "pageSize": 500,
            # SECURITY_CODE alone is not unique because the source keeps
            # revised filings. A stable secondary sort prevents duplicate
            # codes from drifting across page boundaries during concurrent
            # pagination and silently dropping neighbouring companies.
            "sortColumns": "SECURITY_CODE,UPDATE_DATE",
            "sortTypes": "1,-1",
            "source": "WEB",
            "client": "WEB",
        },
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("success") is not True:
        raise RuntimeError(str(payload.get("message") or "东财财务数据返回失败"))
    result = payload.get("result") or {}
    return list(result.get("data") or []), int(result.get("pages") or 0)


def _fetch_financial_period(period: str) -> dict[str, dict[str, Any]]:
    first, pages = _fetch_financial_page(period, 1)
    rows = list(first)
    if pages > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=FINANCIAL_FETCH_WORKERS) as pool:
            futures = [pool.submit(_fetch_financial_page, period, page) for page in range(2, pages + 1)]
            for future in concurrent.futures.as_completed(futures):
                page_rows, _ = future.result()
                rows.extend(page_rows)
    by_code: dict[str, dict[str, Any]] = {}
    for row in sorted(
        rows,
        key=lambda item: (str(item.get("SECURITY_CODE") or ""), str(item.get("UPDATE_DATE") or "")),
    ):
        code = str(row.get("SECURITY_CODE") or "").zfill(6)
        if not re.fullmatch(r"\d{6}", code):
            continue
        # Revised filing rows can coexist for one code and some variants leave
        # individual metrics null. Merge the latest non-null value field by
        # field; replacing the whole row used to erase valid revenue/profit and
        # create false "missing financial data" exclusions.
        merged = by_code.setdefault(code, {"SECURITY_CODE": code})
        for field in (
            "REPORT_DATE", "UPDATE_DATE", "TOTALOPERATEREVE", "KCFJCXSYJLR", "ZCFZL",
        ):
            if row.get(field) is not None:
                merged[field] = row[field]
    return by_code


def _build_ttm_financials(reference: date | None = None) -> tuple[dict[str, dict[str, Any]], str]:
    current_period, annual_period, prior_same_period = _report_dates(reference)
    periods = [current_period]
    if annual_period and prior_same_period:
        periods.extend([annual_period, prior_same_period])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        fetched = dict(zip(periods, pool.map(_fetch_financial_period, periods)))
    current = fetched[current_period]
    financials: dict[str, dict[str, float]] = {}
    for code, current_row in current.items():
        current_revenue = _safe_float(current_row.get("TOTALOPERATEREVE"))
        current_profit = _safe_float(current_row.get("KCFJCXSYJLR"))
        debt_ratio = _safe_float(current_row.get("ZCFZL"))
        if current_revenue is None or current_profit is None or debt_ratio is None:
            continue
        if annual_period and prior_same_period:
            annual_row = fetched[annual_period].get(code)
            prior_row = fetched[prior_same_period].get(code)
            if not annual_row or not prior_row:
                continue
            annual_revenue = _safe_float(annual_row.get("TOTALOPERATEREVE"))
            annual_profit = _safe_float(annual_row.get("KCFJCXSYJLR"))
            prior_revenue = _safe_float(prior_row.get("TOTALOPERATEREVE"))
            prior_profit = _safe_float(prior_row.get("KCFJCXSYJLR"))
            if None in {annual_revenue, annual_profit, prior_revenue, prior_profit}:
                continue
            revenue_ttm = current_revenue + annual_revenue - prior_revenue
            deducted_profit_ttm = current_profit + annual_profit - prior_profit
        else:
            revenue_ttm = current_revenue
            deducted_profit_ttm = current_profit
        financials[code] = {
            "revenue_ttm": float(revenue_ttm),
            "deducted_net_profit_ttm": float(deducted_profit_ttm),
            "debt_ratio": float(debt_ratio),
            "financial_report_period": current_period,
            "financial_source": "东方财富财务主指标",
        }
    return financials, current_period


def _fetch_ths_ttm_financial(code: str) -> dict[str, Any] | None:
    """Fill rare Eastmoney gaps from THS's per-company financial abstract."""
    import akshare as ak

    frame = ak.stock_financial_abstract_new_ths(symbol=code, indicator="按报告期")
    if frame is None or frame.empty:
        return None
    frame = frame.copy()
    frame["_date"] = frame["report_date"].astype(str).str[:10]
    dates = sorted(value for value in frame["_date"].unique() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
    if not dates:
        return None
    current_period = dates[-1]
    current_date = datetime.strptime(current_period, "%Y-%m-%d").date()

    def metric(period: str, name: str) -> float | None:
        rows = frame[(frame["_date"] == period) & (frame["metric_name"] == name)]
        if rows.empty:
            return None
        for value in reversed(rows["value"].tolist()):
            if (parsed := _safe_float(value)) is not None:
                return parsed
        return None

    current_revenue = metric(current_period, "operating_income_total")
    current_profit = metric(current_period, "index_deduct_holder_net_profit")
    debt_ratio = metric(current_period, "assets_debt_ratio")
    if current_revenue is None or current_profit is None or debt_ratio is None:
        return None
    if current_date.month == 12 and current_date.day == 31:
        revenue_ttm = current_revenue
        profit_ttm = current_profit
    else:
        annual_period = date(current_date.year - 1, 12, 31).isoformat()
        prior_same_period = date(current_date.year - 1, current_date.month, current_date.day).isoformat()
        annual_revenue = metric(annual_period, "operating_income_total")
        annual_profit = metric(annual_period, "index_deduct_holder_net_profit")
        prior_revenue = metric(prior_same_period, "operating_income_total")
        prior_profit = metric(prior_same_period, "index_deduct_holder_net_profit")
        if None in {annual_revenue, annual_profit, prior_revenue, prior_profit}:
            return None
        revenue_ttm = current_revenue + annual_revenue - prior_revenue
        profit_ttm = current_profit + annual_profit - prior_profit
    return {
        "revenue_ttm": float(revenue_ttm),
        "deducted_net_profit_ttm": float(profit_ttm),
        "debt_ratio": float(debt_ratio),
        "financial_report_period": current_period,
        "financial_source": "同花顺财务摘要补源",
    }


def _persist_financials(financials: dict[str, dict[str, Any]], report_period: str) -> None:
    if not financials:
        return
    db = DatabaseManager.get_instance()
    now = datetime.now()
    params = [
        {
            "code": code,
            "revenue_ttm": values["revenue_ttm"],
            "deducted_net_profit_ttm": values["deducted_net_profit_ttm"],
            "debt_ratio": values["debt_ratio"],
            "financial_fetched_at": now,
            "report_date": report_period,
        }
        for code, values in financials.items()
    ]
    with db.session_scope() as session:
        session.execute(text(
            "UPDATE stock_meta SET revenue_ttm=:revenue_ttm, "
            "deducted_net_profit_ttm=:deducted_net_profit_ttm, debt_ratio=:debt_ratio, "
            "financial_fetched_at=:financial_fetched_at, report_date=:report_date "
            "WHERE code=:code"
        ), params)


def _market_symbol(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return "bj" + code
    if code.startswith(("6", "5", "9")):
        return "sh" + code
    return "sz" + code


def _normalize_bars(raw: Iterable[Iterable[Any]]) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for row in raw:
        values = list(row)
        if len(values) < 6:
            continue
        opened, closed, high, low = map(_safe_float, values[1:5])
        if None in {opened, closed, high, low} or min(opened, closed, high, low) <= 0:
            continue
        bars.append({
            "date": str(values[0])[:10],
            "open": opened,
            "close": closed,
            "high": high,
            "low": low,
        })
    bars.sort(key=lambda item: item["date"])
    return bars


def _fetch_tencent_bars(code: str, count: int) -> list[dict[str, Any]]:
    symbol = _market_symbol(code)
    if symbol.startswith("bj"):
        return []
    response = _http_session().get(
        TENCENT_KLINE_URL,
        params={"param": f"{symbol},day,,,{count},qfq"},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    bucket = (payload.get("data") or {}).get(symbol) or {}
    return _normalize_bars(bucket.get("qfqday") or bucket.get("day") or [])


def _sina_qfq_factors(symbol: str) -> tuple[list[str], list[float]]:
    response = _http_session().get(
        f"https://finance.sina.com.cn/realstock/company/{symbol}/qfq.js",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        timeout=12,
    )
    response.raise_for_status()
    match = re.search(r"=(\{.*\})", response.text, re.DOTALL)
    if not match:
        return [], []
    import json
    payload = json.loads(match.group(1))
    pairs = sorted(
        (str(item.get("d"))[:10], float(item.get("f")))
        for item in (payload.get("data") or [])
        if item.get("d") and _safe_float(item.get("f")) not in {None, 0}
    )
    return [item[0] for item in pairs], [item[1] for item in pairs]


def _fetch_sina_bars(code: str, count: int) -> list[dict[str, Any]]:
    symbol = _market_symbol(code)
    use_openapi = int(code[-1]) % 2 == 1
    response = _http_session().get(
        SINA_OPENAPI_URL if use_openapi else SINA_KLINE_URL,
        params={"symbol": symbol, "scale": 240, "ma": "no", "datalen": count},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    rows = ((payload.get("result") or {}).get("data") or []) if isinstance(payload, dict) else payload
    raw = [
        [item.get("day"), item.get("open"), item.get("close"), item.get("high"), item.get("low"), item.get("volume")]
        for item in rows
    ]
    bars = _normalize_bars(raw)
    factor_dates, factors = _sina_qfq_factors(symbol)
    if factor_dates:
        adjusted: list[dict[str, Any]] = []
        for bar in bars:
            index = bisect.bisect_right(factor_dates, bar["date"]) - 1
            factor = factors[index] if index >= 0 else factors[0]
            adjusted.append({
                **bar,
                "open": bar["open"] / factor,
                "high": bar["high"] / factor,
                "low": bar["low"] / factor,
                "close": bar["close"] / factor,
            })
        return adjusted
    return bars


def _fetch_adjusted_bars(
    code: str, count: int, allow_tencent: bool = True,
) -> tuple[str, list[dict[str, Any]], str | None]:
    errors: list[str] = []
    if allow_tencent:
        try:
            bars = _fetch_tencent_bars(code, count)
            if bars:
                return code, bars, None
        except Exception as exc:
            errors.append(f"腾讯:{type(exc).__name__}")
    try:
        bars = _fetch_sina_bars(code, count)
        if bars:
            return code, bars, None
    except Exception as exc:
        errors.append(f"新浪:{type(exc).__name__}")
    return code, [], "/".join(errors) or "无行情"


def calculate_atr_screen_metrics(
    bars: list[dict[str, Any]], rules: ScreenRules = ScreenRules(),
) -> dict[str, Any] | None:
    """Calculate the exact SMA-ATR rule; never shrink the 250-day denominator."""
    required = rules.atr_period + rules.long_period + rules.lookback_days - 2
    if len(bars) < required:
        return None
    true_ranges: list[float] = []
    for index, bar in enumerate(bars):
        high = _safe_float(bar.get("high"))
        low = _safe_float(bar.get("low"))
        close = _safe_float(bar.get("close"))
        if high is None or low is None or close is None or high < low or close <= 0:
            return None
        if index == 0:
            true_ranges.append(high - low)
        else:
            previous_close = _safe_float(bars[index - 1].get("close"))
            if previous_close is None or previous_close <= 0:
                return None
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    atr_relative: list[float | None] = [None] * len(bars)
    for index in range(rules.atr_period - 1, len(bars)):
        atr = sum(true_ranges[index - rules.atr_period + 1:index + 1]) / rules.atr_period
        atr_relative[index] = atr / float(bars[index]["close"]) * 100.0
    long_means: list[float | None] = [None] * len(bars)
    for index in range(rules.atr_period + rules.long_period - 2, len(bars)):
        window = atr_relative[index - rules.long_period + 1:index + 1]
        if any(value is None for value in window):
            return None
        long_means[index] = sum(float(value) for value in window) / rules.long_period
    start = len(bars) - rules.lookback_days
    evaluation: list[tuple[float, float]] = []
    for index in range(start, len(bars)):
        current = atr_relative[index]
        mean = long_means[index]
        if current is None or mean is None:
            return None
        evaluation.append((current, mean / rules.warning_divisor))
    if len(evaluation) != rules.lookback_days:
        return None
    qualified_days = sum(current > warning for current, warning in evaluation)
    qualified_ratio = qualified_days / rules.lookback_days * 100.0
    current_atr = float(atr_relative[-1])
    current_mean = float(long_means[-1])
    return {
        "current_atr_pct": current_atr,
        "long_term_mean_pct": current_mean,
        "dynamic_warning_pct": current_mean / rules.warning_divisor,
        "qualified_days": qualified_days,
        "qualified_ratio_pct": qualified_ratio,
        "latest_trade_date": str(bars[-1]["date"]),
        "bar_count": len(bars),
    }


def _write_export(items: list[dict[str, Any]]) -> tuple[str, str]:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    file_id = f"atr-volatility-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.csv"
    path = EXPORT_DIR / file_id
    columns = [
        ("code", "股票代码"), ("name", "股票名称"), ("current_atr_pct", "当前ATR相对波动率(%)"),
        ("long_term_mean_pct", "60日长期均值(%)"), ("dynamic_warning_pct", "动态警戒线(%)"),
        ("qualified_days", "近250日达标天数"), ("qualified_ratio_pct", "近250日达标比例(%)"),
        ("revenue_ttm", "营业收入TTM(元)"), ("deducted_net_profit_ttm", "扣非净利润TTM(元)"),
        ("debt_ratio", "资产负债率(%)"), ("financial_report_period", "财务报告期"),
        ("financial_source", "财务来源"), ("latest_trade_date", "行情日期"),
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([label for _, label in columns])
        for item in items:
            writer.writerow([item.get(key) for key, _ in columns])
    return file_id, f"/api/v1/agent/exports/{file_id}"


def run_atr_volatility_screen(*, refresh_if_stale: bool = True) -> dict[str, Any]:
    rules = ScreenRules()
    expected_trade_date = _expected_latest_kline_date().isoformat()
    try:
        financials, report_period = _build_ttm_financials()
        _persist_financials(financials, report_period)
    except Exception as exc:
        return {
            "success": False, "partial": False, "errors": [f"财务数据刷新失败: {exc}"],
            "data_time": None, "is_stale": None, "freshness_unknown": True, "items": [],
        }
    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        active_rows = session.execute(text(
            "SELECT code, name, ipo_date FROM stock_meta WHERE status='active' ORDER BY code"
        )).mappings().all()
    active = {str(row["code"]): row for row in active_rows}
    ipo_dates = {code: _safe_date(row.get("ipo_date")) for code, row in active.items()}
    recent_listing_count = sum(
        ipo_date is not None and (date.today() - ipo_date).days < 365
        for ipo_date in ipo_dates.values()
    )
    missing_established = [
        code
        for code, row in active.items()
        if code not in financials
        and (
            ipo_dates[code] is None
            or (date.today() - ipo_dates[code]).days >= 365
        )
    ]
    fallback_financial_count = 0
    fallback_errors: dict[str, str] = {}
    if missing_established:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(missing_established))) as pool:
            future_codes = {
                pool.submit(_fetch_ths_ttm_financial, code): code
                for code in missing_established
            }
            for future in concurrent.futures.as_completed(future_codes):
                code = future_codes[future]
                try:
                    fallback = future.result()
                except Exception as exc:
                    fallback_errors[code] = type(exc).__name__
                    continue
                if fallback:
                    financials[code] = fallback
                    fallback_financial_count += 1
                else:
                    fallback_errors[code] = "补源未返回完整TTM字段"
    if fallback_errors:
        return {
            "success": False, "partial": False,
            "errors": [
                f"{len(fallback_errors)} 只已上市满一年股票缺少主源财务，补源仍不完整；已停止筛选。"
            ],
            "failed_symbols": [f"{code}:{reason}" for code, reason in fallback_errors.items()],
            "coverage": {
                "active": len(active),
                "financial_covered": sum(code in financials for code in active),
                "financial_fallback_count": fallback_financial_count,
                "recent_listing_without_ttm": recent_listing_count,
            },
            "data_time": report_period, "is_stale": True, "freshness_unknown": False,
            "items": [],
        }
    candidates: list[str] = []
    excluded_financial = 0
    for code, row in active.items():
        values = financials.get(code)
        if not values:
            excluded_financial += 1
            continue
        if (
            values["revenue_ttm"] > rules.min_revenue_ttm
            and values["deducted_net_profit_ttm"] > rules.min_deducted_profit_ttm
            and values["debt_ratio"] < rules.max_debt_ratio
        ):
            candidates.append(code)
        else:
            excluded_financial += 1
    if not refresh_if_stale:
        return {
            "success": False, "partial": False,
            "errors": ["该筛选必须刷新到最近交易日，refresh_if_stale 不能关闭。"],
            "data_time": None, "is_stale": None, "freshness_unknown": True, "items": [],
        }
    bars_by_code: dict[str, list[dict[str, Any]]] = {}
    failures: dict[str, str] = {}
    allow_tencent = False
    if candidates:
        try:
            allow_tencent = bool(_fetch_tencent_bars(candidates[0], 5))
        except Exception:
            allow_tencent = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=KLINE_FETCH_WORKERS) as pool:
        futures = [
            pool.submit(_fetch_adjusted_bars, code, 340, allow_tencent)
            for code in candidates
        ]
        for future in concurrent.futures.as_completed(futures):
            code, bars, error = future.result()
            # A suspended stock legitimately has no bar on the market's latest
            # date.  The just-fetched provider response is still fresh; only a
            # missing response is a source failure.
            if not bars:
                failures[code] = error or "行情源返回空数据"
                continue
            bars_by_code[code] = bars
    # Full-market completeness is a hard gate. Missing a financially eligible
    # stock could change both the result count and ranking.
    if failures:
        sample = [f"{code}:{message}" for code, message in list(failures.items())[:20]]
        return {
            "success": False, "partial": False,
            "errors": [f"{len(failures)} 只财务合格股票未刷新到 {expected_trade_date}，已停止筛选。"],
            "failed_symbols": sample,
            "coverage": {"active": len(active), "financial_eligible": len(candidates), "fresh_kline": len(bars_by_code)},
            "data_time": expected_trade_date, "is_stale": True, "freshness_unknown": False, "items": [],
        }
    items: list[dict[str, Any]] = []
    insufficient_history = 0
    for code in candidates:
        metrics = calculate_atr_screen_metrics(bars_by_code[code], rules)
        if metrics is None:
            insufficient_history += 1
            continue
        if (
            metrics["qualified_days"] < rules.min_qualified_days
            or metrics["qualified_ratio_pct"] < rules.min_qualified_ratio
        ):
            continue
        values = financials[code]
        items.append({
            "code": code,
            "name": str(active[code]["name"]),
            **metrics,
            **values,
        })
    items.sort(key=lambda item: (-item["qualified_ratio_pct"], item["code"]))
    download_url = file_id = None
    if len(items) > 10:
        file_id, download_url = _write_export(items)
    formula = {
        "true_range": "max(high-low, abs(high-prev_close), abs(low-prev_close))",
        "atr": "TR的14日简单移动平均",
        "atr_relative_pct": "ATR/close*100%",
        "long_term_mean": "ATR相对波动率的60日简单移动平均",
        "dynamic_warning": "long_term_mean/1.27",
        "qualification": "近250日 current_atr_pct > dynamic_warning；至少175日且比例>=70%",
        "financial": "营业收入TTM>5亿元；扣非净利润TTM>0；资产负债率<70%",
    }
    return {
        "success": True, "partial": False, "errors": [], "warnings": [],
        "items": items[:10] if len(items) > 10 else items,
        "total": len(items), "download_url": download_url, "file_id": file_id,
        "data_time": expected_trade_date, "is_stale": False, "freshness_unknown": False,
        "financial_report_period": report_period,
        "coverage": {
            "active": len(active),
            "financial_covered": sum(code in financials for code in active),
            "financial_fallback_count": fallback_financial_count,
            "recent_listing_without_ttm": recent_listing_count,
            "financial_eligible": len(candidates), "fresh_kline": len(bars_by_code),
            "insufficient_history": insufficient_history, "excluded_by_financial": excluded_financial,
        },
        "formula": formula,
        "source": (
            "东方财富财务主指标 + 腾讯前复权日线（单股失败时新浪前复权）"
            if allow_tencent
            else "东方财富财务主指标 + 新浪日线及前复权因子"
        ) + (" + 同花顺财务摘要补源" if fallback_financial_count else ""),
    }


__all__ = ["ScreenRules", "calculate_atr_screen_metrics", "run_atr_volatility_screen"]
