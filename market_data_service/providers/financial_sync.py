"""Existing coherent financial-period selection and TTM normalization, without API state."""

from __future__ import annotations
from contextvars import copy_context
import concurrent.futures
import logging
import re
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)
_REPORT_PERIOD_CANDIDATES = ((12, 31), (9, 30), (6, 30), (3, 31))
_MAX_REPORT_PERIODS = 12
_REPORT_PERIOD_BUFFER_DAYS = 25
_PERIOD_FETCH_WORKERS = 3
_REQUIRED_UPDATE_FIELDS = frozenset(
    {
        "revenue_latest",
        "net_profit_latest",
        "revenue_ttm",
        "parent_net_profit_ttm",
        "deducted_net_profit_ttm",
        "debt_ratio",
    }
)


def _set(**values):
    logger.info("Financial source progress: %s", values)


def latest_report_period(reference: date | None = None) -> str:
    """返回当前最可能已经披露的最近报告期（YYYYMMDD）。"""
    today = reference or date.today()
    candidates: list[date] = []
    for year in (today.year, today.year - 1):
        for month, day in _REPORT_PERIOD_CANDIDATES:
            report_date = date(year, month, day)
            if (
                report_date <= today
                and (today - report_date).days >= _REPORT_PERIOD_BUFFER_DAYS
            ):
                candidates.append(report_date)
    if not candidates:
        for year in (today.year, today.year - 1):
            for month, day in _REPORT_PERIOD_CANDIDATES:
                report_date = date(year, month, day)
                if report_date <= today:
                    candidates.append(report_date)
    return max(candidates).strftime("%Y%m%d")


def _report_periods(
    reference: date | None = None, limit: int = _MAX_REPORT_PERIODS
) -> list[str]:
    """返回从最近报告期开始的回溯列表，顺序为新到旧。"""
    today = reference or date.today()
    latest = datetime.strptime(latest_report_period(today), "%Y%m%d").date()
    return _periods_from_start(latest, limit)


def _periods_from_start(latest: date, limit: int = _MAX_REPORT_PERIODS) -> list[str]:
    """从指定的报告期开始生成回溯列表。"""
    periods: list[str] = []
    for year in range(latest.year, latest.year - 8, -1):
        for month, day in _REPORT_PERIOD_CANDIDATES:
            report_date = date(year, month, day)
            if report_date > latest:
                continue
            periods.append(report_date.isoformat())
            if len(periods) >= limit:
                return periods
    return periods


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return (
        parsed
        if parsed == parsed and parsed not in (float("inf"), float("-inf"))
        else None
    )


def _normalize_code(value: Any) -> str:
    code = str(value or "").strip()
    if code.endswith(".0"):
        code = code[:-2]
    if code.isdigit() and len(code) < 6:
        code = code.zfill(6)
    return code


def _fetch_period_snapshot(period: str) -> dict[str, dict[str, Any]]:
    """读取一个报告期的全市场快照。"""
    from market_data_service.providers.financial_period import (
        fetch_financial_period_snapshot,
    )

    return fetch_financial_period_snapshot(period)


def _has_financial_value(row: dict[str, Any]) -> bool:
    return any(
        _safe_float(row.get(field)) is not None
        for field in ("TOTALOPERATEREVE", "PARENTNETPROFIT", "KCFJCXSYJLR", "ZCFZL")
    )


def _collect_period_rows(
    active_codes: set[str],
    periods: list[str],
) -> tuple[
    dict[str, tuple[str, dict[str, Any]]],
    dict[str, dict[str, dict[str, Any]]],
    set[str],
    list[str],
]:
    """读取多个报告期并为每只股票选出最新可用的一期。"""
    if not periods:
        return {}, {}, set(), ["没有可用的报告期候选"]

    _set(message=f"正在读取 {len(periods)} 个报告期的全市场财务快照")
    rows_by_period: dict[str, dict[str, dict[str, Any]]] = {}
    source_codes: set[str] = set()
    errors: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(_PERIOD_FETCH_WORKERS, len(periods))
    ) as pool:
        future_periods = {
            pool.submit(copy_context().run, _fetch_period_snapshot, period): period
            for period in periods
        }
        for future in concurrent.futures.as_completed(future_periods):
            period = future_periods[future]
            try:
                rows = future.result()
            except Exception as exc:
                errors.append(f"{period}: {type(exc).__name__}: {exc}")
                _set(message=f"报告期 {period} 读取失败，继续检查其他报告期")
                continue
            normalized_rows: dict[str, dict[str, Any]] = {}
            for raw_code, raw_row in (rows or {}).items():
                code = _normalize_code(raw_code)
                if not re.fullmatch(r"\d{6}", code) or not isinstance(raw_row, dict):
                    continue
                row = dict(raw_row)
                row["SECURITY_CODE"] = code
                normalized_rows[code] = row
            rows_by_period[period] = normalized_rows
            source_codes.update(normalized_rows)
            _set(
                message=f"已读取报告期 {period}，收到 {len(normalized_rows)} 条，继续归并最新一期"
            )

    selected: dict[str, tuple[str, dict[str, Any]]] = {}
    # periods 本身是新到旧；只在尚未选中时写入，保证同一股票只取最近一期。
    for period in periods:
        for code, row in rows_by_period.get(period, {}).items():
            if (
                code in active_codes
                and code not in selected
                and _has_financial_value(row)
            ):
                selected[code] = (period, row)
    _set(message=f"批量财务源已覆盖 {len(selected)} / {len(active_codes)} 只股票")
    return selected, rows_by_period, source_codes, errors


