"""Normalized Chinese macro releases with indicator-specific semantics."""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any, Callable

from src.tools._akshare import cached_call
from src.tools._macro_common import expected_indicator_period, get_db, latest_date, number, ordered
from src.tools.base import ToolSpec, object_schema


INDICATORS: dict[str, dict[str, Any]] = {
    "PMI": {"name": "制造业采购经理指数", "frequency": "monthly", "unit": "index_point"},
    "CPI": {"name": "居民消费价格指数", "frequency": "monthly", "unit": "index_previous_year_100"},
    "PPI": {"name": "工业生产者出厂价格指数", "frequency": "monthly", "unit": "index_previous_year_100"},
    "GDP": {"name": "国内生产总值", "frequency": "quarterly", "unit": "亿元"},
    "M2": {"name": "广义货币(M2)", "frequency": "monthly", "unit": "亿元"},
    "社融": {"name": "社会融资规模增量", "frequency": "monthly", "unit": "亿元"},
    "LPR": {"name": "贷款市场报价利率", "frequency": "monthly", "unit": "%"},
}


def _fetcher(indicator: str) -> Callable[[], Any]:
    import akshare as ak

    return {
        "PMI": ak.macro_china_pmi,
        "CPI": ak.macro_china_cpi,
        "PPI": ak.macro_china_ppi,
        "GDP": ak.macro_china_gdp,
        "M2": ak.macro_china_money_supply,
        "社融": ak.macro_china_shrzgm,
        "LPR": ak.macro_china_lpr,
    }[indicator]


def _normalize(frame: Any, indicator: str) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        if indicator == "PMI":
            record = {
                "period": str(row.get("月份") or "").strip(),
                "value": number(row.get("制造业-指数")),
                "yoy_pct": number(row.get("制造业-同比增长")),
                "extra": {
                    "non_manufacturing_index": number(row.get("非制造业-指数")),
                    "non_manufacturing_yoy_pct": number(row.get("非制造业-同比增长")),
                },
            }
        elif indicator == "CPI":
            record = {
                "period": str(row.get("月份") or "").strip(),
                "value": number(row.get("全国-当月")),
                "yoy_pct": number(row.get("全国-同比增长")),
                "mom_pct": number(row.get("全国-环比增长")),
            }
        elif indicator == "PPI":
            record = {
                "period": str(row.get("月份") or "").strip(),
                "value": number(row.get("当月")),
                "yoy_pct": number(row.get("当月同比增长")),
            }
        elif indicator == "GDP":
            record = {
                "period": str(row.get("季度") or "").strip(),
                "value": number(row.get("国内生产总值-绝对值")),
                "yoy_pct": number(row.get("国内生产总值-同比增长")),
            }
        elif indicator == "M2":
            record = {
                "period": str(row.get("月份") or "").strip(),
                "value": number(row.get("货币和准货币(M2)-数量(亿元)")),
                "yoy_pct": number(row.get("货币和准货币(M2)-同比增长")),
                "mom_pct": number(row.get("货币和准货币(M2)-环比增长")),
            }
        elif indicator == "社融":
            record = {
                "period": str(row.get("月份") or "").strip(),
                "value": number(row.get("社会融资规模增量")),
                "extra": {
                    "rmb_loans": number(row.get("其中-人民币贷款")),
                    "entrusted_loans": number(row.get("其中-委托贷款")),
                    "trust_loans": number(row.get("其中-信托贷款")),
                    "corporate_bonds": number(row.get("其中-企业债券")),
                    "domestic_equity_financing": number(row.get("其中-非金融企业境内股票融资")),
                },
            }
        else:
            record = {
                "period": str(row.get("TRADE_DATE") or "").strip(),
                "value": number(row.get("LPR1Y")),
                "extra": {"lpr_5y_pct": number(row.get("LPR5Y"))},
            }
        if record.get("period") and record.get("value") is not None:
            # Storage compatibility while preserving explicit percent names.
            record["yoy"] = record.get("yoy_pct")
            record["mom"] = record.get("mom_pct")
            rows.append(record)
    return ordered(rows, "period")


def fetch_indicator_records(indicator: str) -> list[dict[str, Any]]:
    """Compatibility hook for non-Agent consumers needing normalized rows."""
    return _normalize(_fetcher(indicator)(), indicator)


