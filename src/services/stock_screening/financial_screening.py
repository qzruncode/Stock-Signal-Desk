# -*- coding: utf-8 -*-
"""Financial-only stock screening.

This executor deliberately does not call the ATR screener.  Financial
conditions need a financial snapshot and a complete coverage report; adding
an unrelated technical rule merely because the legacy quantitative schema
requires one makes the result look more precise than the requested rule.
"""

from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import json
import logging
import re
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from pydantic import ValidationError

from src.services.data_maintenance import ensure_stock_universe
from src.storage import DatabaseManager

from .atr_volatility_screener import (
    EXPORT_DIR,
    FINANCIAL_FETCH_WORKERS,
    MAX_SECONDARY_FINANCIAL_FALLBACKS,
    _build_ttm_financials,
    _fetch_secondary_ttm_financial,
    _is_st_name,
    _load_fresh_cached_financials,
    _market_for_code,
    _matches_financial_filters,
    _persist_financials,
    _safe_date,
    _safe_float,
)
from .screen_spec import FinancialScreenSpec

logger = logging.getLogger(__name__)

_FIELD_META: dict[str, tuple[str, str]] = {
    "code": ("股票代码", "text"),
    "name": ("股票名称", "text"),
    "revenue_ttm": ("营业收入TTM(元)", "currency_yuan"),
    "parent_net_profit_ttm": ("归母净利润TTM(元)", "currency_yuan"),
    "deducted_net_profit_ttm": ("扣非净利润TTM(元)", "currency_yuan"),
    "debt_ratio": ("资产负债率(%)", "percent"),
    "financial_report_period": ("财务报告期", "date"),
    "financial_source": ("财务来源", "text"),
}
_FINANCIAL_LABELS = {
    "revenue_ttm": "营业收入TTM",
    "parent_net_profit_ttm": "归母净利润TTM",
    "deducted_net_profit_ttm": "扣非净利润TTM",
    "debt_ratio": "资产负债率",
}
_OPERATOR_LABELS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "="}


def _spec_fingerprint(spec: FinancialScreenSpec) -> str:
    canonical = json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _failure(
    message: str,
    *,
    stage: str,
    spec: FinancialScreenSpec | None = None,
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
        "matched_codes": [],
        "total": 0,
    }


def _column_defs(spec: FinancialScreenSpec) -> list[dict[str, str]]:
    fields = ["code", "name", *spec.output_fields]
    return [
        {
            "field": field,
            "label": _FIELD_META[field][0],
            "format": _FIELD_META[field][1],
        }
        for field in fields
    ]


def _export_cell_value(field: str, value: Any) -> Any:
    if field != "code":
        return value
    code = str(value or "").strip()
    if re.fullmatch(r"\d{6}", code):
        return f'="{code}"'
    return code


def _write_export(
    items: list[dict[str, Any]],
    columns: list[dict[str, str]],
    fingerprint: str,
) -> tuple[str, str]:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    file_id = f"stock-screen-financial-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.csv"
    path = EXPORT_DIR / file_id
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([column["label"] for column in columns] + ["筛选规格指纹"])
        for item in items:
            writer.writerow(
                [_export_cell_value(column["field"], item.get(column["field"])) for column in columns]
                + [fingerprint]
            )
    return file_id, f"/api/v1/agent/exports/{file_id}"


def _format_filter_value(field: str, value: float) -> str:
    if field in {"revenue_ttm", "parent_net_profit_ttm", "deducted_net_profit_ttm"}:
        return f"{value / 100_000_000:g}亿元"
    if field == "debt_ratio":
        return f"{value:g}%"
    return f"{value:g}"


