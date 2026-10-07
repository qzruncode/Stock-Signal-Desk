# -*- coding: utf-8 -*-
"""All-market ATR-relative-volatility screener.

The model never fetches or calculates these figures.  This service refreshes
the financial candidate universe, retrieves adjusted daily bars, applies the
formula exactly, and returns only rows that pass every hard condition.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.services.stock_screening.screen_spec import (
    AtrRelativeFrequencyRule,
    QuantitativeScreenSpec,
)
from src.tools._kline import _expected_latest_kline_date

logger = logging.getLogger(__name__)

EXPORT_DIR = (
    Path(__file__).resolve().parents[3] / "data" / "exports" / "stock-screening"
)


_FIELD_META: dict[str, tuple[str, str]] = {
    "code": ("股票代码", "text"),
    "name": ("股票名称", "text"),
    "current_atr_pct": ("当前ATR相对波动率(%)", "percent"),
    "long_term_mean_pct": ("长期波动均值(%)", "percent"),
    "dynamic_warning_pct": ("动态警戒线(%)", "percent"),
    "qualified_days": ("达标天数", "integer"),
    "qualified_ratio_pct": ("达标比例(%)", "percent"),
    "revenue_ttm": ("营业收入TTM(元)", "currency_yuan"),
    "parent_net_profit_ttm": ("归母净利润TTM(元)", "currency_yuan"),
    "deducted_net_profit_ttm": ("扣非净利润TTM(元)", "currency_yuan"),
    "debt_ratio": ("资产负债率(%)", "percent"),
    "financial_report_period": ("财务报告期", "date"),
    "financial_source": ("财务来源", "text"),
    "latest_trade_date": ("行情日期", "date"),
}

_FINANCIAL_LABELS = {
    "revenue_ttm": "营业收入TTM",
    "parent_net_profit_ttm": "归母净利润TTM",
    "deducted_net_profit_ttm": "扣非净利润TTM",
    "debt_ratio": "资产负债率",
}

_OPERATOR_LABELS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "="}

_AVERAGE_LABELS = {"sma": "简单移动平均", "ema": "指数移动平均", "wilder": "Wilder平滑"}

__all__ = [
    "calculate_atr_screen_metrics",
    "run_atr_volatility_screen",
]


from src.services.market_data_client import get_market_data_client
from src.services.stock_screening.data import financial_rows, daily_rows


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
        true_ranges.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
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
                [
                    _export_cell_value(column["field"], item.get(column["field"]))
                    for column in columns
                ]
                + [_fingerprint]
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
    canonical = json.dumps(
        spec.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
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
    rules.append(
        f"按{_FIELD_META[spec.sort.field][0]}{('降序' if spec.sort.order == 'desc' else '升序')}"
    )
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


def _matches_financial_filters(
    values: dict[str, Any], spec: QuantitativeScreenSpec
) -> bool:
    for condition in spec.financial_filters:
        value = _safe_float(values.get(condition.field))
        if value is None or not _compare(value, condition.operator, condition.value):
            return False
    return True


def _passes_technical_thresholds(
    metrics: dict[str, Any], rule: AtrRelativeFrequencyRule
) -> bool:
    if (
        rule.min_qualified_days is not None
        and metrics["qualified_days"] < rule.min_qualified_days
    ):
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
    include_all_items: bool = False,
) -> dict[str, Any]:
    """Execute a caller-supplied, validated screen and echo the exact normalized spec."""
    try:
        spec = QuantitativeScreenSpec.model_validate(screen_spec)
    except ValidationError as exc:
        return _failure(
            "筛选条件校验失败；未执行任何股票筛选: "
            + "; ".join(error["msg"] for error in exc.errors()[:8]),
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
        universe_maintenance = ensure_stock_universe(
            trigger="agent_quantitative_screen"
        )
    except Exception as exc:
        return _failure(
            f"股票基础库自动维护失败: {type(exc).__name__}: {exc}",
            stage="universe_maintenance",
            spec=spec,
        )
    expected_trade_date = _expected_latest_kline_date().isoformat()
    required_bars = _required_bar_count(spec)
    required_financial_fields = spec.required_financial_fields()
    rows = get_market_data_client().securities(page_size=10000)["items"]
    scoped_codes = set(spec.universe.codes) if spec.universe.codes is not None else None
    active = {
        str(row["code"]): row
        for row in rows
        if (scoped_codes is None or str(row["code"]) in scoped_codes)
        and _market_for_code(str(row["code"])) in spec.universe.markets
        and (spec.universe.include_st or not _is_st_name(str(row["name"])))
    }
    if not active:
        return _failure(
            "股票范围为空；请检查市场、ST和分组范围条件。",
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
    warnings: list[str] = []
    if required_financial_fields:
        try:
            financials, report_period = financial_rows(eligible_universe)
        except Exception as exc:
            return _failure(
                f"独立数据服务尚未提供达标财务快照: {exc}",
                stage="data_readiness",
                spec=spec,
                coverage={"universe": len(active), "eligible": len(eligible_universe)},
            )
        missing = [
            code
            for code in eligible_universe
            if not required_financial_fields.issubset(financials.get(code, {}))
        ]
        if missing:
            return _failure(
                "财务字段覆盖不足，已停止筛选；数据服务将继续补采。",
                stage="financial_coverage",
                spec=spec,
                failed_symbols=missing[:20],
                data_time=report_period,
                coverage={
                    "universe": len(active),
                    "financial_covered": len(eligible_universe) - len(missing),
                },
            )

    candidates: list[str] = []
    excluded_financial = 0
    for code in eligible_universe:
        values = financials.get(code, {})
        if _matches_financial_filters(values, spec):
            candidates.append(code)
        else:
            excluded_financial += 1

    bars_by_code, failures, kline_source_counts = daily_rows(
        candidates, required_bars + 20
    )
    kline_retry_recovered = 0
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
            failed_symbols=[
                f"{code}:{message}" for code, message in list(failures.items())[:20]
            ],
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
            sum(
                required_financial_fields.issubset(financials.get(code, {}))
                for code in eligible_universe
            )
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
    warning_formula = (
        f"固定ATR相对波动率阈值 {spec.technical_rule.volatility_threshold_pct:g}%"
        if spec.technical_rule.volatility_threshold_pct is not None
        else f"baseline {spec.technical_rule.threshold_operator} {spec.technical_rule.threshold_value:g}"
    )
    formula = {
        "true_range": "max(high-low, abs(high-prev_close), abs(low-prev_close))",
        "atr": f"TR的{spec.technical_rule.atr_period}日{spec.technical_rule.atr_average}",
        "atr_relative_pct": "ATR/close*100%",
        "baseline": (
            f"ATR相对波动率的{spec.technical_rule.baseline_period}日"
            f"{spec.technical_rule.baseline_average}"
        ),
        "dynamic_warning": warning_formula,
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
            "独立数据服务 · 同报告期财务快照",
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
        # The normal Agent/tool contract stays preview-only.  The settings
        # page can explicitly request all rows for a complete result table;
        # matched_codes has always remained full for save-to-group actions.
        "items": items if include_all_items else items[: spec.preview_limit],
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