def _report_period_parts(period: str) -> tuple[str | None, str | None]:
    """返回某个季度对应的上年年报和上年同季度。"""
    try:
        parsed = datetime.strptime(period[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None, None
    return (
        date(parsed.year - 1, 12, 31).isoformat(),
        date(parsed.year - 1, parsed.month, parsed.day).isoformat(),
    )


def _ttm_fields(
    code: str,
    period: str,
    current: dict[str, Any],
    rows_by_period: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, float]:
    """仅在同一指标的跨期数据齐全时计算 TTM。"""
    result: dict[str, float] = {}
    current_values = {
        "revenue_ttm": _safe_float(current.get("TOTALOPERATEREVE")),
        "parent_net_profit_ttm": _safe_float(current.get("PARENTNETPROFIT")),
        "deducted_net_profit_ttm": _safe_float(current.get("KCFJCXSYJLR")),
    }
    if period.endswith("-12-31"):
        return {
            key: value for key, value in current_values.items() if value is not None
        }

    annual_period, prior_same_period = _report_period_parts(period)
    if not annual_period or not prior_same_period:
        return {}
    annual = rows_by_period.get(annual_period, {}).get(code)
    prior_same = rows_by_period.get(prior_same_period, {}).get(code)
    if not annual or not prior_same:
        return {}

    fields = (
        ("revenue_ttm", "TOTALOPERATEREVE"),
        ("parent_net_profit_ttm", "PARENTNETPROFIT"),
        ("deducted_net_profit_ttm", "KCFJCXSYJLR"),
    )
    for target, source in fields:
        current_value = current_values[target]
        annual_value = _safe_float(annual.get(source))
        prior_value = _safe_float(prior_same.get(source))
        if None not in (current_value, annual_value, prior_value):
            result[target] = float(current_value + annual_value - prior_value)
    return result


def _row_to_update(
    code: str,
    period: str,
    row: dict[str, Any],
    rows_by_period: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    """把批量源的一期报告转换为 stock_meta 更新字段。"""
    report_date = str(row.get("REPORT_DATE") or period)[:10]
    values: dict[str, Any] = {
        "report_date": report_date,
        "financial_fetched_at": datetime.now(),
    }
    latest_fields = (
        ("revenue_latest", "TOTALOPERATEREVE"),
        ("net_profit_latest", "PARENTNETPROFIT"),
        ("deducted_net_profit_latest", "KCFJCXSYJLR"),
        ("debt_ratio", "ZCFZL"),
    )
    for target, source in latest_fields:
        value = _safe_float(row.get(source))
        if value is not None and target in {
            "revenue_latest",
            "net_profit_latest",
            "debt_ratio",
        }:
            values[target] = value
    values.update(_ttm_fields(code, period, row, rows_by_period))
    return values


def _is_complete_update(fields: dict[str, Any]) -> bool:
    return _REQUIRED_UPDATE_FIELDS.issubset(fields)


def _quarter_index(report_date: str) -> int | None:
    match = re.fullmatch(r"(\d{4})-(03-31|06-30|09-30|12-31)", report_date[:10])
    if not match:
        return None
    quarter = {"03-31": 1, "06-30": 2, "09-30": 3, "12-31": 4}[match.group(2)]
    return int(match.group(1)) * 4 + quarter


def _fallback_update_from_financials(code: str) -> dict[str, Any] | None:
    """用现有逐股财务聚合源补齐批量源未覆盖的股票。"""
    from market_data_service.providers.financials import get_financials

    result = get_financials(code, periods=8, use_cache=False)
    items = [
        item
        for item in result.get("items") or []
        if isinstance(item, dict) and item.get("report_date")
    ]
    if not items:
        return None
    items.sort(key=lambda item: str(item.get("report_date") or ""))
    latest = items[-1]
    values: dict[str, Any] = {
        "report_date": str(latest.get("report_date"))[:10],
        "financial_fetched_at": datetime.now(),
    }
    for target, source in (
        ("revenue_latest", "revenue"),
        ("net_profit_latest", "parent_net_profit"),
        ("debt_ratio", "debt_ratio"),
        ("operating_cf_latest", "operating_cash_flow"),
    ):
        value = _safe_float(latest.get(source))
        if value is not None:
            values[target] = value
    if "net_profit_latest" not in values:
        value = _safe_float(latest.get("net_profit"))
        if value is not None:
            values["net_profit_latest"] = value

    latest_index = _quarter_index(str(latest.get("report_date"))[:10])
    last_four = items[-4:]
    indexes = [_quarter_index(str(item.get("report_date"))[:10]) for item in last_four]
    consecutive = (
        len(last_four) == 4
        and latest_index is not None
        and all(index is not None for index in indexes)
        and indexes == list(range(int(indexes[0]), int(indexes[0]) + 4))
    )
    if consecutive:
        for target, source in (
            ("revenue_ttm", "revenue"),
            ("parent_net_profit_ttm", "parent_net_profit"),
            ("deducted_net_profit_ttm", "deducted_profit"),
        ):
            amounts = [_safe_float(item.get(source)) for item in last_four]
            if all(amount is not None for amount in amounts):
                values[target] = float(sum(float(amount) for amount in amounts))
    if any(
        key in values
        for key in (
            "revenue_latest",
            "net_profit_latest",
            "debt_ratio",
            "operating_cf_latest",
        )
    ):
        return values
    return None