def _applied_rules(spec: FinancialScreenSpec) -> list[str]:
    rules = [
        (
            f"股票范围=active A股，市场={','.join(spec.universe.markets)}，"
            f"{'包含' if spec.universe.include_st else '排除'}ST，"
            f"上市交易历史>={spec.universe.min_listing_trading_days}日"
        ),
        *[
            f"{_FINANCIAL_LABELS[item.field]}{_OPERATOR_LABELS[item.operator]}"
            f"{_format_filter_value(item.field, item.value)}"
            for item in spec.financial_filters
        ],
        f"按{_FIELD_META[spec.sort.field][0]}{('降序' if spec.sort.order == 'desc' else '升序')}",
    ]
    if spec.universe.codes is not None:
        rules.insert(1, f"筛选范围=股票分组（{len(spec.universe.codes)}只）")
    return rules


def _load_required_financials(
    eligible_codes: list[str],
    required_fields: set[str],
) -> tuple[dict[str, dict[str, Any]], str | None, dict[str, Any], list[str], list[str]]:
    """Load a coherent report-period snapshot and fill only a bounded tail."""
    if not required_fields:
        return {}, None, {}, [], []

    warnings: list[str] = []
    fallback_sources: set[str] = set()
    cached_count = 0
    fallback_count = 0
    primary_available = True
    try:
        financials, report_period = _build_ttm_financials()
        _persist_financials(financials, report_period)
    except Exception as exc:
        primary_available = False
        warnings.append(
            "财务主源本轮不可用；已切换到本日成功刷新并落库的财务快照。"
            f"主源错误：{type(exc).__name__}"
        )
        try:
            financials, report_period = _load_fresh_cached_financials(set(eligible_codes))
        except Exception as cache_exc:
            return (
                {},
                None,
                {"financial_required_fields": sorted(required_fields)},
                [f"财务缓存读取失败: {type(cache_exc).__name__}"],
                ["financial_cache"],
            )
        cached_count = sum(required_fields.issubset(values) for values in financials.values())
        primary_error = f"{type(exc).__name__}: {exc}"

    missing = [
        code for code in eligible_codes if not required_fields.issubset(financials.get(code, {}))
    ]
    if len(missing) > MAX_SECONDARY_FINANCIAL_FALLBACKS:
        stage = "financial_cache_coverage" if not primary_available else "financial_coverage"
        return (
            financials,
            report_period,
            {
                "financial_required_fields": sorted(required_fields),
                "financial_covered": sum(
                    required_fields.issubset(financials.get(code, {})) for code in eligible_codes
                ),
                "financial_cache_count": cached_count,
                "financial_fallback_limit": MAX_SECONDARY_FINANCIAL_FALLBACKS,
            },
            [f"{code}:缺少必需财务字段" for code in missing[:20]],
            [stage],
        )

    fallback_errors: list[str] = []
    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(FINANCIAL_FETCH_WORKERS, len(missing))) as pool:
            future_codes = {
                pool.submit(_fetch_secondary_ttm_financial, code, required_fields): code for code in missing
            }
            for future in concurrent.futures.as_completed(future_codes):
                code = future_codes[future]
                try:
                    values = future.result()
                except Exception as exc:
                    fallback_errors.append(f"{code}:{type(exc).__name__}")
                    continue
                if values and required_fields.issubset(values):
                    financials[code] = values
                    fallback_count += 1
                    if values.get("financial_source"):
                        fallback_sources.add(str(values["financial_source"]))
                else:
                    missing_fields = sorted(required_fields.difference(values or {}))
                    fallback_errors.append(f"{code}:缺少{','.join(missing_fields)}")

    if fallback_errors:
        return (
            financials,
            report_period,
            {
                "financial_required_fields": sorted(required_fields),
                "financial_covered": sum(
                    required_fields.issubset(financials.get(code, {})) for code in eligible_codes
                ),
                "financial_cache_count": cached_count,
                "financial_fallback_count": fallback_count,
            },
            fallback_errors[:20],
            ["financial_coverage"],
        )

    coverage = {
        "financial_required_fields": sorted(required_fields),
        "financial_covered": sum(
            required_fields.issubset(financials.get(code, {})) for code in eligible_codes
        ),
        "financial_cache_count": cached_count,
        "financial_fallback_count": fallback_count,
        "financial_fallback_sources": sorted(fallback_sources),
    }
    return financials, report_period, coverage, [], warnings


