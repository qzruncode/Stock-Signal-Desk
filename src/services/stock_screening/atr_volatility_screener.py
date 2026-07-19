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
import hashlib
import json
import logging
import math
import re
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import requests
from pydantic import ValidationError
from sqlalchemy import text

from src.services.stock_screening.screen_spec import (
    AtrRelativeFrequencyRule,
    QuantitativeScreenSpec,
)
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
KLINE_FETCH_WORKERS = 64
KLINE_SECOND_PASS_WORKERS = 4
MAX_KLINE_SECOND_PASS_SYMBOLS = 50
FINANCIAL_FETCH_WORKERS = 8
MAX_SECONDARY_FINANCIAL_FALLBACKS = 50
_HTTP_LOCAL = threading.local()


def _http_session() -> requests.Session:
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        _HTTP_LOCAL.session = session
    return session


def _reset_http_session() -> None:
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is not None:
        session.close()
    _HTTP_LOCAL.session = None


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
    params = {
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": (
            "SECURITY_CODE,REPORT_DATE,UPDATE_DATE,TOTALOPERATEREVE,"
            "KCFJCXSYJLR,ZCFZL"
        ),
        "filter": f"(REPORT_DATE='{period}')",
        "pageNumber": page,
        "pageSize": 500,
        # SECURITY_CODE alone is not unique because the source keeps revised
        # filings. A stable secondary sort prevents pagination drift.
        "sortColumns": "SECURITY_CODE,UPDATE_DATE",
        "sortTypes": "1,-1",
        "source": "WEB",
        "client": "WEB",
    }
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = _http_session().get(
                EASTMONEY_URL,
                params=params,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("success") is not True:
                raise RuntimeError(str(payload.get("message") or "东财财务数据返回失败"))
            result = payload.get("result") or {}
            return list(result.get("data") or []), int(result.get("pages") or 0)
        except Exception as exc:
            last_error = exc
            _reset_http_session()
            if attempt < 3:
                time.sleep(0.4 * attempt)
    assert last_error is not None
    raise last_error


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
    financials: dict[str, dict[str, Any]] = {}
    for code, current_row in current.items():
        current_revenue = _safe_float(current_row.get("TOTALOPERATEREVE"))
        current_profit = _safe_float(current_row.get("KCFJCXSYJLR"))
        debt_ratio = _safe_float(current_row.get("ZCFZL"))
        values: dict[str, Any] = {
            "financial_report_period": current_period,
            "financial_source": "东方财富财务主指标",
        }
        if annual_period and prior_same_period:
            annual_row = fetched[annual_period].get(code)
            prior_row = fetched[prior_same_period].get(code)
            if annual_row and prior_row:
                annual_revenue = _safe_float(annual_row.get("TOTALOPERATEREVE"))
                annual_profit = _safe_float(annual_row.get("KCFJCXSYJLR"))
                prior_revenue = _safe_float(prior_row.get("TOTALOPERATEREVE"))
                prior_profit = _safe_float(prior_row.get("KCFJCXSYJLR"))
                if None not in {current_revenue, annual_revenue, prior_revenue}:
                    values["revenue_ttm"] = float(current_revenue + annual_revenue - prior_revenue)
                if None not in {current_profit, annual_profit, prior_profit}:
                    values["deducted_net_profit_ttm"] = float(
                        current_profit + annual_profit - prior_profit
                    )
        else:
            if current_revenue is not None:
                values["revenue_ttm"] = float(current_revenue)
            if current_profit is not None:
                values["deducted_net_profit_ttm"] = float(current_profit)
        if debt_ratio is not None:
            values["debt_ratio"] = float(debt_ratio)
        if any(field in values for field in ("revenue_ttm", "deducted_net_profit_ttm", "debt_ratio")):
            financials[code] = values
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
    values: dict[str, Any] = {
        "financial_report_period": current_period,
        "financial_source": "同花顺财务摘要补源",
    }
    if current_date.month == 12 and current_date.day == 31:
        if current_revenue is not None:
            values["revenue_ttm"] = float(current_revenue)
        if current_profit is not None:
            values["deducted_net_profit_ttm"] = float(current_profit)
    else:
        annual_period = date(current_date.year - 1, 12, 31).isoformat()
        prior_same_period = date(current_date.year - 1, current_date.month, current_date.day).isoformat()
        annual_revenue = metric(annual_period, "operating_income_total")
        annual_profit = metric(annual_period, "index_deduct_holder_net_profit")
        prior_revenue = metric(prior_same_period, "operating_income_total")
        prior_profit = metric(prior_same_period, "index_deduct_holder_net_profit")
        if None not in {current_revenue, annual_revenue, prior_revenue}:
            values["revenue_ttm"] = float(current_revenue + annual_revenue - prior_revenue)
        if None not in {current_profit, annual_profit, prior_profit}:
            values["deducted_net_profit_ttm"] = float(current_profit + annual_profit - prior_profit)
    if debt_ratio is not None:
        values["debt_ratio"] = float(debt_ratio)
    return values if any(
        field in values for field in ("revenue_ttm", "deducted_net_profit_ttm", "debt_ratio")
    ) else None


def _quarter_index(report_period: str) -> int | None:
    match = re.fullmatch(r"(\d{4})-(03-31|06-30|09-30|12-31)", report_period)
    if not match:
        return None
    quarter = {"03-31": 1, "06-30": 2, "09-30": 3, "12-31": 4}[match.group(2)]
    return int(match.group(1)) * 4 + quarter


def _fetch_sina_ttm_financial(code: str) -> dict[str, Any] | None:
    """Fetch a coherent latest-quarter + four-quarter TTM row from Sina."""
    from api.v1.endpoints.financials._fetch_financials import _fetch_from_sina

    rows = _fetch_from_sina(code, 8)
    normalized = sorted(
        (
            {
                **row,
                "report_date": str(row.get("report_date") or "")[:10],
            }
            for row in rows
            if isinstance(row, dict) and _quarter_index(str(row.get("report_date") or "")[:10])
        ),
        key=lambda row: row["report_date"],
    )
    if not normalized:
        return None
    latest = normalized[-1]
    values: dict[str, Any] = {
        "financial_report_period": latest["report_date"],
        "financial_source": "新浪财经财务摘要补源",
    }
    debt_ratio = _safe_float(latest.get("debt_ratio"))
    if debt_ratio is not None:
        values["debt_ratio"] = debt_ratio

    last_four = normalized[-4:]
    quarter_indexes = [_quarter_index(row["report_date"]) for row in last_four]
    consecutive = (
        len(last_four) == 4
        and all(index is not None for index in quarter_indexes)
        and quarter_indexes == list(range(int(quarter_indexes[0]), int(quarter_indexes[0]) + 4))
    )
    if consecutive:
        for source_field, target_field in (
            ("revenue", "revenue_ttm"),
            ("deducted_profit", "deducted_net_profit_ttm"),
        ):
            amounts = [_safe_float(row.get(source_field)) for row in last_four]
            if all(amount is not None for amount in amounts):
                values[target_field] = float(sum(float(amount) for amount in amounts))
    return values if any(
        field in values for field in ("revenue_ttm", "deducted_net_profit_ttm", "debt_ratio")
    ) else None


def _fetch_secondary_ttm_financial(
    code: str,
    required_fields: set[str] | None = None,
) -> dict[str, Any] | None:
    """Try independent per-company sources without silently merging rows."""
    required = required_fields or set()
    errors: list[str] = []
    for source_name, fetcher in (
        ("同花顺", _fetch_ths_ttm_financial),
        ("新浪财经", _fetch_sina_ttm_financial),
    ):
        try:
            if result := fetcher(code):
                if required.issubset(result):
                    return result
                errors.append(
                    f"{source_name}:缺少{','.join(sorted(required.difference(result)))}"
                )
                continue
            errors.append(f"{source_name}:空数据")
        except Exception as exc:
            errors.append(f"{source_name}:{type(exc).__name__}")
    raise RuntimeError("/".join(errors))


def _persist_financials(financials: dict[str, dict[str, Any]], report_period: str) -> None:
    if not financials:
        return
    db = DatabaseManager.get_instance()
    now = datetime.now()
    params = [
        {
            "code": code,
            "revenue_ttm": values.get("revenue_ttm"),
            "deducted_net_profit_ttm": values.get("deducted_net_profit_ttm"),
            "debt_ratio": values.get("debt_ratio"),
            "financial_fetched_at": now,
            "report_date": values.get("financial_report_period") or report_period,
        }
        for code, values in financials.items()
    ]
    with db.session_scope() as session:
        session.execute(text(
            "UPDATE stock_meta SET revenue_ttm=COALESCE(:revenue_ttm, revenue_ttm), "
            "deducted_net_profit_ttm=COALESCE(:deducted_net_profit_ttm, deducted_net_profit_ttm), "
            "debt_ratio=COALESCE(:debt_ratio, debt_ratio), "
            "financial_fetched_at=:financial_fetched_at, report_date=:report_date "
            "WHERE code=:code"
        ), params)


def _load_fresh_cached_financials(
    codes: set[str],
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Load only financial rows refreshed today; stale cache is never accepted."""
    if not codes:
        return {}, None
    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        rows = session.execute(text(
            "SELECT code, revenue_ttm, deducted_net_profit_ttm, debt_ratio, "
            "report_date, financial_fetched_at FROM stock_meta WHERE status='active'"
        )).mappings().all()
    cached: dict[str, dict[str, Any]] = {}
    periods: list[str] = []
    for row in rows:
        code = str(row.get("code") or "")
        if code not in codes or _safe_date(row.get("financial_fetched_at")) != date.today():
            continue
        report_period = str(row.get("report_date") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_period):
            continue
        values: dict[str, Any] = {
            "financial_report_period": report_period,
            "financial_source": "本地当日财务缓存",
        }
        for field in ("revenue_ttm", "deducted_net_profit_ttm", "debt_ratio"):
            value = _safe_float(row.get(field))
            if value is not None:
                values[field] = value
        if any(field in values for field in ("revenue_ttm", "deducted_net_profit_ttm", "debt_ratio")):
            cached[code] = values
            periods.append(report_period)
    return cached, max(periods) if periods else None


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
) -> tuple[str, list[dict[str, Any]], str | None, str | None]:
    def attempt(source_name: str, fetcher) -> tuple[list[dict[str, Any]], str | None]:
        last_error: str | None = None
        for retry in range(1, 4):
            try:
                bars = fetcher()
                if bars:
                    return bars, None
                last_error = "空数据"
            except Exception as exc:
                last_error = type(exc).__name__
                _reset_http_session()
            if retry < 3:
                time.sleep(0.3 * retry)
        return [], f"{source_name}:{last_error or '无行情'}"

    errors: list[str] = []
    if allow_tencent:
        bars, error = attempt("腾讯", lambda: _fetch_tencent_bars(code, count))
        if bars:
            return code, bars, None, "腾讯前复权日线"
        if error:
            errors.append(error)
    bars, error = attempt("新浪", lambda: _fetch_sina_bars(code, count))
    if bars:
        return code, bars, None, "新浪日线及前复权因子"
    if error:
        errors.append(error)
    return code, [], "/".join(errors) or "无行情", None


def _moving_average(values: list[float], period: int, mode: str) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    if mode == "sma":
        running = sum(values[:period])
        for index in range(period, len(values)):
            running += values[index] - values[index - period]
            result[index] = running / period
        return result
    alpha = 1.0 / period if mode == "wilder" else 2.0 / (period + 1.0)
    previous = seed
    for index in range(period, len(values)):
        previous = alpha * values[index] + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _compare(left: float, operator: str, right: float) -> bool:
    if operator == "gt":
        return left > right
    if operator == "gte":
        return left >= right
    if operator == "lt":
        return left < right
    if operator == "lte":
        return left <= right
    if operator == "eq":
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)
    raise ValueError(f"不支持的比较符: {operator}")


def _dynamic_threshold(mean: float, rule: AtrRelativeFrequencyRule) -> float:
    if rule.threshold_operator == "divide":
        return mean / rule.threshold_value
    return mean * rule.threshold_value


def _required_bar_count(spec: QuantitativeScreenSpec) -> int:
    rule = spec.technical_rule
    # One leading close is required before the first true-range observation.
    # Treating the first requested bar's high-low as its TR silently ignores an
    # overnight gap and shifts every downstream ATR/baseline window by one day.
    calculation_bars = rule.atr_period + rule.baseline_period + rule.lookback_days - 1
    return max(spec.universe.min_listing_trading_days, calculation_bars)


def calculate_atr_screen_metrics(
    bars: list[dict[str, Any]], rule: AtrRelativeFrequencyRule,
) -> dict[str, Any] | None:
    """Calculate one validated ATR-relative-frequency rule without shrinking its window."""
    required = rule.atr_period + rule.baseline_period + rule.lookback_days - 1
    if len(bars) < required:
        return None
    # EMA/Wilder values depend on their seed.  Always use the exact required
    # tail window so a provider returning extra history cannot change the
    # result.  The first period's SMA is the documented seed.
    bars = bars[-required:]
    closes: list[float] = []
    for bar in bars:
        high = _safe_float(bar.get("high"))
        low = _safe_float(bar.get("low"))
        close = _safe_float(bar.get("close"))
        if high is None or low is None or close is None or high < low or close <= 0:
            return None
        closes.append(close)
    true_ranges: list[float] = []
    for index in range(1, len(bars)):
        high = float(bars[index]["high"])
        low = float(bars[index]["low"])
        previous_close = closes[index - 1]
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    atr_values = _moving_average(true_ranges, rule.atr_period, rule.atr_average)
    # true_ranges[0] belongs to bars[1], therefore an ATR at TR offset
    # atr_period-1 belongs to bar index atr_period.
    relative_start = rule.atr_period
    relative_values = [
        float(atr_values[index]) / closes[index + 1] * 100.0
        for index in range(rule.atr_period - 1, len(true_ranges))
        if atr_values[index] is not None
    ]
    baseline_compact = _moving_average(
        relative_values,
        rule.baseline_period,
        rule.baseline_average,
    )
    atr_relative: list[float | None] = [None] * len(bars)
    baselines: list[float | None] = [None] * len(bars)
    for offset, value in enumerate(relative_values):
        atr_relative[relative_start + offset] = value
        baselines[relative_start + offset] = baseline_compact[offset]
    start = len(bars) - rule.lookback_days
    evaluation: list[tuple[float, float]] = []
    for index in range(start, len(bars)):
        current = atr_relative[index]
        mean = baselines[index]
        if current is None or mean is None:
            return None
        evaluation.append((current, _dynamic_threshold(mean, rule)))
    if len(evaluation) != rule.lookback_days:
        return None
    qualified_days = sum(
        _compare(current, rule.daily_comparison, warning)
        for current, warning in evaluation
    )
    current_atr = float(atr_relative[-1])
    current_mean = float(baselines[-1])
    return {
        "current_atr_pct": current_atr,
        "long_term_mean_pct": current_mean,
        "dynamic_warning_pct": _dynamic_threshold(current_mean, rule),
        "qualified_days": qualified_days,
        "qualified_ratio_pct": qualified_days / rule.lookback_days * 100.0,
        "latest_trade_date": str(bars[-1]["date"]),
        "bar_count": len(bars),
    }


_FIELD_META: dict[str, tuple[str, str]] = {
    "code": ("股票代码", "text"),
    "name": ("股票名称", "text"),
    "current_atr_pct": ("当前ATR相对波动率(%)", "percent"),
    "long_term_mean_pct": ("长期波动均值(%)", "percent"),
    "dynamic_warning_pct": ("动态警戒线(%)", "percent"),
    "qualified_days": ("达标天数", "integer"),
    "qualified_ratio_pct": ("达标比例(%)", "percent"),
    "revenue_ttm": ("营业收入TTM(元)", "currency_yuan"),
    "deducted_net_profit_ttm": ("扣非净利润TTM(元)", "currency_yuan"),
    "debt_ratio": ("资产负债率(%)", "percent"),
    "financial_report_period": ("财务报告期", "date"),
    "financial_source": ("财务来源", "text"),
    "latest_trade_date": ("行情日期", "date"),
}
_FINANCIAL_LABELS = {
    "revenue_ttm": "营业收入TTM",
    "deducted_net_profit_ttm": "扣非净利润TTM",
    "debt_ratio": "资产负债率",
}
_OPERATOR_LABELS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "="}
_AVERAGE_LABELS = {"sma": "简单移动平均", "ema": "指数移动平均", "wilder": "Wilder平滑"}


def _column_defs(spec: QuantitativeScreenSpec) -> list[dict[str, str]]:
    rule = spec.technical_rule
    dynamic_labels = {
        "long_term_mean_pct": f"{rule.baseline_period}日长期波动均值(%)",
        "qualified_days": f"近{rule.lookback_days}日达标天数",
        "qualified_ratio_pct": f"近{rule.lookback_days}日达标比例(%)",
    }
    fields = ["code", "name", *spec.output_fields]
    return [
        {
            "field": field,
            "label": dynamic_labels.get(field, _FIELD_META[field][0]),
            "format": _FIELD_META[field][1],
        }
        for field in fields
    ]


def _write_export(
    items: list[dict[str, Any]], columns: list[dict[str, str]], _fingerprint: str,
) -> tuple[str, str]:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    file_id = f"stock-screen-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.csv"
    path = EXPORT_DIR / file_id
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([column["label"] for column in columns] + ["筛选规格指纹"])
        for item in items:
            writer.writerow([
                _export_cell_value(column["field"], item.get(column["field"]))
                for column in columns
            ] + [_fingerprint])
    return file_id, f"/api/v1/agent/exports/{file_id}"


def _export_cell_value(field: str, value: Any) -> Any:
    """Keep six-digit A-share codes as text when CSV is opened in a spreadsheet."""
    if field != "code":
        return value
    code = str(value or "").strip()
    if re.fullmatch(r"\d{6}", code):
        return f'="{code}"'
    return code


def _spec_fingerprint(spec: QuantitativeScreenSpec) -> str:
    canonical = json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _format_filter_value(field: str, value: float) -> str:
    if field in {"revenue_ttm", "deducted_net_profit_ttm"}:
        return f"{value / 100_000_000:g}亿元"
    if field == "debt_ratio":
        return f"{value:g}%"
    return f"{value:g}"


def _applied_rules(spec: QuantitativeScreenSpec) -> list[str]:
    rule = spec.technical_rule
    threshold = (
        f"长期均值/{rule.threshold_value:g}"
        if rule.threshold_operator == "divide"
        else f"长期均值*{rule.threshold_value:g}"
    )
    qualification_parts: list[str] = []
    if rule.min_qualified_days is not None:
        qualification_parts.append(f"达标天数>={rule.min_qualified_days}")
    if rule.min_qualified_ratio_pct is not None:
        qualification_parts.append(f"达标比例>={rule.min_qualified_ratio_pct:g}%")
    rules = [
        (
            f"TR=max(high-low, abs(high-prev_close), abs(low-prev_close))；"
            f"ATR=TR的{rule.atr_period}日{_AVERAGE_LABELS[rule.atr_average]}；"
            "ATR相对波动率=ATR/close*100%"
        ),
        (
            f"长期波动均值=ATR相对波动率的{rule.baseline_period}日"
            f"{_AVERAGE_LABELS[rule.baseline_average]}；动态线={threshold}；"
            f"日达标条件=ATR相对波动率{_OPERATOR_LABELS[rule.daily_comparison]}动态线"
        ),
        f"统计最近{rule.lookback_days}个交易日；" + "且".join(qualification_parts),
        (
            f"不缩短回看分母；指标预热与完整回看合计至少需要"
            f"{rule.atr_period + rule.baseline_period + rule.lookback_days - 1}根日线；"
            "EMA/Wilder以计算窗口内首个完整周期的SMA为种子"
        ),
        (
            f"股票范围=active A股，市场={','.join(spec.universe.markets)}，"
            f"{'包含' if spec.universe.include_st else '排除'}ST，"
            f"上市交易历史>={spec.universe.min_listing_trading_days}日，前复权"
        ),
    ]
    rules.extend(
        f"{_FINANCIAL_LABELS[item.field]}{_OPERATOR_LABELS[item.operator]}"
        f"{_format_filter_value(item.field, item.value)}"
        for item in spec.financial_filters
    )
    rules.append(f"按{_FIELD_META[spec.sort.field][0]}{('降序' if spec.sort.order == 'desc' else '升序')}")
    return rules


def _market_for_code(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return "bj"
    if code.startswith(("6", "5", "9")):
        return "sh"
    return "sz"


def _is_st_name(name: str) -> bool:
    normalized = re.sub(r"\s+", "", name).upper()
    return bool(re.match(r"^(?:S\*ST|\*ST|ST)", normalized))


def _matches_financial_filters(values: dict[str, Any], spec: QuantitativeScreenSpec) -> bool:
    for condition in spec.financial_filters:
        value = _safe_float(values.get(condition.field))
        if value is None or not _compare(value, condition.operator, condition.value):
            return False
    return True


def _passes_technical_thresholds(metrics: dict[str, Any], rule: AtrRelativeFrequencyRule) -> bool:
    if rule.min_qualified_days is not None and metrics["qualified_days"] < rule.min_qualified_days:
        return False
    if (
        rule.min_qualified_ratio_pct is not None
        and metrics["qualified_ratio_pct"] < rule.min_qualified_ratio_pct
    ):
        return False
    return True


def _failure(
    message: str,
    *,
    stage: str,
    spec: QuantitativeScreenSpec | None = None,
    data_time: str | None = None,
    coverage: dict[str, Any] | None = None,
    failed_symbols: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "partial": False,
        "errors": [message],
        "warnings": warnings or [],
        "failure_stage": stage,
        "screen_spec": spec.model_dump(mode="json") if spec is not None else None,
        "spec_fingerprint": _spec_fingerprint(spec) if spec is not None else None,
        "coverage": coverage or {},
        "failed_symbols": failed_symbols or [],
        "data_time": data_time,
        "is_stale": True if data_time is not None else None,
        "freshness_unknown": data_time is None,
        "items": [],
    }


def run_atr_volatility_screen(
    *,
    screen_spec: dict[str, Any] | None = None,
    refresh_if_stale: bool = True,
    include_matched_codes: bool = False,
) -> dict[str, Any]:
    """Execute a caller-supplied, validated screen and echo the exact normalized spec."""
    try:
        spec = QuantitativeScreenSpec.model_validate(screen_spec)
    except ValidationError as exc:
        return _failure(
            "筛选条件校验失败；未执行任何股票筛选: " + "; ".join(
                error["msg"] for error in exc.errors()[:8]
            ),
            stage="spec_validation",
        )
    if not refresh_if_stale:
        return _failure(
            "专业全市场筛选必须刷新到最近交易日，refresh_if_stale 不能关闭。",
            stage="freshness_policy",
            spec=spec,
        )
    from src.services.data_maintenance import ensure_stock_universe

    try:
        universe_maintenance = ensure_stock_universe(trigger="agent_quantitative_screen")
    except Exception as exc:
        return _failure(
            f"股票基础库自动维护失败: {type(exc).__name__}: {exc}",
            stage="universe_maintenance",
            spec=spec,
        )
    expected_trade_date = _expected_latest_kline_date().isoformat()
    required_bars = _required_bar_count(spec)
    required_financial_fields = spec.required_financial_fields()
    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        rows = session.execute(text(
            "SELECT code, name, ipo_date FROM stock_meta WHERE status='active' ORDER BY code"
        )).mappings().all()
    active = {
        str(row["code"]): row
        for row in rows
        if _market_for_code(str(row["code"])) in spec.universe.markets
        and (spec.universe.include_st or not _is_st_name(str(row["name"])))
    }
    if not active:
        return _failure(
            "股票范围为空；请检查市场和ST范围条件。",
            stage="universe",
            spec=spec,
        )
    ipo_dates = {code: _safe_date(row.get("ipo_date")) for code, row in active.items()}
    definitely_insufficient = {
        code
        for code, ipo_date in ipo_dates.items()
        if ipo_date is not None and (date.today() - ipo_date).days + 1 < required_bars
    }
    eligible_universe = [code for code in active if code not in definitely_insufficient]

    financials: dict[str, dict[str, Any]] = {}
    report_period: str | None = None
    fallback_financial_count = 0
    fallback_financial_sources: set[str] = set()
    cached_financial_count = 0
    primary_financial_available = True
    warnings: list[str] = []
    if required_financial_fields:
        try:
            financials, report_period = _build_ttm_financials()
            _persist_financials(financials, report_period)
        except Exception as exc:
            primary_financial_available = False
            primary_error = f"{type(exc).__name__}: {exc}"
            warnings.append(
                "东方财富全市场财务接口本轮不可用；已自动切换到本日成功刷新并落库的逐股财务快照。"
            )
            try:
                financials, report_period = _load_fresh_cached_financials(
                    set(eligible_universe)
                )
            except Exception as cache_exc:
                return _failure(
                    f"财务主源失败（{primary_error}），读取本日财务快照也失败: {cache_exc}",
                    stage="financial_cache",
                    spec=spec,
                    coverage={
                        "universe": len(active),
                        "history_preexcluded": len(definitely_insufficient),
                        "financial_required_fields": sorted(required_financial_fields),
                    },
                    warnings=warnings,
                )
            cached_financial_count = sum(
                required_financial_fields.issubset(values)
                for values in financials.values()
            )
        missing_required = [
            code
            for code in eligible_universe
            if not required_financial_fields.issubset(financials.get(code, {}))
        ]
        # A per-company source is suitable for filling a small number of holes,
        # not for silently turning a bulk-source outage into thousands of slow
        # network calls. A cold/missing cache therefore fails closed with an
        # explicit coverage stage; a warm same-day cache continues normally.
        max_secondary_fallbacks = MAX_SECONDARY_FINANCIAL_FALLBACKS
        if not primary_financial_available and len(missing_required) > max_secondary_fallbacks:
            return _failure(
                "财务主源不可用，且本日财务快照不足以覆盖本轮股票范围；"
                f"仍有 {len(missing_required)} 只缺少必需字段，已停止筛选。"
                f"主源错误: {primary_error}",
                stage="financial_cache_coverage",
                spec=spec,
                data_time=report_period,
                coverage={
                    "universe": len(active),
                    "history_preexcluded": len(definitely_insufficient),
                    "financial_required_fields": sorted(required_financial_fields),
                    "financial_covered": cached_financial_count,
                    "financial_cache_count": cached_financial_count,
                    "financial_fallback_limit": max_secondary_fallbacks,
                },
                failed_symbols=missing_required[:20],
                warnings=warnings,
            )
        fallback_errors: dict[str, str] = {}
        recovered_fallbacks: dict[str, dict[str, Any]] = {}
        if missing_required:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(missing_required))) as pool:
                future_codes = {
                    pool.submit(
                        _fetch_secondary_ttm_financial,
                        code,
                        required_financial_fields,
                    ): code
                    for code in missing_required
                }
                for future in concurrent.futures.as_completed(future_codes):
                    code = future_codes[future]
                    try:
                        fallback = future.result()
                    except Exception as exc:
                        fallback_errors[code] = type(exc).__name__
                        continue
                    # Keep every row on one coherent report period/source.
                    # Mixing a current-period primary value with an older
                    # fallback value would make the row-level provenance false.
                    if fallback and required_financial_fields.issubset(fallback):
                        financials[code] = fallback
                        recovered_fallbacks[code] = fallback
                        fallback_financial_count += 1
                        if fallback.get("financial_source"):
                            fallback_financial_sources.add(str(fallback["financial_source"]))
                    else:
                        missing_names = sorted(required_financial_fields.difference(fallback or {}))
                        fallback_errors[code] = "缺少" + ",".join(missing_names)
        if recovered_fallbacks and report_period:
            try:
                _persist_financials(recovered_fallbacks, report_period)
            except Exception as exc:
                return _failure(
                    f"补源财务数据写入失败: {exc}",
                    stage="financial_persist",
                    spec=spec,
                    data_time=report_period,
                )
        if fallback_errors:
            return _failure(
                f"{len(fallback_errors)} 只有足够上市历史的股票缺少本轮必需财务字段，补源仍不完整；已停止筛选。",
                stage="financial_coverage",
                spec=spec,
                data_time=report_period,
                coverage={
                    "universe": len(active),
                    "history_preexcluded": len(definitely_insufficient),
                    "financial_required_fields": sorted(required_financial_fields),
                    "financial_covered": sum(
                        required_financial_fields.issubset(financials.get(code, {}))
                        for code in eligible_universe
                    ),
                    "financial_cache_count": cached_financial_count,
                    "financial_fallback_count": fallback_financial_count,
                },
                failed_symbols=[
                    f"{code}:{reason}" for code, reason in list(fallback_errors.items())[:20]
                ],
                warnings=warnings,
            )

    candidates: list[str] = []
    excluded_financial = 0
    for code in eligible_universe:
        values = financials.get(code, {})
        if _matches_financial_filters(values, spec):
            candidates.append(code)
        else:
            excluded_financial += 1

    bars_by_code: dict[str, list[dict[str, Any]]] = {}
    kline_source_counts: dict[str, int] = {}
    failures: dict[str, str] = {}
    kline_retry_recovered = 0
    allow_tencent = False
    if candidates:
        try:
            allow_tencent = bool(_fetch_tencent_bars(candidates[0], 5))
        except Exception:
            allow_tencent = False
    fetch_count = required_bars + 20
    with concurrent.futures.ThreadPoolExecutor(max_workers=KLINE_FETCH_WORKERS) as pool:
        future_codes = {
            pool.submit(_fetch_adjusted_bars, code, fetch_count, allow_tencent): code
            for code in candidates
        }
        for future in concurrent.futures.as_completed(future_codes):
            requested_code = future_codes[future]
            try:
                code, bars, error, kline_source = future.result()
            except Exception as exc:
                failures[requested_code] = type(exc).__name__
                continue
            if not bars:
                failures[code] = error or "行情源返回空数据"
                continue
            bars_by_code[code] = bars
            source_name = kline_source or "未知行情源"
            kline_source_counts[source_name] = kline_source_counts.get(source_name, 0) + 1
    # A handful of concurrent requests can fail transiently even while the
    # same source is healthy. Retry only the small failed tail with low
    # concurrency and both sources enabled; do not rerun the whole market or
    # let one random TLS reset invalidate an otherwise complete screen.
    if failures and len(failures) <= MAX_KLINE_SECOND_PASS_SYMBOLS:
        retry_codes = list(failures)
        time.sleep(0.5)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(KLINE_SECOND_PASS_WORKERS, len(retry_codes))
        ) as pool:
            retry_futures = {
                pool.submit(_fetch_adjusted_bars, code, fetch_count, True): code
                for code in retry_codes
            }
            for future in concurrent.futures.as_completed(retry_futures):
                requested_code = retry_futures[future]
                try:
                    code, bars, error, kline_source = future.result()
                except Exception as exc:
                    failures[requested_code] = type(exc).__name__
                    continue
                if not bars:
                    failures[code] = error or "行情源重试仍返回空数据"
                    continue
                failures.pop(code, None)
                bars_by_code[code] = bars
                source_name = kline_source or "未知行情源"
                kline_source_counts[source_name] = kline_source_counts.get(source_name, 0) + 1
                kline_retry_recovered += 1
    if failures:
        return _failure(
            f"{len(failures)} 只候选股票没有返回行情，完整性校验失败；已停止筛选。",
            stage="kline_coverage",
            spec=spec,
            data_time=expected_trade_date,
            coverage={
                "universe": len(active),
                "history_preexcluded": len(definitely_insufficient),
                "financial_eligible": len(candidates),
                "fresh_kline": len(bars_by_code),
                "kline_source_counts": kline_source_counts,
                "kline_retry_recovered": kline_retry_recovered,
                "financial_cache_count": cached_financial_count,
                "financial_fallback_count": fallback_financial_count,
            },
            failed_symbols=[f"{code}:{message}" for code, message in list(failures.items())[:20]],
            warnings=warnings,
        )

    items: list[dict[str, Any]] = []
    insufficient_history = 0
    for code in candidates:
        bars = bars_by_code[code]
        if len(bars) < spec.universe.min_listing_trading_days:
            insufficient_history += 1
            continue
        metrics = calculate_atr_screen_metrics(bars, spec.technical_rule)
        if metrics is None:
            insufficient_history += 1
            continue
        if not _passes_technical_thresholds(metrics, spec.technical_rule):
            continue
        items.append({
            "code": code,
            "name": str(active[code]["name"]),
            **metrics,
            **financials.get(code, {}),
        })
    items.sort(key=lambda item: str(item["code"]))
    items.sort(
        key=lambda item: item.get(spec.sort.field),
        reverse=spec.sort.order == "desc",
    )
    fingerprint = _spec_fingerprint(spec)
    columns = _column_defs(spec)
    download_url = file_id = None
    if len(items) > spec.preview_limit:
        file_id, download_url = _write_export(items, columns, fingerprint)
    coverage = {
        "universe": len(active),
        "history_preexcluded": len(definitely_insufficient),
        "financial_required_fields": sorted(required_financial_fields),
        "financial_covered": (
            sum(
                required_financial_fields.issubset(financials.get(code, {}))
                for code in eligible_universe
            )
            if required_financial_fields else None
        ),
        "financial_cache_count": cached_financial_count,
        "financial_fallback_count": fallback_financial_count,
        "financial_eligible": len(candidates),
        "fresh_kline": len(bars_by_code),
        "kline_source_counts": kline_source_counts,
        "kline_retry_recovered": kline_retry_recovered,
        "insufficient_history": insufficient_history,
        "excluded_by_financial": excluded_financial,
        "complete": True,
    }
    formula = {
        "true_range": "max(high-low, abs(high-prev_close), abs(low-prev_close))",
        "atr": f"TR的{spec.technical_rule.atr_period}日{spec.technical_rule.atr_average}",
        "atr_relative_pct": "ATR/close*100%",
        "baseline": (
            f"ATR相对波动率的{spec.technical_rule.baseline_period}日"
            f"{spec.technical_rule.baseline_average}"
        ),
        "dynamic_warning": (
            f"baseline {spec.technical_rule.threshold_operator} "
            f"{spec.technical_rule.threshold_value:g}"
        ),
        "daily_comparison": spec.technical_rule.daily_comparison,
    }
    source_parts: list[str] = []
    source_parts.extend(
        f"{source_name}（{count}只）"
        for source_name, count in sorted(kline_source_counts.items())
    )
    if required_financial_fields:
        source_parts.insert(
            0,
            "东方财富财务主指标"
            if primary_financial_available else "本地当日财务快照（主源故障自动切换）",
        )
        if fallback_financial_count:
            source_parts.extend(sorted(fallback_financial_sources))
    result = {
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": warnings,
        "failure_stage": None,
        "screen_spec": spec.model_dump(mode="json"),
        "spec_fingerprint": fingerprint,
        "applied_rules": _applied_rules(spec),
        "formula": formula,
        "columns": columns,
        "items": items[:spec.preview_limit],
        "total": len(items),
        "download_url": download_url,
        "file_id": file_id,
        "data_time": expected_trade_date if candidates else report_period,
        "data_times": {
            "kline_expected_date": expected_trade_date if candidates else None,
            "financial_report_period": report_period,
        },
        "is_stale": False,
        "freshness_unknown": False,
        "financial_report_period": report_period,
        "maintenance": {"stock_universe": universe_maintenance},
        "coverage": coverage,
        "source": " + ".join(source_parts),
    }
    if include_matched_codes:
        result["matched_codes"] = [str(item["code"]) for item in items]
    return result


__all__ = [
    "calculate_atr_screen_metrics",
    "run_atr_volatility_screen",
]
