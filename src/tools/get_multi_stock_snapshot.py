# -*- coding: utf-8 -*-
"""Batch, entity-safe market snapshot for comparing multiple A-share stocks."""

from __future__ import annotations

from typing import Any

from src.tools.base import ToolSpec, object_schema
from src.tools.get_realtime_quotes import get_realtime_quotes
from src.tools.get_technical_indicators import get_technical_indicators
from src.tools.symbols import resolve_securities_csv
from src.tools._trading_calendar import is_trading_time


DESCRIPTION = (
    "一次解析并查询多只 A 股的当前行情、估值快照、本地技术指标与最新报告期财务质量。"
    "适用于多公司比较、候选股筛选、'这些公司现在能买吗'等需要同时核验多只证券的追问。"
    "返回经过 stock_meta 校验的公司名/代码映射；不会生成买卖建议。"
)


def _financial_snapshots(codes: list[str]) -> dict[str, dict[str, Any]]:
    """Read the latest synchronized fundamentals in one local query.

    A comparison tool must not fan out into one slow network request per
    company.  ``stock_meta`` is the project's synchronized, report-period
    keyed source for the compact fields needed by a preliminary decision.
    """
    from src.storage import DatabaseManager, StockMeta

    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        rows = session.query(StockMeta).filter(StockMeta.code.in_(codes)).all()
        return {
            row.code: {
                "report_date": row.report_date,
                "revenue": row.revenue_latest,
                "net_profit": row.net_profit_latest,
                "operating_cash_flow": row.operating_cf_latest,
                "debt_ratio_pct": row.debt_ratio,
                "fetched_at": (
                    row.financial_fetched_at.isoformat()
                    if row.financial_fetched_at else None
                ),
                "flow_basis": "latest_report_period",
                "amount_unit": "元",
            }
            for row in rows
        }


def _technical_summary(result: dict[str, Any]) -> dict[str, Any]:
    indicators = result.get("indicators") or {}
    keys = (
        "close", "ma5", "ma10", "ma20", "ma60", "rsi14",
        "macd_dif", "macd_dea", "macd_hist", "boll_lower", "boll_mid",
        "boll_upper", "return_5d_pct", "return_20d_pct", "return_60d_pct",
        "volume_vs_prev5d",
    )
    return {
        "success": bool(result.get("success")),
        "data_time": result.get("data_time") or result.get("date"),
        "is_stale": result.get("is_stale"),
        "source": result.get("source"),
        "bar_complete": result.get("bar_complete"),
        "indicators": {key: indicators.get(key) for key in keys},
        "errors": result.get("errors") or [],
    }


def get_multi_stock_snapshot(symbols: str) -> dict[str, Any]:
    resolved, unresolved = resolve_securities_csv(symbols)
    resolved = resolved[:12]
    codes = [item["symbol"] for item in resolved]
    if not codes:
        return {
            "success": False,
            "partial": False,
            "items": [],
            "resolved_entities": [],
            "unresolved_entities": unresolved,
            "errors": ["没有可验证的 A 股公司名称或代码"],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
            "fallback_used": False,
        }

    quote_is_intraday = is_trading_time()
    quote_result = get_realtime_quotes(codes)
    financials_by_code = _financial_snapshots(codes)
    quotes_by_code = {
        str(item.get("code")): item
        for item in quote_result.get("items") or []
        if isinstance(item, dict) and item.get("code")
    }
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for entity in resolved:
        code = entity["symbol"]
        quote = quotes_by_code.get(code)
        technical = _technical_summary(get_technical_indicators(code))
        if technical.get("is_stale") is True:
            warnings.append(
                f"{entity['name']}({code}) 技术指标截至 {technical.get('data_time') or '未知日期'}，不是最新交易日"
            )
        items.append({
            "symbol": code,
            "name": entity["name"],
            "input": entity["input"],
            "quote": quote,
            "technical": technical,
            "financial": financials_by_code.get(code),
        })

    errors = list(quote_result.get("errors") or [])
    if unresolved:
        errors.append(f"无法解析: {', '.join(unresolved)}")
    successful_items = [
        item for item in items
        if (
            item.get("quote") is not None
            or item["technical"].get("success")
            or item.get("financial") is not None
        )
    ]
    success = bool(successful_items)
    partial = success and (
        bool(errors)
        or len(successful_items) < len(items)
        or any(item.get("quote") is None for item in items)
    )
    return {
        "success": success,
        "partial": partial,
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "total": len(items),
        "data_time": quote_result.get("data_time"),
        "quote_basis": (
            "盘中实时快照（当日尚未收盘，不是收盘价）"
            if quote_is_intraday
            else "非交易时段的最近市场快照"
        ),
        "quote_is_intraday": quote_is_intraday,
        "is_stale": quote_result.get("is_stale"),
        "fallback_used": bool(quote_result.get("fallback_used")),
        "source": {
            "quotes": quote_result.get("source") or [],
            "technical": "stock_daily 优先，缺失时 K 线多源链",
            "financial": "stock_meta 已同步最新报告期财务快照",
        },
        "errors": errors,
        "warnings": warnings,
        "decision_boundary": (
            "该工具返回适合多公司初筛的行情、估值、技术和最新报告期财务证据。"
            "它不替代逐家公司核验业务兑现与最新公告；最终判断还需结合用户期限和风险承受能力。"
        ),
    }


TOOL = ToolSpec(
    name="get_multi_stock_snapshot",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbols": {
                "type": "string",
                "description": "股票代码或公司名称，多个用逗号分隔，最多 12 只",
            },
        },
        ["symbols"],
    ),
    executor=get_multi_stock_snapshot,
    category="analysis",
)


__all__ = ["TOOL", "get_multi_stock_snapshot"]
