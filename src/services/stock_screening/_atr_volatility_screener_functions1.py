"""Function group 1 extracted from src/services/stock_screening/atr_volatility_screener.py."""

from __future__ import annotations

from src.services.stock_screening.atr_volatility_screener import (
    bisect,
    concurrent,
    csv,
    hashlib,
    json,
    logging,
    math,
    re,
    threading,
    time,
    uuid,
    date,
    datetime,
    Path,
    Any,
    Iterable,
    requests,
    ValidationError,
    text,
    AtrRelativeFrequencyRule,
    QuantitativeScreenSpec,
    DatabaseManager,
    _expected_latest_kline_date,
    logger,
    EASTMONEY_URL,
    TENCENT_KLINE_URL,
    SINA_KLINE_URL,
    SINA_OPENAPI_URL,
    EXPORT_DIR,
    KLINE_FETCH_WORKERS,
    KLINE_SECOND_PASS_WORKERS,
    MAX_KLINE_SECOND_PASS_SYMBOLS,
    FINANCIAL_FETCH_WORKERS,
    MAX_SECONDARY_FINANCIAL_FALLBACKS,
    _HTTP_LOCAL,
    _FIELD_META,
    _FINANCIAL_LABELS,
    _OPERATOR_LABELS,
    _AVERAGE_LABELS,
    __all__,
 )

__all__ = ['_http_session', '_reset_http_session', '_safe_float', '_safe_date', '_report_dates', '_fetch_financial_page', '_fetch_financial_period', 'fetch_financial_period_snapshot', '_build_ttm_financials', '_fetch_ths_ttm_financial', '_quarter_index', '_fetch_sina_ttm_financial', '_fetch_secondary_ttm_financial', '_persist_financials', '_load_fresh_cached_financials', '_market_symbol', '_normalize_bars', '_fetch_tencent_bars', '_sina_qfq_factors']

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
        if date(year, month, day) <= today and (today - date(year, month, day)).days >= 25
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
        "columns": ("SECURITY_CODE,REPORT_DATE,UPDATE_DATE,TOTALOPERATEREVE," "PARENTNETPROFIT,KCFJCXSYJLR,ZCFZL"),
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
            "REPORT_DATE",
            "UPDATE_DATE",
            "TOTALOPERATEREVE",
            "PARENTNETPROFIT",
            "KCFJCXSYJLR",
            "ZCFZL",
        ):
            if row.get(field) is not None:
                merged[field] = row[field]
    return by_code

def fetch_financial_period_snapshot(period: str) -> dict[str, dict[str, Any]]:
    """Return one complete report-period snapshot from the financial provider.

    This is the shared provider boundary for workflows that need an exact
    fiscal-year value.  Callers own their cache policy; the low-level fetch and
    revised-filing merge semantics remain identical to the all-market screener.
    """
    if not re.fullmatch(r"\d{4}-(?:03-31|06-30|09-30|12-31)", period):
        raise ValueError("period must be a supported financial report date")
    return _fetch_financial_period(period)

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
                    values["deducted_net_profit_ttm"] = float(current_profit + annual_profit - prior_profit)
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
    return (
        values if any(field in values for field in ("revenue_ttm", "deducted_net_profit_ttm", "debt_ratio")) else None
    )

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
    return (
        values if any(field in values for field in ("revenue_ttm", "deducted_net_profit_ttm", "debt_ratio")) else None
    )

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
                errors.append(f"{source_name}:缺少{','.join(sorted(required.difference(result)))}")
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
        session.execute(
            text(
                "UPDATE stock_meta SET revenue_ttm=COALESCE(:revenue_ttm, revenue_ttm), "
                "deducted_net_profit_ttm=COALESCE(:deducted_net_profit_ttm, deducted_net_profit_ttm), "
                "debt_ratio=COALESCE(:debt_ratio, debt_ratio), "
                "financial_fetched_at=:financial_fetched_at, report_date=:report_date "
                "WHERE code=:code"
            ),
            params,
        )

def _load_fresh_cached_financials(
    codes: set[str],
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Load only financial rows refreshed today; stale cache is never accepted."""
    if not codes:
        return {}, None
    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        rows = (
            session.execute(
                text(
                    "SELECT code, revenue_ttm, deducted_net_profit_ttm, debt_ratio, "
                    "report_date, financial_fetched_at FROM stock_meta WHERE status='active'"
                )
            )
            .mappings()
            .all()
        )
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
        bars.append(
            {
                "date": str(values[0])[:10],
                "open": opened,
                "close": closed,
                "high": high,
                "low": low,
            }
        )
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
