"""Function group 3 extracted from src/services/stock_screening/atr_volatility_screener.py."""

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

__all__ = ['run_atr_volatility_screen']

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
            "筛选条件校验失败；未执行任何股票筛选: " + "; ".join(error["msg"] for error in exc.errors()[:8]),
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
        rows = (
            session.execute(text("SELECT code, name, ipo_date FROM stock_meta WHERE status='active' ORDER BY code"))
            .mappings()
            .all()
        )
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
            warnings.append("东方财富全市场财务接口本轮不可用；已自动切换到本日成功刷新并落库的逐股财务快照。")
            try:
                financials, report_period = _load_fresh_cached_financials(set(eligible_universe))
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
            cached_financial_count = sum(required_financial_fields.issubset(values) for values in financials.values())
        missing_required = [
            code for code in eligible_universe if not required_financial_fields.issubset(financials.get(code, {}))
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
                        required_financial_fields.issubset(financials.get(code, {})) for code in eligible_universe
                    ),
                    "financial_cache_count": cached_financial_count,
                    "financial_fallback_count": fallback_financial_count,
                },
                failed_symbols=[f"{code}:{reason}" for code, reason in list(fallback_errors.items())[:20]],
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
            pool.submit(_fetch_adjusted_bars, code, fetch_count, allow_tencent): code for code in candidates
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
            retry_futures = {pool.submit(_fetch_adjusted_bars, code, fetch_count, True): code for code in retry_codes}
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
        items.append(
            {
                "code": code,
                "name": str(active[code]["name"]),
                **metrics,
                **financials.get(code, {}),
            }
        )
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
            sum(required_financial_fields.issubset(financials.get(code, {})) for code in eligible_universe)
            if required_financial_fields
            else None
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
            f"ATR相对波动率的{spec.technical_rule.baseline_period}日" f"{spec.technical_rule.baseline_average}"
        ),
        "dynamic_warning": (
            f"baseline {spec.technical_rule.threshold_operator} " f"{spec.technical_rule.threshold_value:g}"
        ),
        "daily_comparison": spec.technical_rule.daily_comparison,
    }
    source_parts: list[str] = []
    source_parts.extend(f"{source_name}（{count}只）" for source_name, count in sorted(kline_source_counts.items()))
    if required_financial_fields:
        source_parts.insert(
            0,
            "东方财富财务主指标" if primary_financial_available else "本地当日财务快照（主源故障自动切换）",
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
        "items": items[: spec.preview_limit],
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
