"""``get_consensus_estimates`` — normalized sell-side consensus evidence."""

from __future__ import annotations
import math
import re
from datetime import datetime, timedelta
from typing import Any
import akshare as ak
from market_data_service.providers.common import (
    bare_local_symbol,
    cached_call,
    frame_records,
)

DESCRIPTION = "按明确指标读取同花顺一致盈利预测、机构预测明细或财务预测明细；每个读取只返回对应来源记录，不在 provider 内混合其他预测表。"
_INDICATORS = {"eps": "预测年报每股收益", "net_profit": "预测年报净利润"}
_DETAIL_INDICATORS = {
    "institutions": "业绩预测详表-机构",
    "financial_metrics": "业绩预测详表-详细指标预测",
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    text = str(value).strip().replace(",", "")
    text = re.sub("(?:亿元|亿|万元|万|元|%)$", "", text)
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
    return (frame_records(frame), cached)


def _detail(code: str, kind: str) -> tuple[list[dict[str, Any]], bool]:
    indicator = _DETAIL_INDICATORS[kind]
    frame, cached = cached_call(
        f"consensus:v2:{code}:{kind}",
        lambda: ak.stock_profit_forecast_ths(symbol=code, indicator=indicator),
        ttl_seconds=6 * 3600,
        attempts=2,
    )
    return (frame_records(frame), cached)


def _normalize_summary(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    unit = "元/股" if metric == "eps" else "亿元"
    items = []
    for row in rows:
        year_value = _number(row.get("年度"))
        if year_value is None:
            continue
        items.append(
            {
                "year": int(year_value),
                "coverage_count": int(_number(row.get("预测机构数")) or 0),
                "low": _number(row.get("最小值")),
                "mean": _number(row.get("均值")),
                "high": _number(row.get("最大值")),
                "industry_mean": _number(row.get("行业平均数")),
                "unit": unit,
            }
        )
    return sorted(items, key=lambda item: item["year"])


def _normalize_institutions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        forecasts: dict[int, dict[str, Any]] = {}
        for key, value in row.items():
            match = re.search("每股收益(\\d{4})预测", str(key))
            if match:
                forecasts.setdefault(int(match.group(1)), {})["eps"] = _number(value)
                continue
            match = re.search("净利润(\\d{4})预测", str(key))
            if match:
                forecasts.setdefault(int(match.group(1)), {})["net_profit_yi"] = (
                    _number(value)
                )
        report_date = _date_text(row.get("报告日期"))
        institution = str(row.get("机构名称") or "").strip()
        if not institution and (not forecasts):
            continue
        items.append(
            {
                "institution": institution or None,
                "analysts": str(row.get("研究员") or "").strip() or None,
                "report_date": report_date,
                "forecasts": [
                    {
                        "year": year,
                        **forecasts[year],
                        "eps_unit": "元/股",
                        "net_profit_unit": "亿元",
                    }
                    for year in sorted(forecasts)
                ],
            }
        )
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


def _normalize_financial_metrics(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_year: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        mapping = _FINANCIAL_METRIC_MAP.get(str(row.get("预测指标") or "").strip())
        if not mapping:
            continue
        field, unit = mapping
        for key, value in row.items():
            match = re.fullmatch("(\\d{4})-实际值", str(key))
            status = "actual"
            if not match:
                match = re.fullmatch("预测(\\d{4})-平均", str(key))
                status = "forecast"
            if not match:
                continue
            year = int(match.group(1))
            item = by_year.setdefault(
                (year, status), {"year": year, "status": status, "units": {}}
            )
            item[field] = _number(value)
            item["units"][field] = unit
    actuals = [
        value for (year, status), value in sorted(by_year.items()) if status == "actual"
    ]
    forecasts = [
        value
        for (year, status), value in sorted(by_year.items())
        if status == "forecast"
    ]
    return (actuals, forecasts)


def _validated_code(symbol: str) -> str:
    code = bare_local_symbol(symbol)
    if not re.fullmatch("\\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    return code


def read_consensus_metric_ths(symbol: str, metric: str) -> dict[str, Any]:
    """Read one THS consensus metric query (EPS or net profit)."""
    code = _validated_code(symbol)
    if metric not in _INDICATORS:
        raise ValueError("metric 必须是 eps 或 net_profit")
    rows, cached = _forecast(code, metric)
    estimates = _normalize_summary(rows, metric)
    return {
        "symbol": code,
        "metric": metric,
        "estimates": estimates,
        "coverage_available": bool(estimates),
        "coverage_status": "covered" if estimates else "no_sell_side_coverage",
        "unit": "元/股" if metric == "eps" else "亿元",
        "source": "同花顺盈利预测/AKShare",
        "source_url": f"https://basic.10jqka.com.cn/new/{code}/worth.html",
        "source_scope": f"consensus_{metric}",
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [] if estimates else [f"{metric} 无机构一致预测覆盖"],
        "data_time": None,
        "is_stale": None,
        "freshness_unknown": True,
        "_cached": cached,
        "_fetched_at": datetime.now().astimezone().isoformat(),
    }


def read_consensus_institution_forecasts_ths(symbol: str) -> dict[str, Any]:
    """Read one THS institution-detail consensus query."""
    code = _validated_code(symbol)
    rows, cached = _detail(code, "institutions")
    institutions = _normalize_institutions(rows)
    report_dates = [
        item["report_date"] for item in institutions if item.get("report_date")
    ]
    latest_report_date = max(report_dates) if report_dates else None
    return {
        "symbol": code,
        "institutions": institutions[:20],
        "institution_item_count": len(institutions),
        "latest_institution_report_date": latest_report_date,
        "source": "同花顺机构预测明细/AKShare",
        "source_url": f"https://basic.10jqka.com.cn/new/{code}/worth.html",
        "source_scope": "consensus_institution_forecasts",
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [] if institutions else ["该数据源未返回机构预测明细"],
        "data_time": latest_report_date,
        "is_stale": datetime.fromisoformat(latest_report_date).date()
        < datetime.now().date() - timedelta(days=180)
        if latest_report_date
        else None,
        "freshness_unknown": latest_report_date is None,
        "_cached": cached,
        "_fetched_at": datetime.now().astimezone().isoformat(),
    }


def read_consensus_financial_estimates_ths(symbol: str) -> dict[str, Any]:
    """Read one THS financial-estimate detail query."""
    code = _validated_code(symbol)
    rows, cached = _detail(code, "financial_metrics")
    actuals, forecasts = _normalize_financial_metrics(rows)
    dated = [item.get("year") for item in [*actuals, *forecasts] if item.get("year")]
    latest_year = max(dated) if dated else None
    return {
        "symbol": code,
        "actuals": actuals,
        "financial_forecasts": forecasts,
        "source": "同花顺财务预测明细/AKShare",
        "source_url": f"https://basic.10jqka.com.cn/new/{code}/worth.html",
        "source_scope": "consensus_financial_estimates",
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [] if actuals or forecasts else ["该数据源未返回财务预测明细"],
        "data_time": str(latest_year) if latest_year else None,
        "is_stale": None,
        "freshness_unknown": latest_year is None,
        "_cached": cached,
        "_fetched_at": datetime.now().astimezone().isoformat(),
    }
