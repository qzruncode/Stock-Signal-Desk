# -*- coding: utf-8 -*-
"""``get_peer_comparison`` — normalized industry-relative company evidence."""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, Callable

import httpx

from src.tools._akshare import bare_local_symbol, bare_symbol, cached_call, exchange_prefix
from src.tools.base import ToolSpec, object_schema

_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
_REPORTS = {
    "growth": "RPT_PCF10_INDUSTRY_GROWTH",
    "valuation": "RPT_PCF10_INDUSTRY_CVALUE",
    "profitability": "RPT_PCF10_INDUSTRY_DBFX",
    "scale": "RPT_PCF10_INDUSTRY_MARKET",
}
_LABELS = {
    "growth": "成长性",
    "valuation": "估值",
    "profitability": "杜邦盈利能力",
    "scale": "规模与财务体量",
}

DESCRIPTION = (
    "获取目标公司与东方财富行业同行的标准化横向比较：成长性、估值、杜邦盈利能力和规模。"
    "每个维度明确给出目标、行业中值/均值、排名样本量及头部同行；"
    "估值默认优先参考中值，避免亏损股和极端值扭曲均值。"
)


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _date_text(value: Any) -> str | None:
    text = str(value or "")[:10]
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _request_rows(code: str, dimension: str) -> list[dict[str, Any]]:
    prefixed = exchange_prefix(code, upper=True)
    params = {
        "reportName": _REPORTS[dimension],
        "columns": "ALL",
        "quoteColumns": "",
        "filter": f'(SECUCODE="{code}.{prefixed[:2]}")',
        "pageNumber": "1" if dimension == "scale" else "",
        "pageSize": "500" if dimension == "scale" else "",
        "sortTypes": "-1" if dimension == "scale" else "1",
        "sortColumns": "TOTAL_CAP" if dimension == "scale" else "PAIMING",
        "source": "HSF10",
        "client": "PC",
    }
    response = httpx.get(
        _URL,
        params=params,
        headers={"User-Agent": _UA, "Referer": "https://emweb.securities.eastmoney.com/"},
        timeout=httpx.Timeout(12.0, connect=3.0),
    )
    response.raise_for_status()
    rows = (response.json().get("result") or {}).get("data") or []
    if not rows:
        raise RuntimeError(f"东方财富没有返回{_LABELS[dimension]}同行数据")
    return [row for row in rows if isinstance(row, dict)]


def _forecast_series(row: dict[str, Any], prefix: str, base_year: int | None) -> list[dict[str, Any]]:
    result = []
    for offset, suffix in enumerate(("1E", "2E", "3E")):
        value = _number(row.get(f"{prefix}_{suffix}"))
        if value is not None:
            result.append({"year": base_year + offset if base_year else None, "value_pct": value})
    return result


def _growth_row(row: dict[str, Any]) -> dict[str, Any]:
    report_date = _date_text(row.get("REPORT_DATE"))
    base_year = datetime.fromisoformat(report_date).year if report_date else None
    return {
        "symbol": str(row.get("CORRE_SECURITY_CODE") or ""),
        "name": str(row.get("CORRE_SECURITY_NAME") or ""),
        "eps_growth_3y_cagr_pct": _number(row.get("MGSY_3Y")),
        "eps_growth_report_year_pct": _number(row.get("MGSYTB")),
        "eps_growth_ttm_pct": _number(row.get("MGSYTTM")),
        "eps_growth_forecast": _forecast_series(row, "MGSY", base_year),
        "revenue_growth_3y_cagr_pct": _number(row.get("YYSR_3Y")),
        "revenue_growth_report_year_pct": _number(row.get("YYSRTB")),
        "revenue_growth_ttm_pct": _number(row.get("YYSRTTM")),
        "revenue_growth_forecast": _forecast_series(row, "YYSR", base_year),
        "net_profit_growth_3y_cagr_pct": _number(row.get("JLR_3Y")),
        "net_profit_growth_report_year_pct": _number(row.get("JLRTB")),
        "net_profit_growth_ttm_pct": _number(row.get("JLRTTM")),
        "net_profit_growth_forecast": _forecast_series(row, "JLR", base_year),
        "rank": _integer(row.get("PAIMING")),
    }


