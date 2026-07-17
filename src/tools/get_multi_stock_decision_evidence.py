# -*- coding: utf-8 -*-
"""Professional, multi-dimensional evidence packet for stock decisions."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from src.tools.base import ToolSpec, object_schema
from src.tools.get_multi_stock_snapshot import get_multi_stock_snapshot
from src.tools.symbols import resolve_securities_csv

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "按照专业股票买入决策标准，一次性为多只 A 股收集完整证据：公司与主营、连续财务趋势、"
    "现金流与负债、PE(TTM)/PB/远期估值、一致预期、行业同行、技术与资金持续性、正式公告"
    "及风险事件。适用于‘这些公司现在能买吗’、个股深度研究和多股横向决策。"
    "该工具不会因估值高或单项失败提前终止，而会返回每家公司所有维度及证据缺口。"
)


def _pick(data: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    return {key: source.get(key) for key in keys if source.get(key) is not None}


def _ok(data: Any) -> bool:
    return isinstance(data, dict) and data.get("success") is not False


def _compact_profile(data: dict[str, Any]) -> dict[str, Any]:
    return _pick(data, (
        "success", "short_name", "company_name", "market", "industry",
        "industry_eastmoney", "listing_date", "main_business", "company_profile",
        "source", "data_time", "is_stale", "errors", "warnings",
    ))


def _compact_financials(data: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "report_date", "report_period", "revenue", "revenue_yoy", "revenue_qoq",
        "parent_net_profit", "parent_net_profit_yoy", "deducted_net_profit",
        "deducted_net_profit_yoy", "gross_margin", "net_margin", "roe",
        "operating_cash_flow", "free_cash_flow", "cash_conversion_ratio",
        "debt_ratio", "accounts_receivable", "inventory", "contract_liabilities",
        "flow_basis",
    )
    return {
        **_pick(data, (
            "success", "partial", "symbol", "periods", "amount_unit", "ratio_unit",
            "source", "data_time", "is_stale", "errors", "warnings",
        )),
        "items": [_pick(item, fields) for item in (data.get("items") or [])[-6:] if isinstance(item, dict)],
    }


def _compact_segments(data: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in data.get("items") or [] if isinstance(item, dict)]
    latest = max((str(item.get("report_date") or "") for item in items), default="")
    relevant = [
        item for item in items
        if str(item.get("report_date") or "") == latest
        and item.get("category") in {"product", "industry"}
    ]
    relevant.sort(key=lambda item: float(item.get("revenue_share_pct") or 0), reverse=True)
    return {
        **_pick(data, (
            "success", "symbol", "periods", "source", "source_url", "data_time",
            "is_stale", "errors", "warnings",
        )),
        "latest_report_date": latest or None,
        "items": [
            _pick(item, (
                "report_date", "flow_basis", "category", "segment_name", "revenue",
                "revenue_share_pct", "gross_profit_share_pct", "gross_margin_pct",
            ))
            for item in relevant[:6]
        ],
    }


def _compact_valuation(data: dict[str, Any]) -> dict[str, Any]:
    return _pick(data, (
        "success", "symbol", "name", "trade_date", "current_price", "pe_ttm",
        "pe_static", "pe_dynamic", "pb_mrq", "ps_ttm", "pcf_ttm",
        "peg_trailing", "peg_forward", "forward_pe", "dividend_yield",
        "history_statistics", "positive_pe_percentile", "industry_average",
        "source", "data_time", "is_stale", "errors", "warnings",
    ))


def _compact_consensus(data: dict[str, Any]) -> dict[str, Any]:
    estimates = []
    for item in (data.get("estimates") or [])[:3]:
        if isinstance(item, dict):
            estimates.append(_pick(item, ("year", "coverage_count", "eps", "net_profit")))
    return {
        **_pick(data, (
            "success", "partial", "symbol", "coverage_available", "coverage_count_latest",
            "latest_institution_report_date", "forecast_warning", "source", "data_time",
            "is_stale", "errors", "warnings",
        )),
        "estimates": estimates,
        "actuals": [
            _pick(item, (
                "year", "revenue_yi", "revenue_growth_pct", "net_profit_yi",
                "net_profit_growth_pct", "cashflow_per_share", "roe_pct",
            ))
            for item in (data.get("actuals") or [])[-3:]
            if isinstance(item, dict)
        ],
    }


def _compact_peers(data: dict[str, Any]) -> dict[str, Any]:
    dimensions: dict[str, Any] = {}
    for dimension, value in (data.get("dimensions") or {}).items():
        if not isinstance(value, dict):
            continue
        dimensions[dimension] = {
            **_pick(value, (
                "label", "report_date", "sample_size", "target_rank", "ranking",
                "source_scope", "success",
            )),
            "target": value.get("target"),
            "industry_median": value.get("industry_median"),
        }
    return {
        **_pick(data, (
            "success", "partial", "symbol", "source", "source_url", "data_time",
            "is_stale", "errors", "warnings",
        )),
        "dimensions": dimensions,
    }


def _compact_risks(data: dict[str, Any]) -> dict[str, Any]:
    return {
        **_pick(data, (
            "success", "partial", "symbol", "name", "has_risk_events", "analysis",
            "source", "source_scope", "data_time", "retrieved_at", "is_stale",
            "freshness_unknown", "errors", "warnings",
        )),
        "items": [
            _pick(item, (
                "title", "date", "publish_time", "risk_category", "risk_label",
                "severity", "status", "source", "url", "summary",
            ))
            for item in (data.get("items") or [])[:8]
            if isinstance(item, dict)
        ],
    }


def _compact_announcements(data: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in data.get("items") or [] if isinstance(item, dict)]
    important = [
        item for item in items
        if item.get("notice_type") != "其他" or item.get("importance") in {"high", "medium"}
    ]
    selected = (important or items)[:8]
    return {
        **_pick(data, (
            "success", "partial", "symbol", "name", "has_announcements", "analysis",
            "coverage_start", "coverage_end", "source", "data_time", "retrieved_at",
            "is_stale", "errors", "warnings",
        )),
        "items": [
            _pick(item, (
                "title", "notice_type", "publish_date", "importance", "tags", "url", "source",
            ))
            for item in selected
        ],
    }


def _compact_flow(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    return {
        **_pick(data, (
            "success", "symbol", "market", "main_flow_definition", "interpretation_warning",
            "source", "data_time", "is_stale", "errors", "warnings",
        )),
        "latest": data.get("latest"),
        "windows": summary.get("windows") or {},
    }


def _callers() -> dict[str, tuple[Callable[..., dict[str, Any]], dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]]:
    from src.tools.get_announcements import get_announcements
    from src.tools.get_business_segments import get_business_segments
    from src.tools.get_consensus_estimates import get_consensus_estimates
    from src.tools.get_financials import get_financials
    from src.tools.get_peer_comparison import get_peer_comparison
    from src.tools.get_risk_events import get_risk_events
    from src.tools.get_stock_capital_flow import get_stock_capital_flow
    from src.tools.get_stock_info import get_stock_info
    from src.tools.get_valuation_ratios import get_valuation_ratios

    return {
        "profile": (get_stock_info, {}, _compact_profile),
        "financials": (get_financials, {"periods": 6}, _compact_financials),
        "business_segments": (get_business_segments, {"category": "all", "periods": 2}, _compact_segments),
        "valuation": (get_valuation_ratios, {"with_history": True}, _compact_valuation),
        "consensus": (get_consensus_estimates, {"metric": "all"}, _compact_consensus),
        "peer_comparison": (get_peer_comparison, {"dimension": "all"}, _compact_peers),
        "risk_events": (get_risk_events, {"days": 180, "limit": 20}, _compact_risks),
        "announcements": (get_announcements, {"days": 180, "type": "all", "limit": 30}, _compact_announcements),
        "capital_flow": (get_stock_capital_flow, {"days": 20}, _compact_flow),
    }


def _run_dimension(
    code: str,
    dimension: str,
    caller: Callable[..., dict[str, Any]],
    kwargs: dict[str, Any],
    compactor: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[str, str, dict[str, Any]]:
    try:
        raw = caller(code, **kwargs)
        compact = compactor(raw if isinstance(raw, dict) else {})
        if not _ok(raw):
            compact.setdefault("success", False)
        return code, dimension, compact
    except Exception as exc:
        logger.warning("decision evidence %s/%s failed: %s", code, dimension, exc)
        return code, dimension, {
            "success": False,
            "errors": [f"{type(exc).__name__}: {str(exc)[:240]}"],
        }


def _coverage(item: dict[str, Any]) -> dict[str, Any]:
    financials = item.get("financials") or {}
    valuation = item.get("valuation") or {}
    consensus = item.get("consensus") or {}
    peers = item.get("peer_comparison") or {}
    technical = (item.get("snapshot") or {}).get("technical") or {}
    dimensions = {
        "business_reality": _ok(item.get("profile")) and _ok(item.get("business_segments")),
        "financial_quality": _ok(financials) and len(financials.get("items") or []) >= 4,
        "valuation": _ok(valuation) and any(valuation.get(key) is not None for key in ("pe_ttm", "pb_mrq", "ps_ttm")),
        "expectations": _ok(consensus) and bool(consensus.get("coverage_available")),
        "peer_context": _ok(peers) and bool(peers.get("dimensions")),
        "trading_state": bool(technical.get("success")) and _ok(item.get("capital_flow")),
        "catalyst_and_risk": _ok(item.get("announcements")) and _ok(item.get("risk_events")),
    }
    missing = [name for name, complete in dimensions.items() if not complete]
    return {
        "dimensions": dimensions,
        "complete_count": sum(bool(value) for value in dimensions.values()),
        "required_count": len(dimensions),
        "missing": missing,
        "complete": not missing,
    }


def _screening_flags(item: dict[str, Any]) -> dict[str, list[str]]:
    negatives: list[str] = []
    positives: list[str] = []
    periods = (item.get("financials") or {}).get("items") or []
    latest = periods[-1] if periods else {}
    valuation = item.get("valuation") or {}
    technical = ((item.get("snapshot") or {}).get("technical") or {}).get("indicators") or {}
    flow_windows = (item.get("capital_flow") or {}).get("windows") or {}
    risks = item.get("risk_events") or {}

    if latest.get("parent_net_profit") is not None:
        (positives if latest["parent_net_profit"] > 0 else negatives).append(
            "最新单季度盈利" if latest["parent_net_profit"] > 0 else "最新单季度亏损"
        )
    if latest.get("revenue_yoy") is not None:
        (positives if latest["revenue_yoy"] > 0 else negatives).append(
            "最新单季度收入同比增长" if latest["revenue_yoy"] > 0 else "最新单季度收入同比下降"
        )
    if latest.get("operating_cash_flow") is not None:
        (positives if latest["operating_cash_flow"] > 0 else negatives).append(
            "最新单季度经营现金流为正" if latest["operating_cash_flow"] > 0 else "最新单季度经营现金流为负"
        )
    if (latest.get("debt_ratio") or 0) >= 70:
        negatives.append("资产负债率较高")
    pe_ttm = valuation.get("pe_ttm")
    industry_pe = (valuation.get("industry_average") or {}).get("pe")
    if pe_ttm and industry_pe and pe_ttm > industry_pe * 1.5:
        negatives.append("PE(TTM)显著高于行业中值口径")
    if valuation.get("peg_forward") is not None:
        (positives if valuation["peg_forward"] <= 1.5 else negatives).append(
            "远期PEG不高于1.5" if valuation["peg_forward"] <= 1.5 else "远期PEG高于1.5"
        )
    if technical.get("return_20d_pct") is not None and technical["return_20d_pct"] <= -15:
        negatives.append("近20日跌幅超过15%")
    flow_10d = flow_windows.get("10d") or {}
    if flow_10d.get("main_net_inflow") is not None:
        (positives if flow_10d["main_net_inflow"] > 0 else negatives).append(
            "10日主力口径资金净流入" if flow_10d["main_net_inflow"] > 0 else "10日主力口径资金净流出"
        )
    active_high = ((risks.get("analysis") or {}).get("active_high_severity_count") or 0)
    if active_high:
        negatives.append(f"存在{active_high}项未缓释高风险事件")
    return {"positive": positives, "negative": negatives}


def get_multi_stock_decision_evidence(symbols: str, thesis: str = "") -> dict[str, Any]:
    resolved, unresolved = resolve_securities_csv(symbols)
    resolved = resolved[:8]
    codes = [item["symbol"] for item in resolved]
    if not codes:
        return {
            "success": False,
            "partial": False,
            "items": [],
            "resolved_entities": [],
            "unresolved_entities": unresolved,
            "errors": ["没有可验证的 A 股公司名称或代码"],
        }

    snapshot = get_multi_stock_snapshot(",".join(codes))
    snapshot_by_code = {
        str(item.get("symbol")): item
        for item in snapshot.get("items") or []
        if isinstance(item, dict)
    }
    details: dict[str, dict[str, Any]] = {code: {} for code in codes}
    callers = _callers()
    futures = []
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(codes) * 2))) as pool:
        for code in codes:
            for dimension, (caller, kwargs, compactor) in callers.items():
                futures.append(pool.submit(
                    _run_dimension,
                    code,
                    dimension,
                    caller,
                    dict(kwargs),
                    compactor,
                ))
        for future in as_completed(futures):
            code, dimension, payload = future.result()
            details[code][dimension] = payload

    entity_by_code = {item["symbol"]: item for item in resolved}
    items: list[dict[str, Any]] = []
    warnings = list(snapshot.get("warnings") or [])
    for code in codes:
        entity = entity_by_code[code]
        item: dict[str, Any] = {
            "symbol": code,
            "name": entity["name"],
            "thesis": thesis.strip() or None,
            "snapshot": snapshot_by_code.get(code),
            **details[code],
        }
        item["evidence_coverage"] = _coverage(item)
        item["screening_flags"] = _screening_flags(item)
        if not item["evidence_coverage"]["complete"]:
            warnings.append(
                f"{entity['name']}({code}) 缺少证据维度: "
                + ", ".join(item["evidence_coverage"]["missing"])
            )
        items.append(item)

    errors = list(snapshot.get("errors") or [])
    if unresolved:
        errors.append("无法解析: " + ", ".join(unresolved))
    success = bool(items)
    partial = bool(errors) or any(not item["evidence_coverage"]["complete"] for item in items)
    return {
        "success": success,
        "partial": partial,
        "playbook": "professional_investment_decision",
        "thesis": thesis.strip() or None,
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "total": len(items),
        "data_time": snapshot.get("data_time"),
        "quote_basis": snapshot.get("quote_basis"),
        "quote_is_intraday": snapshot.get("quote_is_intraday"),
        "source": {
            "snapshot": snapshot.get("source"),
            "profile": "巨潮资讯/AKShare",
            "financials": "同花顺/AKShare核心指标 + 东方财富单季度财报",
            "business_segments": "东方财富主营构成",
            "valuation_consensus_peers": "东方财富结构化估值与预测",
            "events": "东方财富公司公告 + 公司新闻规则筛查",
            "capital_flow": "东方财富成交单大小口径",
        },
        "evidence_standard": [
            "business_reality", "financial_quality", "valuation", "expectations",
            "peer_context", "trading_state", "catalyst_and_risk",
        ],
        "decision_rule": (
            "必须逐家公司综合全部维度，不得因估值高、亏损或技术走弱而提前停止后续分析；"
            "screening_flags 只是确定性信号清单，不是最终买卖建议。"
        ),
        "errors": errors,
        "warnings": warnings,
    }


TOOL = ToolSpec(
    name="get_multi_stock_decision_evidence",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbols": {
                "type": "string",
                "description": "股票代码或公司名称，多个用逗号分隔，最多 8 只",
            },
            "thesis": {
                "type": "string",
                "description": "本轮投资逻辑或产业主题，例如人形机器人；用于提醒模型核验主营兑现，不作为事实证据",
                "default": "",
            },
        },
        ["symbols"],
    ),
    executor=get_multi_stock_decision_evidence,
    category="analysis",
)


__all__ = ["TOOL", "get_multi_stock_decision_evidence"]
