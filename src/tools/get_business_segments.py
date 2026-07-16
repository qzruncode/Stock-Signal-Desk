# -*- coding: utf-8 -*-
"""``get_business_segments`` — normalized reported business composition."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

import akshare as ak

from src.tools._akshare import bare_symbol, cached_call, exchange_prefix, frame_records
from src.tools.base import ToolSpec, object_schema

DESCRIPTION = (
    "获取公司披露的主营业务构成，可按产品、行业或地区查看收入、成本、毛利及各自占比，"
    "并返回收入集中度和主要利润来源。金额为人民币元，占比与毛利率为百分比；"
    "中期数据是年初至报告期累计口径，不作为单季度数据。"
)

_CATEGORY_BY_SOURCE = {
    "按产品分类": "product",
    "按行业分类": "industry",
    "按地区分类": "region",
    "1": "industry",
    "2": "product",
    "3": "region",
}


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


def _percent(value: Any) -> float | None:
    number = _number(value)
    return round(number * 100, 6) if number is not None else None


def _flow_basis(report_date: str) -> str:
    return "full_year" if report_date.endswith("-12-31") else "year_to_date"


def _normalize_row(row: dict[str, Any], code: str) -> dict[str, Any] | None:
    report_date = _date_text(row.get("报告日期") or row.get("REPORT_DATE"))
    category = _CATEGORY_BY_SOURCE.get(str(row.get("分类类型") or row.get("MAINOP_TYPE") or ""))
    segment_name = str(row.get("主营构成") or row.get("ITEM_NAME") or "").strip()
    if not report_date or not category or not segment_name:
        return None
    return {
        "symbol": code,
        "report_date": report_date,
        "flow_basis": _flow_basis(report_date),
        "category": category,
        "segment_name": segment_name,
        "revenue": _number(row.get("主营收入") if "主营收入" in row else row.get("MAIN_BUSINESS_INCOME")),
        "revenue_share_pct": _percent(row.get("收入比例") if "收入比例" in row else row.get("MBI_RATIO")),
        "cost": _number(row.get("主营成本") if "主营成本" in row else row.get("MAIN_BUSINESS_COST")),
        "cost_share_pct": _percent(row.get("成本比例") if "成本比例" in row else row.get("MBC_RATIO")),
        "gross_profit": _number(row.get("主营利润") if "主营利润" in row else row.get("MAIN_BUSINESS_RPOFIT")),
        "gross_profit_share_pct": _percent(row.get("利润比例") if "利润比例" in row else row.get("MBR_RATIO")),
        "gross_margin_pct": _percent(row.get("毛利率") if "毛利率" in row else row.get("GROSS_RPOFIT_RATIO")),
    }


def _expected_min_report_date(today: date) -> date:
    # Segment disclosures are consistently available at least in annual and
    # half-year reports.  Some issuers additionally disclose Q1/Q3, but that is
    # not a universal freshness requirement.
    if today.month <= 4:
        return date(today.year - 1, 6, 30)
    if today.month <= 8:
        return date(today.year - 1, 12, 31)
    return date(today.year, 6, 30)


def _summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault((item["report_date"], item["category"]), []).append(item)
    summaries = []
    for (report_date, category), rows in sorted(groups.items(), reverse=True):
        ranked = sorted(rows, key=lambda row: row.get("revenue") or float("-inf"), reverse=True)
        positive_profit = sorted(rows, key=lambda row: row.get("gross_profit") or float("-inf"), reverse=True)
        shares = [row.get("revenue_share_pct") for row in ranked]
        summaries.append({
            "report_date": report_date,
            "flow_basis": _flow_basis(report_date),
            "category": category,
            "segment_count": len(rows),
            "largest_revenue_segment": ranked[0]["segment_name"] if ranked else None,
            "largest_revenue_share_pct": shares[0] if shares else None,
            "top3_revenue_share_pct": round(sum(value for value in shares[:3] if value is not None), 6),
            "largest_gross_profit_segment": positive_profit[0]["segment_name"] if positive_profit else None,
        })
    return summaries


def _empty(code: str, category: str, message: str, *, fetched_at: str) -> dict[str, Any]:
    return {
        "symbol": code,
        "category": category,
        "requested_periods": 0,
        "periods": [],
        "available_categories": [],
        "items": [],
        "item_count": 0,
        "summaries": [],
        "amount_unit": "元",
        "ratio_unit": "%",
        "currency": "CNY",
        "success": False,
        "errors": [message],
        "source": "东方财富主营构成（AKShare）",
        "source_url": None,
        "data_time": None,
        "is_stale": True,
        "fallback_used": False,
        "_cached": False,
        "_fetched_at": fetched_at,
    }


def get_business_segments(symbol: str, category: str = "all", periods: int = 2) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    if category not in {"all", "product", "industry", "region"}:
        raise ValueError("category 必须是 all、product、industry 或 region")
    periods = max(1, min(int(periods), 8))
    prefixed = exchange_prefix(code, upper=True)
    now = datetime.now().astimezone()
    try:
        frame, cached = cached_call(
            f"business_segments:v2:{code}",
            lambda: ak.stock_zygc_em(symbol=prefixed),
            ttl_seconds=6 * 3600,
            attempts=2,
        )
    except Exception as exc:
        return _empty(code, category, f"主营构成数据获取失败: {exc}", fetched_at=now.isoformat())

    normalized = [
        item for item in (_normalize_row(row, code) for row in frame_records(frame))
        if item is not None
    ]
    if category != "all":
        normalized = [item for item in normalized if item["category"] == category]
    available_periods = sorted({item["report_date"] for item in normalized}, reverse=True)
    selected_periods = available_periods[:periods]
    selected = [item for item in normalized if item["report_date"] in selected_periods]
    selected.sort(key=lambda item: (
        item["report_date"], item["category"], item.get("revenue") or float("-inf")
    ), reverse=True)

    latest_report = selected_periods[0] if selected_periods else None
    source_url = (
        "https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/"
        f"Index?type=web&code={prefixed}"
    )
    errors = [] if selected else [f"没有找到 category={category} 的主营构成披露"]
    return {
        "symbol": code,
        "category": category,
        "requested_periods": periods,
        "periods": selected_periods,
        "available_categories": sorted({item["category"] for item in selected}),
        "items": selected,
        "item_count": len(selected),
        "summaries": _summary(selected),
        "amount_unit": "元",
        "ratio_unit": "%",
        "currency": "CNY",
        "flow_basis": "full_year for 12-31; otherwise year_to_date",
        "source": "东方财富主营构成（AKShare）",
        "source_url": source_url,
        "success": bool(selected),
        "errors": errors,
        "data_time": latest_report,
        "is_stale": not latest_report or datetime.fromisoformat(latest_report).date() < _expected_min_report_date(now.date()),
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


TOOL = ToolSpec(
    name="get_business_segments",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码或股票名称，如 600519 或 贵州茅台"},
            "category": {
                "type": "string",
                "enum": ["all", "product", "industry", "region"],
                "default": "all",
                "description": "主营分类：all 全部、product 产品、industry 行业、region 地区",
            },
            "periods": {"type": "integer", "minimum": 1, "maximum": 8, "default": 2, "description": "返回最近报告期数量"},
        },
        ["symbol"],
    ),
    executor=get_business_segments,
    category="financials",
)
