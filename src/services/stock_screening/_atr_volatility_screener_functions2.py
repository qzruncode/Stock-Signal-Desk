"""Function group 2 extracted from src/services/stock_screening/atr_volatility_screener.py."""

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

__all__ = ['_fetch_sina_bars', '_fetch_adjusted_bars', '_moving_average', '_compare', '_dynamic_threshold', '_required_bar_count', 'calculate_atr_screen_metrics', '_column_defs', '_write_export', '_export_cell_value', '_spec_fingerprint', '_format_filter_value', '_applied_rules', '_market_for_code', '_is_st_name', '_matches_financial_filters', '_passes_technical_thresholds', '_failure']

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
            adjusted.append(
                {
                    **bar,
                    "open": bar["open"] / factor,
                    "high": bar["high"] / factor,
                    "low": bar["low"] / factor,
                    "close": bar["close"] / factor,
                }
            )
        return adjusted
    return bars

def _fetch_adjusted_bars(
    code: str,
    count: int,
    allow_tencent: bool = True,
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
    if rule.volatility_threshold_pct is not None:
        return rule.volatility_threshold_pct
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
    bars: list[dict[str, Any]],
    rule: AtrRelativeFrequencyRule,
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
    qualified_days = sum(_compare(current, rule.daily_comparison, warning) for current, warning in evaluation)
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

def _column_defs(spec: QuantitativeScreenSpec) -> list[dict[str, str]]:
    rule = spec.technical_rule
    dynamic_labels = {
        "long_term_mean_pct": f"{rule.baseline_period}日长期波动均值(%)",
        "dynamic_warning_pct": (
            "ATR相对波动率阈值(%)"
            if rule.volatility_threshold_pct is not None
            else "动态警戒线(%)"
        ),
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
    items: list[dict[str, Any]],
    columns: list[dict[str, str]],
    _fingerprint: str,
) -> tuple[str, str]:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    file_id = f"stock-screen-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.csv"
    path = EXPORT_DIR / file_id
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([column["label"] for column in columns] + ["筛选规格指纹"])
        for item in items:
            writer.writerow(
                [_export_cell_value(column["field"], item.get(column["field"])) for column in columns] + [_fingerprint]
            )
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
    if field in {"revenue_ttm", "parent_net_profit_ttm", "deducted_net_profit_ttm"}:
        return f"{value / 100_000_000:g}亿元"
    if field == "debt_ratio":
        return f"{value:g}%"
    return f"{value:g}"

def _applied_rules(spec: QuantitativeScreenSpec) -> list[str]:
    rule = spec.technical_rule
    if rule.volatility_threshold_pct is not None:
        threshold_label = "固定阈值"
        threshold = f"{rule.volatility_threshold_pct:g}%"
    else:
        threshold_label = "动态线"
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
            f"{_AVERAGE_LABELS[rule.baseline_average]}；{threshold_label}={threshold}；"
            f"日达标条件=ATR相对波动率{_OPERATOR_LABELS[rule.daily_comparison]}{threshold_label}"
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
    if spec.universe.codes is not None:
        rules.append(f"筛选范围=股票分组（{len(spec.universe.codes)}只）")
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
    if rule.min_qualified_ratio_pct is not None and metrics["qualified_ratio_pct"] < rule.min_qualified_ratio_pct:
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
