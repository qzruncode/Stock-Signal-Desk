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
    "为最多8只 A 股的深度研究或横向比较收集完整证据：公司与主营、连续财务趋势、"
    "现金流与负债、PE(TTM)/PB/远期估值、一致预期、行业同行、技术与资金持续性、正式公告"
    "及风险事件。该工具适用于个股深度研究和多股横向比较，也作为八维专业买入分析的基础证据包；"
    "它本身不输出最终买卖结论，也不会提前终止任何研究维度。"
)


def _pick(data: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    return {key: source.get(key) for key in keys if source.get(key) is not None}


def _ok(data: Any) -> bool:
    return isinstance(data, dict) and data.get("success") is not False


def _compact_profile(data: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        data,
        (
            "success",
            "short_name",
            "company_name",
            "market",
            "industry",
            "industry_eastmoney",
            "listing_date",
            "main_business",
            "company_profile",
            "source",
            "data_time",
            "is_stale",
            "errors",
            "warnings",
        ),
    )


def _compact_financials(data: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "report_date",
        "report_period",
        "revenue",
        "revenue_yoy",
        "revenue_qoq",
        "parent_net_profit",
        "parent_net_profit_yoy",
        "deducted_net_profit",
        "deducted_net_profit_yoy",
        "gross_margin",
        "net_margin",
        "roe",
        "operating_cash_flow",
        "free_cash_flow",
        "cash_conversion_ratio",
        "debt_ratio",
        "accounts_receivable",
        "inventory",
        "contract_liabilities",
        "flow_basis",
    )
    return {
        **_pick(
            data,
            (
                "success",
                "partial",
                "symbol",
                "periods",
                "amount_unit",
                "ratio_unit",
                "source",
                "data_time",
                "is_stale",
                "errors",
                "warnings",
            ),
        ),
        "items": [_pick(item, fields) for item in (data.get("items") or [])[-10:] if isinstance(item, dict)],
    }


def _compact_segments(data: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in data.get("items") or [] if isinstance(item, dict)]
    latest = max((str(item.get("report_date") or "") for item in items), default="")
    relevant = [
        item
        for item in items
        if str(item.get("report_date") or "") == latest and item.get("category") in {"product", "industry"}
    ]
    relevant.sort(key=lambda item: float(item.get("revenue_share_pct") or 0), reverse=True)
    return {
        **_pick(
            data,
            (
                "success",
                "symbol",
                "periods",
                "source",
                "source_url",
                "data_time",
                "is_stale",
                "errors",
                "warnings",
            ),
        ),
        "latest_report_date": latest or None,
        "items": [
            _pick(
                item,
                (
                    "report_date",
                    "flow_basis",
                    "category",
                    "segment_name",
                    "revenue",
                    "revenue_share_pct",
                    "gross_profit_share_pct",
                    "gross_margin_pct",
                ),
            )
            for item in relevant[:6]
        ],
    }


def _compact_valuation(data: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        data,
        (
            "success",
            "symbol",
            "name",
            "trade_date",
            "current_price",
            "pe_ttm",
            "pe_static",
            "pe_dynamic",
            "pb_mrq",
            "ps_ttm",
            "pcf_ttm",
            "peg_trailing",
            "peg_forward",
            "forward_pe",
            "dividend_yield",
            "history_statistics",
            "positive_pe_percentile",
            "industry_average",
            "source",
            "data_time",
            "is_stale",
            "errors",
            "warnings",
        ),
    )


def _compact_consensus(data: dict[str, Any]) -> dict[str, Any]:
    estimates = []
    for item in (data.get("estimates") or [])[:3]:
        if isinstance(item, dict):
            estimates.append(_pick(item, ("year", "coverage_count", "eps", "net_profit")))
    return {
        **_pick(
            data,
            (
                "success",
                "partial",
                "symbol",
                "coverage_available",
                "coverage_count_latest",
                "coverage_status",
                "source_query_complete",
                "latest_institution_report_date",
                "forecast_warning",
                "source",
                "data_time",
                "is_stale",
                "errors",
                "warnings",
            ),
        ),
        "estimates": estimates,
        "actuals": [
            _pick(
                item,
                (
                    "year",
                    "revenue_yi",
                    "revenue_growth_pct",
                    "net_profit_yi",
                    "net_profit_growth_pct",
                    "cashflow_per_share",
                    "roe_pct",
                ),
            )
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
            **_pick(
                value,
                (
                    "label",
                    "report_date",
                    "sample_size",
                    "target_rank",
                    "ranking",
                    "source_scope",
                    "success",
                ),
            ),
            "target": value.get("target"),
            "industry_median": value.get("industry_median"),
        }
    return {
        **_pick(
            data,
            (
                "success",
                "partial",
                "symbol",
                "source",
                "source_url",
                "data_time",
                "is_stale",
                "errors",
                "warnings",
            ),
        ),
        "dimensions": dimensions,
    }


def _compact_risks(data: dict[str, Any]) -> dict[str, Any]:
    return {
        **_pick(
            data,
            (
                "success",
                "partial",
                "symbol",
                "name",
                "has_risk_events",
                "analysis",
                "source",
                "source_scope",
                "data_time",
                "retrieved_at",
                "is_stale",
                "freshness_unknown",
                "errors",
                "warnings",
            ),
        ),
        "items": [
            _pick(
                item,
                (
                    "title",
                    "date",
                    "source",
                    "source_type",
                    "url",
                    "summary",
                    "evidence_basis",
                    "semantic_status",
                    "requires_fulltext_verification",
                ),
            )
            for item in (data.get("items") or [])[:20]
            if isinstance(item, dict)
        ],
    }


def _compact_announcements(data: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in data.get("items") or [] if isinstance(item, dict)]
    return {
        **_pick(
            data,
            (
                "success",
                "partial",
                "symbol",
                "name",
                "has_announcements",
                "analysis",
                "coverage_start",
                "coverage_end",
                "source",
                "data_time",
                "retrieved_at",
                "is_stale",
                "errors",
                "warnings",
            ),
        ),
        "items": [
            _pick(
                item,
                (
                    "title",
                    "notice_type",
                    "source_notice_type",
                    "publish_date",
                    "url",
                    "source",
                    "source_type",
                    "semantic_status",
                ),
            )
            for item in items[:20]
        ],
    }


def _compact_flow(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    return {
        **_pick(
            data,
            (
                "success",
                "symbol",
                "market",
                "main_flow_definition",
                "interpretation_warning",
                "source",
                "data_time",
                "is_stale",
                "errors",
                "warnings",
            ),
        ),
        "latest": data.get("latest"),
        "windows": summary.get("windows") or {},
    }


def _compact_structured(data: dict[str, Any]) -> dict[str, Any]:
    compact_sections: dict[str, Any] = {}
    tail_datasets = {"northbound_holding_history", "chip_distribution"}
    for section_name, section in (data.get("sections") or {}).items():
        compact_datasets: dict[str, Any] = {}
        for dataset_name, dataset in (section.get("datasets") or {}).items():
            items = [item for item in dataset.get("items") or [] if isinstance(item, dict)]
            selected = items[-20:] if dataset_name in tail_datasets else items[:8]
            compact_datasets[dataset_name] = {
                **_pick(
                    dataset,
                    ("success", "partial", "source_api", "item_count", "source_row_count", "error"),
                ),
                "items": selected,
            }
        compact_sections[section_name] = {
            **_pick(
                section,
                (
                    "success",
                    "partial",
                    "coverage_complete",
                    "available_dataset_count",
                    "required_dataset_count",
                    "data_time",
                    "errors",
                    "warnings",
                ),
            ),
            "datasets": compact_datasets,
        }
    return {
        **_pick(
            data,
            (
                "success",
                "partial",
                "symbol",
                "coverage_complete",
                "retrieval_only",
                "semantic_status",
                "source",
                "data_time",
                "errors",
                "warnings",
            ),
        ),
        "sections": compact_sections,
    }


def _callers() -> (
    dict[str, tuple[Callable[..., dict[str, Any]], dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]]
):
    from src.tools.get_announcements import get_announcements
    from src.tools.get_business_segments import get_business_segments
    from src.tools.get_consensus_estimates import get_consensus_estimates
    from src.tools.get_financials import get_financials
    from src.tools.get_peer_comparison import get_peer_comparison
    from src.tools.get_risk_events import get_risk_events
    from src.tools.get_stock_capital_flow import get_stock_capital_flow
    from src.tools.get_stock_info import get_stock_info
    from src.tools.get_valuation_ratios import get_valuation_ratios
    from src.tools.get_company_structured_evidence import get_company_structured_evidence

    return {
        "profile": (get_stock_info, {}, _compact_profile),
        "financials": (get_financials, {"periods": 10}, _compact_financials),
        "business_segments": (get_business_segments, {"category": "all", "periods": 2}, _compact_segments),
        "valuation": (get_valuation_ratios, {"with_history": True}, _compact_valuation),
        "consensus": (get_consensus_estimates, {"metric": "all"}, _compact_consensus),
        "peer_comparison": (get_peer_comparison, {"dimension": "all"}, _compact_peers),
        "risk_events": (get_risk_events, {"days": 730, "limit": 60}, _compact_risks),
        "announcements": (get_announcements, {"days": 730, "type": "all", "limit": 100}, _compact_announcements),
        "capital_flow": (get_stock_capital_flow, {"days": 20}, _compact_flow),
        "structured_company_evidence": (
            get_company_structured_evidence,
            {"scope": "all", "days": 730, "report_period_count": 4},
            _compact_structured,
        ),
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
        return (
            code,
            dimension,
            {
                "success": False,
                "errors": [f"{type(exc).__name__}: {str(exc)[:240]}"],
            },
        )


def _coverage(item: dict[str, Any]) -> dict[str, Any]:
    technical = (item.get("snapshot") or {}).get("technical") or {}
    structured = item.get("structured_company_evidence") or {}
    structured_sections = structured.get("sections") or {}
    structured_required = "structured_company_evidence" in item
    dimensions = {
        "business_reality": (
            _ok(item.get("profile"))
            and _ok(item.get("business_segments"))
            and (not structured_required or _ok(structured_sections.get("ownership")))
        ),
        "financial_quality": (
            _ok(item.get("financials"))
            and (not structured_required or _ok(structured_sections.get("financial_events")))
        ),
        "valuation": _ok(item.get("valuation")),
        "expectations": _ok(item.get("consensus")),
        "peer_context": _ok(item.get("peer_comparison")),
        "trading_state": (
            bool(technical.get("success"))
            and _ok(item.get("capital_flow"))
            and (not structured_required or _ok(structured_sections.get("trading_evidence")))
        ),
        "catalyst_and_risk": (
            _ok(item.get("announcements"))
            and _ok(item.get("risk_events"))
            and (not structured_required or _ok(structured_sections.get("corporate_events")))
        ),
    }
    missing = [name for name, complete in dimensions.items() if not complete]
    return {
        "dimensions": dimensions,
        "complete_count": sum(bool(value) for value in dimensions.values()),
        "required_count": len(dimensions),
        "missing": missing,
        "complete": not missing,
    }


def get_multi_stock_decision_evidence(symbols: str, thesis: str = "") -> dict[str, Any]:
    resolved, unresolved = resolve_securities_csv(symbols)
    if len(resolved) > 8:
        return {
            "success": False,
            "partial": False,
            "items": [],
            "resolved_entities": [],
            "unresolved_entities": unresolved,
            "total": 0,
            "errors": ["深度研究工具单次最多8只；本次没有静默截断，请拆分研究任务"],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
        }
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
    snapshot_by_code = {str(item.get("symbol")): item for item in snapshot.get("items") or [] if isinstance(item, dict)}
    details: dict[str, dict[str, Any]] = {code: {} for code in codes}
    callers = _callers()
    futures = []
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(codes) * 2))) as pool:
        for code in codes:
            for dimension, (caller, kwargs, compactor) in callers.items():
                futures.append(
                    pool.submit(
                        _run_dimension,
                        code,
                        dimension,
                        caller,
                        dict(kwargs),
                        compactor,
                    )
                )
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
        if not item["evidence_coverage"]["complete"]:
            warnings.append(
                f"{entity['name']}({code}) 缺少证据维度: " + ", ".join(item["evidence_coverage"]["missing"])
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
            "events": "东方财富公司公告 + 公司新闻原始证据",
            "structured_company_evidence": "AKShare股权/财务披露/公司事项/筹码与龙虎榜",
            "capital_flow": "东方财富成交单大小口径",
        },
        "evidence_standard": [
            "business_reality",
            "financial_quality",
            "valuation",
            "expectations",
            "peer_context",
            "trading_state",
            "catalyst_and_risk",
        ],
        "decision_rule": (
            "本工具只报告各来源是否成功及原始结构化证据，不根据数值阈值生成买卖标签；" "语义判断由后续分析模型完成。"
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
