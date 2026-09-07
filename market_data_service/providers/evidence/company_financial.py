"""Forward financial disclosures and goodwill evidence."""

from __future__ import annotations

from typing import Any, Callable

from .common import (
    disclosure_period_label,
    fetch_frame,
    recent_report_periods,
    section_envelope,
)


def _period_datasets(
    api_name: str,
    factory: Callable[[str], Any],
    periods: list[str],
    symbol: str,
    *,
    ttl_seconds: int,
) -> dict[str, Any]:
    combined: list[dict[str, Any]] = []
    period_errors: list[str] = []
    cached = True
    source_rows = 0
    any_success = False
    for period in periods:
        result = fetch_frame(
            api_name,
            f"report:{api_name}:{period}",
            lambda period=period: factory(period),
            ttl_seconds=ttl_seconds,
            symbol=symbol,
        )
        cached = cached and bool(result.get("cached"))
        if result.get("success"):
            any_success = True
            source_rows += int(result.get("source_row_count") or 0)
            combined.extend(
                {"report_period": period, **item} for item in result.get("items") or []
            )
        elif result.get("error"):
            period_errors.append(f"{period}: {result['error']}")
    return {
        "success": any_success,
        "partial": any_success and bool(period_errors),
        "source_api": f"AKShare.{api_name}",
        "items": combined,
        "item_count": len(combined),
        "source_row_count": source_rows if any_success else None,
        "cached": cached if any_success else False,
        "error": "；".join(period_errors)[:1200] if period_errors else None,
        "queried_periods": periods,
    }


def get_financial_event_evidence(
    symbol: str, *, report_period_count: int = 4
) -> dict[str, Any]:
    import akshare as ak

    code = str(symbol)
    periods = recent_report_periods(count=max(1, min(int(report_period_count), 8)))
    schedule_periods = recent_report_periods(count=4, include_next=True)
    datasets = {
        "performance_forecasts": _period_datasets(
            "stock_yjyg_em",
            lambda period: ak.stock_yjyg_em(date=period),
            periods,
            code,
            ttl_seconds=12 * 3600,
        ),
        "performance_reports": _period_datasets(
            "stock_yjbb_em",
            lambda period: ak.stock_yjbb_em(date=period),
            periods,
            code,
            ttl_seconds=12 * 3600,
        ),
        "performance_flashes": _period_datasets(
            "stock_yjkb_em",
            lambda period: ak.stock_yjkb_em(date=period),
            periods,
            code,
            ttl_seconds=12 * 3600,
        ),
        "goodwill_impairment_expectations": _period_datasets(
            "stock_sy_yq_em",
            lambda period: ak.stock_sy_yq_em(date=period),
            periods,
            code,
            ttl_seconds=24 * 3600,
        ),
        "goodwill_impairments": _period_datasets(
            "stock_sy_jz_em",
            lambda period: ak.stock_sy_jz_em(date=period),
            periods,
            code,
            ttl_seconds=24 * 3600,
        ),
        "report_disclosure_schedule": _period_datasets(
            "stock_report_disclosure",
            lambda period: ak.stock_report_disclosure(
                market="沪深京", period=disclosure_period_label(period)
            ),
            schedule_periods,
            code,
            ttl_seconds=12 * 3600,
        ),
    }
    return section_envelope(
        "financial_events",
        code,
        datasets,
        units={"amount": "元（以源字段口径为准）", "ratio": "%"},
        notes=[
            "业绩预告、快报、正式业绩报表是不同披露阶段，后续披露不应与早期预告混为一项。"
        ],
    )


__all__ = ["get_financial_event_evidence"]