def _valuation_row(row: dict[str, Any]) -> dict[str, Any]:
    report_date = _date_text(row.get("REPORT_DATE"))
    base_year = datetime.fromisoformat(report_date).year if report_date else None
    return {
        "symbol": str(row.get("CORRE_SECURITY_CODE") or ""),
        "name": str(row.get("CORRE_SECURITY_NAME") or ""),
        "peg_forward": _number(row.get("PEG")),
        "pe_ttm": _number(row.get("PE_TTM")),
        "forward_pe": [
            {"year": base_year + offset if base_year else None, "value": value}
            for offset, field in enumerate(("PE_1Y", "PE_2Y", "PE_3Y"))
            if (value := _number(row.get(field))) is not None
        ],
        "ps_ttm": _number(row.get("PS_TTM")),
        "forward_ps": [
            {"year": base_year + offset if base_year else None, "value": value}
            for offset, field in enumerate(("PS_1Y", "PS_2Y", "PS_3Y"))
            if (value := _number(row.get(field))) is not None
        ],
        "pb_mrq": _number(row.get("PB_MRQ")),
        "pcf_ttm": _number(row.get("PCE_TTM")),
        "ev_ebitda_report_year": _number(row.get("QYBS")),
        "rank": _integer(row.get("PAIMING")),
    }


def _profitability_row(row: dict[str, Any]) -> dict[str, Any]:
    report_date = _date_text(row.get("REPORT_DATE"))
    base_year = datetime.fromisoformat(report_date).year if report_date else None
    years = [base_year - 3, base_year - 2, base_year - 1] if base_year else [None, None, None]
    annual = []
    for year, suffix in zip(years, ("L3", "L2", "L1")):
        annual.append(
            {
                "year": year,
                "roe_pct": _number(row.get(f"ROEPJ_{suffix}")),
                "net_margin_pct": _number(row.get(f"XSJLL_{suffix}")),
                "asset_turnover": (
                    round(value / 100, 6) if (value := _number(row.get(f"TOAZZL_{suffix}"))) is not None else None
                ),
                "equity_multiplier": (
                    round(value / 100, 6) if (value := _number(row.get(f"QYCS_{suffix}"))) is not None else None
                ),
            }
        )
    return {
        "symbol": str(row.get("CORRE_SECURITY_CODE") or ""),
        "name": str(row.get("CORRE_SECURITY_NAME") or ""),
        "roe_3y_average_pct": _number(row.get("ROE_AVG")),
        "net_margin_3y_average_pct": _number(row.get("XSJLL_AVG")),
        "asset_turnover_3y_average": (
            round(value / 100, 6) if (value := _number(row.get("TOAZZL_AVG"))) is not None else None
        ),
        "equity_multiplier_3y_average": (
            round(value / 100, 6) if (value := _number(row.get("QYCS_AVG"))) is not None else None
        ),
        "annual_history": annual,
        "rank": _integer(row.get("PAIMING")),
    }


def _scale_row(row: dict[str, Any]) -> dict[str, Any]:
    total_market_cap = _number(row.get("TOTAL_CAP"))
    free_cap_raw = _number(row.get("FREECAP"))
    circulating_market_cap = None
    circulating_market_cap_source_unit = None
    if free_cap_raw is not None:
        # The current Eastmoney scale report exposes TOTAL_CAP in yuan but
        # FREECAP in 100m yuan.  Validate that assumption against total market
        # cap so an upstream unit change cannot silently multiply an already-
        # yuan value by 1e8.
        yi_candidate = free_cap_raw * 100_000_000
        yuan_candidate = free_cap_raw
        if total_market_cap is None:
            circulating_market_cap = yi_candidate if abs(free_cap_raw) < 10_000_000 else yuan_candidate
            circulating_market_cap_source_unit = "亿元" if circulating_market_cap == yi_candidate else "元"
        else:
            tolerance = abs(total_market_cap) * 1.05
            yi_valid = 0 <= yi_candidate <= tolerance
            yuan_valid = 0 <= yuan_candidate <= tolerance
            if yi_valid and (not yuan_valid or abs(free_cap_raw) < 10_000_000):
                circulating_market_cap = yi_candidate
                circulating_market_cap_source_unit = "亿元"
            elif yuan_valid:
                circulating_market_cap = yuan_candidate
                circulating_market_cap_source_unit = "元"
    return {
        "symbol": str(row.get("CORRE_SECURITY_CODE") or ""),
        "name": str(row.get("CORRE_SECURITY_NAME") or ""),
        "report_period": str(row.get("REPORT_TYPE") or "").strip() or None,
        "total_market_cap": total_market_cap,
        "total_market_cap_rank": _integer(row.get("TOTAL_CAP_RANK")),
        "circulating_market_cap": circulating_market_cap,
        "circulating_market_cap_source_unit": circulating_market_cap_source_unit,
        "market_cap_output_unit": "元",
        "circulating_market_cap_rank": _integer(row.get("FREECAP_RANK")),
        "revenue": _number(row.get("TOTAL_OPERATEINCOME")),
        "revenue_rank": _integer(row.get("TOTAL_OPERATEINCOME_RANK")),
        "net_profit": _number(row.get("NETPROFIT")),
        "net_profit_rank": _integer(row.get("NETPROFIT_RANK")),
    }


_NORMALIZERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "growth": _growth_row,
    "valuation": _valuation_row,
    "profitability": _profitability_row,
    "scale": _scale_row,
}


def _dimension_result(code: str, dimension: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    normalizer = _NORMALIZERS[dimension]
    normalized = [normalizer(row) for row in rows]
    target = next((item for item in normalized if item["symbol"] == code), None)
    median = next((item for item in normalized if item["symbol"] == "行业中值"), None)
    average = next((item for item in normalized if item["symbol"] == "行业平均"), None)
    peers = [item for item in normalized if item["symbol"] not in {code, "行业中值", "行业平均"}]
    sample_size = max((_integer(row.get("TOTAL_COUNT")) or 0 for row in rows), default=0)
    if dimension == "scale":
        sample_size = max(
            sample_size, len([item for item in normalized if item["symbol"] not in {"行业中值", "行业平均"}])
        )
    report_dates = [_date_text(row.get("REPORT_DATE")) for row in rows]
    report_date = max((value for value in report_dates if value), default=None)
    ranking = {
        "growth": {"metric": "eps_growth_3y_cagr_pct", "direction": "higher_is_better"},
        "valuation": {
            "metric": "provider_valuation_rank",
            "direction": "lower_rank_is_better",
            "observed_sort_field": "peg_forward when available",
        },
        "profitability": {"metric": "roe_3y_average_pct", "direction": "higher_is_better"},
        "scale": {"metric": "total_market_cap", "direction": "higher_is_larger"},
    }[dimension]
    target_rank = target.get("rank") if target else None
    if dimension == "scale" and target:
        target_rank = target.get("total_market_cap_rank")
    return {
        "label": _LABELS[dimension],
        "report_date": report_date,
        "report_period": target.get("report_period") if target else None,
        "sample_size": sample_size,
        "target": target,
        "industry_median": median,
        "industry_average": average,
        "target_rank": target_rank,
        "ranking": ranking,
        "top_peers": peers[:10],
        "top_peer_item_count": len(peers),
        "source_scope": (
            "full industry scale table, bounded to top 10 peers in Agent output"
            if dimension == "scale"
            else "target + industry median/average + provider top-ranked peer sample"
        ),
        "success": target is not None,
    }


def get_peer_comparison(symbol: str, dimension: str = "all") -> dict[str, Any]:
    """Legacy multi-dimension convenience view for non-Agent callers only."""
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    if dimension not in {"all", *_REPORTS}:
        raise ValueError("dimension 必须是 all、growth、valuation、profitability 或 scale")
    requested = list(_REPORTS) if dimension == "all" else [dimension]
    errors: list[str] = []
    cache_detail: dict[str, bool] = {}
    raw: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=len(requested)) as pool:
        futures = {
            key: pool.submit(
                cached_call,
                f"peer:v2:{code}:{key}",
                lambda key=key: _request_rows(code, key),
                ttl_seconds=2 * 3600,
                attempts=2,
            )
            for key in requested
        }
        for key, future in futures.items():
            try:
                rows, cached = future.result()
                raw[key] = rows
                cache_detail[key] = cached
            except Exception as exc:
                raw[key] = []
                cache_detail[key] = False
                errors.append(f"{_LABELS[key]}: {exc}")
    dimensions = {
        key: (
            _dimension_result(code, key, raw[key])
            if raw[key]
            else {
                "label": _LABELS[key],
                "success": False,
                "target": None,
                "top_peers": [],
                "sample_size": 0,
            }
        )
        for key in requested
    }
    success = any(item.get("success") for item in dimensions.values())
    report_dates = [item.get("report_date") for item in dimensions.values() if item.get("report_date")]
    now = datetime.now().astimezone()
    data_time = max(report_dates) if report_dates else None
    expected_annual = date(now.year - 1, 12, 31) if now.month >= 5 else date(now.year - 2, 12, 31)
    return {
        "symbol": code,
        "dimension": dimension,
        "dimensions": dimensions,
        "amount_unit": "元",
        "ratio_unit": "% 或 倍，详见字段名后缀与 ranking.metric",
        "source": "东方财富同行比较公开接口",
        "source_url": (
            "https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html"
            f"?type=web&code={exchange_prefix(code, upper=True)}#/thbj"
        ),
        "success": success,
        "partial": success and bool(errors),
        "errors": errors,
        "data_time": data_time,
        "freshness_unknown": success and data_time is None,
        "is_stale": (datetime.fromisoformat(data_time).date() < expected_annual if data_time else None),
        "fallback_used": False,
        "cache_detail": cache_detail,
        "_cached": bool(cache_detail) and all(cache_detail.values()),
        "_fetched_at": now.isoformat(),
    }