INDICATOR_FETCHERS = {indicator: partial(fetch_indicator_records, indicator) for indicator in INDICATORS}


def _trend(records: list[dict[str, Any]]) -> dict[str, Any]:
    metric = "yoy_pct" if sum(row.get("yoy_pct") is not None for row in records[-5:]) >= 2 else "value"
    values = [number(row.get(metric)) for row in records[-5:]]
    values = [value for value in values if value is not None]
    if len(values) < 2:
        return {
            "direction": None,
            "semantic_status": "model_required",
            "metric": metric,
            "change": None,
        }
    change = values[-1] - values[0]
    return {
        "direction": None,
        "semantic_status": "model_required",
        "metric": metric,
        "change": round(change, 4),
        "window_observations": len(values),
    }


def get_macro_indicator(
    indicator: str,
    periods: int = 12,
    *,
    months: int | None = None,
) -> dict[str, Any]:
    indicator = str(indicator or "").strip()
    indicator = indicator if indicator == "社融" else indicator.upper()
    if indicator not in INDICATORS:
        raise ValueError(f"不支持的宏观指标: {indicator}")
    if months is not None:
        periods = months
    periods = int(periods)
    if not 3 <= periods <= 120:
        raise ValueError("periods 必须在 3 到 120 之间")

    errors: list[str] = []
    warnings: list[str] = []
    frame = None
    cached = False
    try:
        frame, cached = cached_call(f"macro-indicator:{indicator}", _fetcher(indicator), ttl_seconds=6 * 3600)
    except Exception as exc:
        errors.append(f"{indicator}: {exc}")
    records = _normalize(frame, indicator)[-periods:]
    fallback_used = False
    if records:
        try:
            get_db().save_macro_indicator(indicator, records)
        except Exception as exc:
            warnings.append(f"宏观指标本地缓存写入失败: {exc}")
    else:
        fallback_used = True
        try:
            records = ordered(get_db().get_macro_indicator(indicator, limit=periods) or [], "period")[-periods:]
            for row in records:
                if "yoy_pct" not in row:
                    row["yoy_pct"] = row.get("yoy")
                if "mom_pct" not in row:
                    row["mom_pct"] = row.get("mom")
        except Exception as exc:
            errors.append(f"宏观指标本地缓存: {exc}")
            records = []

    actual = latest_date(records, "period")
    expected = expected_indicator_period(indicator)
    stale = actual < expected if actual else None
    if stale and actual:
        warnings.append(f"{indicator} 最新期间为 {actual.isoformat()}，正常发布日历下预期至少为 {expected.isoformat()}")
    success = bool(records)
    latest = records[-1] if records else {}
    retrieved_at = datetime.now().astimezone().isoformat()
    return {
        "indicator": indicator,
        "indicator_name": INDICATORS[indicator]["name"],
        "frequency": INDICATORS[indicator]["frequency"],
        "unit": INDICATORS[indicator]["unit"],
        "latest": latest,
        "history": records,
        "history_count": len(records),
        "trend": _trend(records),
        "expected_latest_period_end": expected.isoformat(),
        "source": "AKShare/东方财富宏观数据" if not fallback_used else "本地宏观指标缓存",
        "success": success,
        "partial": success and bool(errors or warnings),
        "data_time": actual.isoformat() if actual else None,
        "retrieved_at": retrieved_at,
        "is_stale": stale,
        "freshness_unknown": actual is None,
        "fallback_used": fallback_used,
        "fallback_recommended": not success or stale is True,
        "errors": errors[:10],
        "warnings": warnings[:10],
        "_cached": cached if not fallback_used else True,
        "_fetched_at": retrieved_at,
    }


TOOL = ToolSpec(
    name="get_macro_indicator",
    description=(
        "获取中国 PMI、CPI、PPI、GDP、M2、社融或 LPR 的结构化历史。按指标分别标注频率、单位、"
        "同比/环比含义和正常发布日历；上游落后时明确标为 stale，不把缓存命中误报为降级。"
    ),
    parameters=object_schema(
        {
            "indicator": {"type": "string", "enum": list(INDICATORS)},
            "periods": {
                "type": "integer",
                "minimum": 3,
                "maximum": 120,
                "default": 12,
                "description": "返回最近发布期数；GDP 为季度，其余通常为月度",
            },
        },
        ["indicator"],
    ),
    executor=get_macro_indicator,
    category="macro",
)