def run_financial_screen(
    *,
    screen_spec: dict[str, Any] | None = None,
    include_all_items: bool = False,
) -> dict[str, Any]:
    """Execute a financial-only screen without fetching or applying ATR data."""
    try:
        spec = FinancialScreenSpec.model_validate(screen_spec)
    except ValidationError as exc:
        return _failure(
            "财务筛选条件校验失败；未执行任何股票筛选: "
            + "; ".join(error["msg"] for error in exc.errors()[:8]),
            stage="spec_validation",
        )

    try:
        maintenance = ensure_stock_universe(trigger="indicator_financial_screen")
    except Exception as exc:
        return _failure(
            f"股票基础库自动维护失败: {type(exc).__name__}: {exc}",
            stage="universe_maintenance",
            spec=spec,
        )

    db = DatabaseManager.get_instance()
    with db.session_scope() as session:
        rows = (
            session.execute(
                text("SELECT code, name, ipo_date FROM stock_meta WHERE status='active' ORDER BY code")
            )
            .mappings()
            .all()
        )
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

    eligible = [
        code
        for code, row in active.items()
        if _safe_date(row.get("ipo_date")) is None
        or (date.today() - _safe_date(row.get("ipo_date"))).days + 1 >= spec.universe.min_listing_trading_days
    ]
    required_fields = spec.required_financial_fields()
    financials, report_period, financial_coverage, failed_symbols, failure_stages = _load_required_financials(
        eligible,
        required_fields,
    )
    if failure_stages:
        return _failure(
            "本轮财务快照覆盖不足，已停止筛选；不会把缺失财务数据当作不满足条件。",
            stage=failure_stages[0],
            spec=spec,
            data_time=report_period,
            coverage={
                "universe": len(active),
                "eligible": len(eligible),
                **financial_coverage,
            },
            failed_symbols=failed_symbols,
        )

    items = [
        {"code": code, "name": str(active[code]["name"]), **financials[code]}
        for code in eligible
        if _matches_financial_filters(financials.get(code, {}), spec)
    ]
    items.sort(key=lambda item: str(item["code"]))
    if spec.sort.field != "code":
        items.sort(key=lambda item: item.get(spec.sort.field), reverse=spec.sort.order == "desc")
    elif spec.sort.order == "desc":
        items.reverse()

    fingerprint = _spec_fingerprint(spec)
    columns = _column_defs(spec)
    download_url = file_id = None
    if len(items) > spec.preview_limit:
        file_id, download_url = _write_export(items, columns, fingerprint)
    source_values = {
        str(item.get("financial_source"))
        for item in financials.values()
        if item.get("financial_source")
    }
    coverage = {
        "universe": len(active),
        "eligible": len(eligible),
        "financial_eligible": len(items),
        "excluded_by_financial": len(eligible) - len(items),
        "financial_required_fields": sorted(required_fields),
        "financial_covered": len(eligible),
        "complete": True,
        **financial_coverage,
    }
    result_items = items if include_all_items else items[: spec.preview_limit]
    return {
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [],
        "failure_stage": None,
        "screen_spec": spec.model_dump(mode="json"),
        "spec_fingerprint": fingerprint,
        "applied_rules": _applied_rules(spec),
        "formula": {"financial_basis": "按最新可用报告期构造滚动十二个月TTM；缺少必需字段则整轮停止"},
        "columns": columns,
        "items": result_items,
        "matched_codes": [str(item["code"]) for item in items],
        "total": len(items),
        "download_url": download_url,
        "file_id": file_id,
        "data_time": report_period,
        "data_times": {"financial_report_period": report_period},
        "is_stale": False,
        "freshness_unknown": report_period is None,
        "financial_report_period": report_period,
        "maintenance": {"stock_universe": maintenance},
        "coverage": coverage,
        "source": " + ".join(sorted(source_values)),
    }


__all__ = ["run_financial_screen"]