def read_peer_comparison_dimension_eastmoney(
    symbol: str,
    dimension: str,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Read one Eastmoney peer-comparison report dimension."""
    code = bare_local_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    if dimension not in _REPORTS:
        raise ValueError("dimension 必须是 growth、valuation、profitability 或 scale")
    rows, cached = (
        cached_call(
            f"peer:v3:{code}:{dimension}",
            lambda: _request_rows(code, dimension),
            ttl_seconds=2 * 3600,
            attempts=2,
        )
        if use_cache
        else (_request_rows(code, dimension), False)
    )
    result = _dimension_result(code, dimension, rows)
    report_date = result.get("report_date")
    now = datetime.now().astimezone()
    expected_annual = date(now.year - 1, 12, 31) if now.month >= 5 else date(now.year - 2, 12, 31)
    result.update(
        {
            "symbol": code,
            "dimension": dimension,
            "amount_unit": "元",
            "ratio_unit": "% 或 倍，详见字段名后缀与 ranking.metric",
            "source": "东方财富同行比较公开接口",
            "source_url": (
                "https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html"
                f"?type=web&code={exchange_prefix(code, upper=True)}#/thbj"
            ),
            "source_scope": f"peer_comparison_{dimension}",
            "partial": False,
            "errors": [],
            "warnings": [],
            "data_time": report_date,
            "freshness_unknown": report_date is None,
            "is_stale": (
                datetime.fromisoformat(report_date).date() < expected_annual
                if report_date
                else None
            ),
            "fallback_used": False,
            "_cached": cached,
            "_fetched_at": now.isoformat(),
        }
    )
    return result


TOOLS = (
    ToolSpec(
        name="read_peer_comparison_dimension_eastmoney",
        description=(
            "从东方财富读取一只 A 股在一个明确同行比较维度上的原始对标数据。"
            "dimension 必须明确选择成长、估值、盈利能力或规模；不并发读取其他维度，也不输出综合评分。"
        ),
        parameters=object_schema(
            {
                "symbol": {"type": "string", "description": "股票代码或股票名称"},
                "dimension": {
                    "type": "string",
                    "enum": ["growth", "valuation", "profitability", "scale"],
                    "description": "同行比较维度",
                },
            },
            ["symbol", "dimension"],
        ),
        executor=read_peer_comparison_dimension_eastmoney,
        category="analysis",
    ),
)


__all__ = ["TOOLS", "get_peer_comparison", "read_peer_comparison_dimension_eastmoney"]
