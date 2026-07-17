# -*- coding: utf-8 -*-
"""``get_consensus_estimates`` — normalized sell-side consensus evidence."""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

import akshare as ak

from src.tools._akshare import bare_symbol, cached_call, frame_records
from src.tools.base import ToolSpec, object_schema

DESCRIPTION = (
    "获取券商一致盈利预测，返回未来年度 EPS 与净利润的覆盖机构数、最低/均值/最高值、"
    "近期机构明细，以及收入、利润增速和 ROE 等预测；区分一致预期与单篇研报，"
    "并标明净利润金额单位为亿元。"
)

_INDICATORS = {
    "eps": "预测年报每股收益",
    "net_profit": "预测年报净利润",
}
_DETAIL_INDICATORS = {
    "institutions": "业绩预测详表-机构",
    "financial_metrics": "业绩预测详表-详细指标预测",
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    text = str(value).strip().replace(",", "")
    text = re.sub(r"(?:亿元|亿|万元|万|元|%)$", "", text)
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_text(value: Any) -> str | None:
    text = str(value or "")[:10]
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _forecast(code: str, metric: str) -> tuple[list[dict[str, Any]], bool]:
    indicator = _INDICATORS[metric]
    frame, cached = cached_call(
        f"consensus:v2:{code}:{metric}",
        lambda: ak.stock_profit_forecast_ths(symbol=code, indicator=indicator),
        ttl_seconds=6 * 3600,
        attempts=2,
    )
    return frame_records(frame), cached


def _detail(code: str, kind: str) -> tuple[list[dict[str, Any]], bool]:
    indicator = _DETAIL_INDICATORS[kind]
    frame, cached = cached_call(
        f"consensus:v2:{code}:{kind}",
        lambda: ak.stock_profit_forecast_ths(symbol=code, indicator=indicator),
        ttl_seconds=6 * 3600,
        attempts=2,
    )
    return frame_records(frame), cached


def _normalize_summary(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    unit = "元/股" if metric == "eps" else "亿元"
    items = []
    for row in rows:
        year_value = _number(row.get("年度"))
        if year_value is None:
            continue
        items.append({
            "year": int(year_value),
            "coverage_count": int(_number(row.get("预测机构数")) or 0),
            "low": _number(row.get("最小值")),
            "mean": _number(row.get("均值")),
            "high": _number(row.get("最大值")),
            "industry_mean": _number(row.get("行业平均数")),
            "unit": unit,
        })
    return sorted(items, key=lambda item: item["year"])


def _merge_estimates(metrics: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    by_year: dict[int, dict[str, Any]] = {}
    for metric, rows in metrics.items():
        for row in rows:
            item = by_year.setdefault(row["year"], {"year": row["year"], "coverage_count": 0})
            item[metric] = {key: value for key, value in row.items() if key not in {"year", "coverage_count"}}
            item["coverage_count"] = max(item["coverage_count"], row["coverage_count"])
    return [by_year[year] for year in sorted(by_year)]


def _normalize_institutions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        forecasts: dict[int, dict[str, Any]] = {}
        for key, value in row.items():
            match = re.search(r"每股收益(\d{4})预测", str(key))
            if match:
                forecasts.setdefault(int(match.group(1)), {})["eps"] = _number(value)
                continue
            match = re.search(r"净利润(\d{4})预测", str(key))
            if match:
                forecasts.setdefault(int(match.group(1)), {})["net_profit_yi"] = _number(value)
        report_date = _date_text(row.get("报告日期"))
        institution = str(row.get("机构名称") or "").strip()
        if not institution and not forecasts:
            continue
        items.append({
            "institution": institution or None,
            "analysts": str(row.get("研究员") or "").strip() or None,
            "report_date": report_date,
            "forecasts": [
                {"year": year, **forecasts[year], "eps_unit": "元/股", "net_profit_unit": "亿元"}
                for year in sorted(forecasts)
            ],
        })
    return sorted(items, key=lambda item: item.get("report_date") or "", reverse=True)


_FINANCIAL_METRIC_MAP = {
    "营业收入(元)": ("revenue_yi", "亿元"),
    "营业收入增长率": ("revenue_growth_pct", "%"),
    "利润总额(元)": ("total_profit_yi", "亿元"),
    "净利润(元)": ("net_profit_yi", "亿元"),
    "净利润增长率": ("net_profit_growth_pct", "%"),
    "每股现金流(元)": ("cashflow_per_share", "元/股"),
    "每股净资产(元)": ("book_value_per_share", "元/股"),
    "净资产收益率": ("roe_pct", "%"),
}


def _normalize_financial_metrics(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_year: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        mapping = _FINANCIAL_METRIC_MAP.get(str(row.get("预测指标") or "").strip())
        if not mapping:
            continue
        field, unit = mapping
        for key, value in row.items():
            match = re.fullmatch(r"(\d{4})-实际值", str(key))
            status = "actual"
            if not match:
                match = re.fullmatch(r"预测(\d{4})-平均", str(key))
                status = "forecast"
            if not match:
                continue
            year = int(match.group(1))
            item = by_year.setdefault((year, status), {"year": year, "status": status, "units": {}})
            item[field] = _number(value)
            item["units"][field] = unit
    actuals = [value for (year, status), value in sorted(by_year.items()) if status == "actual"]
    forecasts = [value for (year, status), value in sorted(by_year.items()) if status == "forecast"]
    return actuals, forecasts


def get_consensus_estimates(symbol: str, metric: str = "all") -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    if metric not in {"all", "eps", "net_profit"}:
        raise ValueError("metric 必须是 all、eps 或 net_profit")
    requested = list(_INDICATORS) if metric == "all" else [metric]
    now = datetime.now().astimezone()
    raw: dict[str, list[dict[str, Any]]] = {}
    cache_detail: dict[str, bool] = {}
    errors: list[str] = []
    warnings: list[str] = []
    calls = {name: (lambda name=name: _forecast(code, name)) for name in requested}
    if metric == "all":
        calls.update({name: (lambda name=name: _detail(code, name)) for name in _DETAIL_INDICATORS})
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = {name: pool.submit(fn) for name, fn in calls.items()}
        for name, future in futures.items():
            try:
                rows, cached = future.result()
                raw[name] = rows
                cache_detail[name] = cached
                if name in requested and not rows:
                    errors.append(f"{name} 暂无机构一致预测")
                elif name not in requested and not rows:
                    warnings.append(f"{name} 明细不可用")
            except Exception as exc:
                raw[name] = []
                cache_detail[name] = False
                if name in requested:
                    errors.append(f"{name}: {exc}")
                else:
                    warnings.append(f"{name}: {exc}")

    metrics = {name: _normalize_summary(raw.get(name, []), name) for name in requested}
    estimates = _merge_estimates(metrics)
    institutions = _normalize_institutions(raw.get("institutions", []))
    actuals, financial_forecasts = _normalize_financial_metrics(raw.get("financial_metrics", []))
    report_dates = [item["report_date"] for item in institutions if item.get("report_date")]
    latest_report_date = max(report_dates) if report_dates else None
    success = bool(estimates or institutions or financial_forecasts)
    freshness_unknown = latest_report_date is None
    stale = (
        datetime.fromisoformat(latest_report_date).date() < (now.date() - timedelta(days=180))
        if latest_report_date else None
    )
    return {
        "symbol": code,
        "metric": metric,
        "estimates": estimates,
        "metrics": metrics,
        "institutions": institutions[:20],
        "institution_item_count": len(institutions),
        "latest_institution_report_date": latest_report_date,
        "actuals": actuals,
        "financial_forecasts": financial_forecasts,
        "coverage_available": bool(estimates),
        "coverage_count_latest": estimates[0].get("coverage_count") if estimates else 0,
        "eps_unit": "元/股",
        "net_profit_unit": "亿元",
        "amount_unit_note": "同花顺净利润一致预期原始表以亿元展示，不转换为元。",
        "forecast_warning": "一致预期是机构预测汇总，不是公司业绩承诺；应结合预测发布日期和实际财报验证。",
        "source": "同花顺盈利预测/AKShare",
        "source_url": f"https://basic.10jqka.com.cn/new/{code}/worth.html",
        "success": success,
        "partial": success and bool(errors),
        "errors": errors,
        "warnings": warnings,
        "data_time": latest_report_date,
        "freshness_unknown": freshness_unknown,
        "is_stale": stale,
        "fallback_used": False,
        "cache_detail": cache_detail,
        "_cached": bool(cache_detail) and all(cache_detail.values()),
        "_fetched_at": now.isoformat(),
    }


TOOL = ToolSpec(
    name="get_consensus_estimates",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码或股票名称"},
            "metric": {
                "type": "string",
                "enum": ["all", "eps", "net_profit"],
                "default": "all",
                "description": "预测指标：all、eps 每股收益、net_profit 净利润",
            },
        },
        ["symbol"],
    ),
    executor=get_consensus_estimates,
    category="financials",
)
