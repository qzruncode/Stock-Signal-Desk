# -*- coding: utf-8 -*-
"""Agent tool helpers — result formatting, compaction, and fallback logic."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

LLM_ARRAY_LIMIT = 8
LLM_SERIES_LIMIT = 30


def _format_result(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _pick_fields(item: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    return {
        field: item.get(field)
        for field in fields
        if field in item and item.get(field) is not None
    }


def _trim_list(
    items: Any,
    limit: int,
    fields: List[str] | None = None,
    text_limits: Dict[str, int] | None = None,
) -> list[Any]:
    if not isinstance(items, list):
        return []
    trimmed = items[:limit]
    if fields is None:
        return trimmed
    result: list[Any] = []
    for item in trimmed:
        if isinstance(item, dict):
            picked = _pick_fields(item, fields)
            for field, character_limit in (text_limits or {}).items():
                value = picked.get(field)
                if not isinstance(value, str) or len(value) <= character_limit:
                    continue
                picked[field] = value[:character_limit].rstrip() + "…"
                picked[f"{field}_characters"] = len(value)
                picked[f"{field}_compacted"] = True
            result.append(picked)
        else:
            result.append(item)
    return result


def _annotate_tool_payload(
    tool_name: str,
    payload: Dict[str, Any],
    *,
    payload_policy: str,
    compacted: bool,
    compaction_reason: str | None = None,
    source_scope: str = "tool_defined_view",
) -> Dict[str, Any]:
    annotated = dict(payload)
    annotated["_tool_payload_meta"] = {
        "tool_name": tool_name,
        "payload_policy": payload_policy,
        "compacted": compacted,
        "compaction_reason": compaction_reason,
        "source_scope": source_scope,
    }
    return annotated


def _normalize_stock_info(result: Dict[str, Any]) -> Dict[str, Any]:
    """把 get_stock_info endpoint 的嵌套中文结构拍平成英文 key 字典。

    endpoint `_fetch_all` 返回的是按数据源嵌套 + 中文字段名的结构:
      { symbol, _sources, cninfo: {中文列名: 值}, eastmoney: {中文item: 值},
        ths_business: {...} }
    而 `_compact_tool_result` 的白名单用的是英文 key(name/industry/pe_dynamic/...),
    直接 `_pick_fields` 顶层取不到值(只剩 symbol)。这里做归一化,把中文字段名映射
    成白名单 key,多候选名兜底(akshare 不同接口/版本 item 名有差异)。
    与 get_realtime_quotes 的 _QUOTE_FIELD_MAP 同思路。
    """
    # (英文目标key, [候选中文字段名(按优先级)], 数据源子dict名)
    # cninfo 列名来自 stock_profile_cninfo;em item 名来自 stock_individual_info_em。
    _MAP: list[tuple[str, list[str], str]] = [
        ("name", ["A股简称", "股票简称", "公司名称"], "cninfo"),
        ("short_name", ["A股简称", "股票简称"], "cninfo"),
        ("industry", ["所属行业", "行业"], "cninfo"),
        ("market", ["所属市场"], "cninfo"),
        ("listing_date", ["上市日期", "上市时间"], "cninfo"),
        ("main_business", ["主营业务"], "cninfo"),
        ("total_shares", ["总股本"], "eastmoney"),
        ("circ_shares", ["流通股本", "流通股"], "eastmoney"),
        ("pe_dynamic", ["市盈率(动态)", "市盈率-动态", "动态市盈率"], "eastmoney"),
        ("pe_static", ["市盈率(静态)", "市盈率-静态", "静态市盈率"], "eastmoney"),
        ("pb_ratio", ["市净率"], "eastmoney"),
        ("total_mv", ["总市值"], "eastmoney"),
        ("circ_mv", ["流通市值"], "eastmoney"),
    ]
    flat: Dict[str, Any] = {"symbol": result.get("symbol")}
    for dst, candidates, source in _MAP:
        bucket = result.get(source)
        if not isinstance(bucket, dict):
            continue
        for cand in candidates:
            val = bucket.get(cand)
            if val is not None and str(val).strip() not in ("", "--", "-", "nan", "None"):
                flat[dst] = val
                break
    # 保留新鲜度元数据(若有)
    for meta_key in ("_cached", "_fetched_at", "_sources"):
        if result.get(meta_key) is not None:
            flat[meta_key] = result.get(meta_key)
    return flat


def _compact_time_series(result: Dict[str, Any], key: str = "data") -> Dict[str, Any]:
    series = result.get(key)
    if not isinstance(series, list):
        return result
    latest = series[-1] if series else {}
    compact = {
        "success": result.get("success"),
        "partial": result.get("partial"),
        "symbol": result.get("symbol") or result.get("index_code"),
        "count": len(series),
        "latest": latest,
        "recent": series[-LLM_SERIES_LIMIT:],
        "source": result.get("source"),
        "data_time": result.get("data_time") or latest.get("date"),
        "is_stale": result.get("is_stale"),
        "fallback_used": result.get("fallback_used"),
        "errors": result.get("errors", []),
        "warnings": result.get("warnings", []),
        "_cached": result.get("_cached"),
        "_fetched_at": result.get("_fetched_at"),
        "adjust": result.get("adjust"),
        "period": result.get("period"),
        "volume_unit": result.get("volume_unit"),
        "amount_unit": result.get("amount_unit"),
        "bar_complete": result.get("bar_complete"),
    }
    if series:
        compact["range"] = {
            "start": series[0].get("date"),
            "end": series[-1].get("date"),
        }
    return compact


def _compact_tool_result(tool_name: str, result: Any) -> Any:
    """Shape endpoint results into LLM-friendly payloads with less noise."""
    if not isinstance(result, dict):
        return result

    workflow_tools = {
        "manage_watchlist", "manage_watchlist_groups",
        "run_stock_analysis", "get_analysis_status", "search_analysis_history",
        "delete_analysis_history", "manage_analysis_templates", "run_batch_analysis",
        "manage_batch_run", "manage_analysis_schedule", "get_notification_status",
        "send_notification",
    }
    if tool_name in workflow_tools:
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="complete",
            compacted=False,
            source_scope="persisted_analysis_workflow",
        )

    if tool_name == "read_analysis_report":
        compact = dict(result)
        markdown = str(compact.get("markdown") or "")
        if len(markdown) > 12000:
            compact["markdown"] = markdown[:12000]
            compact["markdown_excerpt"] = True
            compact["markdown_length"] = len(markdown)
            compact["markdown_note"] = "工具上下文仅保留前 12000 字；结构化 report 为完整正式报告。"
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted" if len(markdown) > 12000 else "complete",
            compacted=len(markdown) > 12000,
            compaction_reason="markdown_context_window" if len(markdown) > 12000 else None,
            source_scope="persisted_analysis_report",
        )

    if tool_name == "screen_atr_volatility_stocks":
        # The service bounds inline rows according to the validated spec and
        # exports the complete set. Preserve the spec, rule echo, every
        # decision field and the download link without model-facing compaction.
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="complete",
            compacted=False,
        )

    if tool_name == "get_realtime_quotes":
        # 后端 UnifiedRealtimeQuote.to_dict() 的字段名(code/change_pct/change_amount/
        # open_price/pe_ratio/pb_ratio)与前端 RealtimeQuotesToolUI 期望的
        # (symbol/pct_chg/change/open/pe/pb)不一致。直接 _pick_fields 会取不到值,
        # 导致前端卡片字段全空 + React key(item.symbol)为 undefined。这里做字段映射,
        # 把后端真实字段归一为前端/类型约定的字段名。
        _QUOTE_FIELD_MAP = {
            "code": "symbol",
            "change_pct": "pct_chg",
            "change_amount": "change",
            "open_price": "open",
            "pe_ratio": "pe_dynamic",
            "pb_ratio": "pb",
        }
        _QUOTE_PASSTHROUGH = (
            "name", "price", "high", "low", "volume", "amount",
            "turnover_rate", "total_mv", "circ_mv", "source", "trade_time", "data_time",
            "is_stale", "fallback_used", "volume_unit", "amount_unit", "market_value_unit",
            "quote_mode", "quote_mode_label",
        )
        raw_items = result.get("items", []) if isinstance(result, dict) else []
        compact_items: list[Any] = []
        for item in _trim_list(raw_items, 12):
            if not isinstance(item, dict):
                compact_items.append(item)
                continue
            mapped: Dict[str, Any] = {}
            for src, dst in _QUOTE_FIELD_MAP.items():
                if item.get(src) is not None:
                    mapped[dst] = item.get(src)
            for f in _QUOTE_PASSTHROUGH:
                if item.get(f) is not None:
                    mapped[f] = item.get(f)
            compact_items.append(mapped)
        return _annotate_tool_payload(tool_name, {
            "success": result.get("success"),
            "partial": result.get("partial"),
            "total": result.get("total", len(compact_items)),
            "items": compact_items,
            "data_time": result.get("data_time"),
            "is_stale": result.get("is_stale"),
            "is_trading_session": result.get("is_trading_session"),
            "quote_mode": result.get("quote_mode"),
            "quote_mode_label": result.get("quote_mode_label"),
            "fallback_used": result.get("fallback_used"),
            "source": result.get("source"),
            "errors": result.get("errors", []),
            "_cached": result.get("_cached"),
            "requested_symbols": result.get("requested_symbols"),
            "missing_symbols": result.get("missing_symbols"),
            "invalid_symbols": result.get("invalid_symbols"),
            "volume_unit": result.get("volume_unit"),
            "amount_unit": result.get("amount_unit"),
            "market_value_unit": result.get("market_value_unit"),
            "valuation_basis": {
                "pe_dynamic": "实时行情动态市盈率，不是 PE(TTM)",
                "pb": "实时行情市净率字段",
            },
        }, payload_policy="compacted", compacted=True, compaction_reason="quotes_item_window")

    if tool_name == "get_multi_stock_snapshot":
        compact_items: list[Dict[str, Any]] = []
        for item in _trim_list(result.get("items", []), 12):
            if not isinstance(item, dict):
                continue
            quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
            technical = (
                item.get("technical") if isinstance(item.get("technical"), dict) else {}
            )
            financial = (
                item.get("financial") if isinstance(item.get("financial"), dict) else None
            )
            compact_items.append({
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "quote": {
                    **_pick_fields(quote, [
                        "price", "change_pct", "turnover_rate", "pb_ratio",
                        "total_mv", "data_time", "is_stale", "source",
                    ]),
                    **({"pe_dynamic": quote.get("pe_ratio")} if quote.get("pe_ratio") is not None else {}),
                },
                "technical": _pick_fields(technical, [
                    "success", "data_time", "is_stale", "source", "indicators", "errors",
                ]),
                "financial": financial,
            })
        return _annotate_tool_payload(tool_name, {
            "success": result.get("success"),
            "partial": result.get("partial"),
            "items": compact_items,
            "resolved_entities": result.get("resolved_entities", []),
            "unresolved_entities": result.get("unresolved_entities", []),
            "total": result.get("total", len(compact_items)),
            "data_time": result.get("data_time"),
            "quote_basis": result.get("quote_basis"),
            "quote_is_intraday": result.get("quote_is_intraday"),
            "is_stale": result.get("is_stale"),
            "fallback_used": result.get("fallback_used"),
            "source": result.get("source"),
            "valuation_basis": {
                "pe_dynamic": "实时行情动态市盈率，不是 PE(TTM)",
                "pb_ratio": "实时行情市净率字段",
            },
            "errors": result.get("errors", []),
            "warnings": result.get("warnings", []),
            "decision_boundary": result.get("decision_boundary"),
        }, payload_policy="compacted", compacted=True, compaction_reason="multi_stock_decision_fields")

    if tool_name == "get_multi_stock_financials":
        # Every row is needed to prove collection coverage and to apply the
        # requested threshold. The tool itself already enforces a 12-row cap.
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="complete",
            compacted=False,
            source_scope="local_synchronized_financials",
        )

    if tool_name == "get_multi_stock_decision_evidence":
        # This tool already returns its own bounded professional evidence view:
        # all requested companies and all seven dimensions remain present, while
        # each upstream source is compacted inside the tool.  Mark that contract
        # explicitly instead of silently presenting it as a full raw payload.
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="professional_decision_evidence_view",
            source_scope="seven_dimension_multi_stock_evidence",
        )

    if tool_name == "evaluate_multi_stock_buy_criteria":
        # The strict decision payload is already bounded to eight companies and
        # every executed gate, stop reason, price level and position field is
        # required for deterministic all-pass validation. Never compact it.
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="complete",
            compacted=False,
            source_scope="strict_sequential_buy_decision",
        )

    if tool_name == "analyze_stock_catalysts":
        # Every returned event is already bounded and program-bound to one or
        # more retrieved evidence ids. Preserve those ids, dates and URLs for
        # the deterministic renderer; generic list trimming would break the
        # audit trail between an event and its source.
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="complete",
            compacted=False,
            source_scope="evidence_bound_company_catalysts",
        )

    if tool_name == "get_domain_stock_candidates":
        compact_domains = []
        for domain_result in result.get("domain_results") or []:
            if not isinstance(domain_result, dict):
                continue
            compact_items = []
            for item in domain_result.get("items") or []:
                if not isinstance(item, dict):
                    continue
                compact_items.append({
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "sector": item.get("sector"),
                    "boards": item.get("boards", []),
                    "matched_domains": item.get("matched_domains", []),
                    "lookup_themes": item.get("lookup_themes", []),
                    "evidence_level": item.get("evidence_level"),
                })
            compact_domains.append({
                "domain": domain_result.get("domain"),
                "lookup_themes": domain_result.get("lookup_themes", []),
                "mapping_type": domain_result.get("mapping_type"),
                "mapping_rationale": domain_result.get("mapping_rationale"),
                "unresolved_parts": domain_result.get("unresolved_parts", []),
                "mapping_basis": domain_result.get("mapping_basis"),
                "context_theme": domain_result.get("context_theme"),
                "context_filter_applied": domain_result.get("context_filter_applied"),
                "pre_context_candidate_count": domain_result.get("pre_context_candidate_count"),
                "success": domain_result.get("success"),
                "partial": domain_result.get("partial"),
                "coverage_complete": domain_result.get("coverage_complete"),
                "candidate_count": domain_result.get("candidate_count"),
                "items": compact_items,
                "matched_boards": domain_result.get("matched_boards", []),
                "context_boards": domain_result.get("context_boards", []),
                "warnings": domain_result.get("warnings", []),
                "errors": domain_result.get("errors", []),
            })
        compact_result = {
            "success": result.get("success"),
            "partial": result.get("partial"),
            "requested_domains": result.get("requested_domains", []),
            "domain_specs": result.get("domain_specs", []),
            "context_theme": result.get("context_theme"),
            "local_universe_count": result.get("local_universe_count"),
            "domain_results": compact_domains,
            "candidate_count": result.get("candidate_count"),
            "returned_count": result.get("returned_count"),
            "source_scope": result.get("source_scope"),
            "decision_boundary": result.get("decision_boundary"),
            "warnings": result.get("warnings", []),
            "errors": result.get("errors", []),
        }
        return _annotate_tool_payload(
            tool_name,
            compact_result,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="complete_multi_domain_candidate_indexes",
            source_scope="structured_board_constituents_x_local_stock_meta",
        )

    if tool_name == "get_theme_stock_candidates":
        # 保留全部公司/代码，但去掉每家公司重复的财务快照和冗长说明。完整候选
        # 索引是后续写作的硬约束，不能再用 generic list trimming 截成前几家公司。
        compact_items = []
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            sources = item.get("sources") if isinstance(item.get("sources"), list) else []
            source = next((value for value in sources if isinstance(value, dict)), {})
            compact_items.append({
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "sector": item.get("sector"),
                "boards": item.get("boards", []),
                "primary_theme_membership": item.get("primary_theme_membership"),
                "evidence_level": item.get("evidence_level"),
                "source": {
                    "name": source.get("name"),
                    "url": source.get("url"),
                    "date": source.get("date"),
                },
            })
        compact_result = {
            "success": result.get("success"),
            "partial": result.get("partial"),
            "theme": result.get("theme"),
            "local_universe_count": result.get("local_universe_count"),
            "raw_constituent_records": result.get("raw_constituent_records"),
            "candidate_count": result.get("candidate_count"),
            "returned_count": result.get("returned_count"),
            "omitted_count": result.get("omitted_count"),
            "coverage_complete": result.get("coverage_complete"),
            "items": compact_items,
            "matched_boards": result.get("matched_boards", []),
            "source_scope": result.get("source_scope"),
            "decision_boundary": result.get("decision_boundary"),
            "data_time": result.get("data_time"),
            "warnings": result.get("warnings", []),
            "errors": result.get("errors", []),
        }
        return _annotate_tool_payload(
            tool_name,
            compact_result,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="all_candidate_identity_index_without_repeated_financial_fields",
            source_scope="public_board_constituents_x_local_stock_meta",
        )

    if tool_name in {"get_kline", "get_history_data"}:
        return _annotate_tool_payload(
            tool_name,
            _compact_time_series(result, "data"),
            payload_policy="compacted",
            compacted=True,
            compaction_reason="time_series_recent_window",
        )

    if tool_name == "get_market_status":
        return _annotate_tool_payload(tool_name, _pick_fields(
            result,
            [
                "is_trading_time", "up_count", "down_count", "flat_count",
                "limit_up_count", "limit_down_count", "total_amount", "north_flow",
                "halt_count", "total_amount_unit", "turnover_scope", "breadth_scope",
                "breadth_source", "indices", "sh_index", "north_flow_available", "north_flow_note",
                "source", "errors", "warnings", "market_date", "data_time", "is_stale",
                "fallback_used", "success", "partial", "_cached", "_fetched_at",
            ],
        ), payload_policy="compacted", compacted=True, compaction_reason="market_status_key_fields")

    if tool_name == "get_sector_list":
        items = result.get("items", [])
        sorted_items = sorted(
            [item for item in items if isinstance(item, dict)],
            key=lambda item: item.get("change_pct") if item.get("change_pct") is not None else -999,
            reverse=True,
        )
        top = _trim_list(
            sorted_items,
            12,
            ["name", "code", "change_pct", "lead_stock", "lead_stock_change_pct", "up_count", "down_count", "net_flow"],
        )
        bottom = _trim_list(
            list(reversed(sorted_items)),
            12,
            ["name", "code", "change_pct", "lead_stock", "lead_stock_change_pct", "up_count", "down_count", "net_flow"],
        )
        return _annotate_tool_payload(tool_name, {
            "success": result.get("success"),
            "partial": result.get("partial"),
            "type": result.get("type"),
            "total": len(items),
            "top_movers": top,
            "bottom_movers": bottom,
            "data_time": result.get("data_time"),
            "is_stale": result.get("is_stale"),
            "fallback_used": result.get("fallback_used"),
            "source": result.get("source"),
            "errors": result.get("errors", []),
            "_cached": result.get("_cached"),
            "_fetched_at": result.get("_fetched_at"),
        }, payload_policy="compacted", compacted=True, compaction_reason="sector_top_bottom_window")

    if tool_name == "get_stock_info":
        return _annotate_tool_payload(tool_name, _pick_fields(
            result,
            [
                "symbol", "company_name", "company_name_en", "short_name", "former_names",
                "market", "market_code", "industry", "industry_eastmoney", "legal_representative",
                "registered_capital", "registered_capital_unit", "established_date", "listing_date",
                "official_website", "email", "phone", "registered_address", "office_address",
                "main_business", "business_scope", "company_profile", "included_indices",
                "capital_snapshot", "profile_available", "capital_snapshot_available", "sources", "source",
                "success", "errors", "warnings", "data_time", "is_stale", "fallback_used",
                "_cached", "cache_detail", "_fetched_at",
            ],
        ), payload_policy="compacted", compacted=True, compaction_reason="stock_info_key_fields")

    if tool_name in {"get_financials", "get_balance_sheet", "get_income_statement", "get_cashflow"}:
        series_key = "items"
        if tool_name == "get_balance_sheet":
            series_key = "balance_sheet"
        elif tool_name == "get_income_statement":
            series_key = "income_statement"
        elif tool_name == "get_cashflow":
            series_key = "cashflow"
        series = result.get(series_key, [])
        return _annotate_tool_payload(tool_name, {
            "symbol": result.get("symbol"),
            "requested_periods": result.get("requested_periods"),
            "periods": len(series) if isinstance(series, list) else result.get("periods"),
            "latest": series[-1] if isinstance(series, list) and series else {},
            "recent_periods": series[-6:] if isinstance(series, list) else [],
            "amount_unit": result.get("amount_unit"),
            "ratio_unit": result.get("ratio_unit"),
            "per_share_unit": result.get("per_share_unit"),
            "currency": result.get("currency"),
            "basis": result.get("basis"),
            "flow_basis": result.get("flow_basis"),
            "balance_basis": result.get("balance_basis"),
            "source": result.get("source"),
            "sources": result.get("sources"),
            "source_url": result.get("source_url"),
            "success": result.get("success"),
            "partial": result.get("partial"),
            "errors": result.get("errors"),
            "data_time": result.get("data_time"),
            "is_stale": result.get("is_stale"),
            "fallback_used": result.get("fallback_used"),
            "_cached": result.get("_cached"),
            "_fetched_at": result.get("_fetched_at"),
        }, payload_policy="compacted", compacted=True, compaction_reason="financial_recent_periods")

    if tool_name == "get_valuation_ratios":
        return _annotate_tool_payload(tool_name, _pick_fields(
            result,
            [
                "symbol", "name", "trade_date", "history_trade_date", "current_price", "pe_static", "pe_dynamic",
                "pe_ttm", "pb_mrq", "pb_annual", "ps_ttm", "pcf_ttm", "peg_trailing", "peg_forward",
                "peg_basis", "forward_pe", "forward_ps", "forward_pe_current_year", "forward_pe_next_year",
                "dividend_yield_ttm_pct", "cash_dividend_per_share_ttm", "dividend_count_ttm", "dividends_ttm",
                "pe_percentiles", "pe_history_stats", "industry_rank", "industry_benchmark",
                "price_overdraft_signal", "total_market_cap", "circulating_market_cap", "price_unit",
                "market_cap_unit", "ratio_unit", "percent_unit", "valuation_basis", "sources", "source_urls",
                "success", "partial", "errors", "data_time", "data_time_inferred", "is_stale", "fallback_used",
                "cache_detail", "_cached", "_fetched_at",
            ],
        ), payload_policy="compacted", compacted=True, compaction_reason="valuation_key_fields")

    if tool_name == "get_shareholder_structure":
        return _annotate_tool_payload(tool_name, {
            **_pick_fields(
                result,
                [
                    "symbol", "holder_count", "top_holders_report_date", "top_holder_item_count",
                    "institution_holding", "institution_holding_ratio", "institution_holding_ratio_basis",
                    "actual_controller", "holder_change_item_count", "units", "source", "sources",
                    "source_urls", "source_scope", "section_availability", "success", "partial", "errors",
                    "warnings", "data_time", "holder_report_date", "expected_latest_report_date",
                    "freshness_unknown", "is_stale", "fallback_used", "cache_detail", "_cached", "_fetched_at",
                ],
            ),
            "top_holders": _trim_list(
                result.get("top_holders"),
                10,
                [
                    "rank", "holder_name", "holder_type", "share_type", "holding_shares",
                    "holding_ratio_pct", "change_direction", "change_shares", "change_ratio_pct",
                ],
            ),
            "holder_changes": _trim_list(
                result.get("holder_changes"),
                8,
                [
                    "announcement_date", "holder_name", "change_direction", "change_shares",
                    "signed_change_shares", "average_price_yuan", "remaining_shares",
                    "change_period", "transaction_method",
                ],
            ),
        }, payload_policy="compacted", compacted=True, compaction_reason="shareholder_top_lists")

    if tool_name in {"search_news", "get_announcements", "get_risk_events", "get_research_report", "get_social_sentiment"}:
        item_fields_map = {
            "search_news": [
                "title", "published", "source", "url", "source_type", "rss_route", "relevance",
                "relevance_score", "event_type", "event_label", "importance", "tags", "classification_method", "summary",
            ],
            "get_announcements": [
                "title", "publish_date", "notice_type", "source_notice_type", "url",
                "importance", "tags", "classification_method",
            ],
            "get_risk_events": [
                "title", "date", "source", "source_type", "url", "severity", "status",
                "risk_category", "risk_label", "risk_summary", "tags", "confidence",
                "evidence_basis", "requires_fulltext_verification", "classification_method",
            ],
            "get_research_report": [
                "title", "org", "rating", "publish_date", "industry", "url", "summary",
                "profit_forecasts", "monthly_report_count", "source", "source_type",
            ],
            "get_social_sentiment": [
                "title", "publish_time", "source", "author", "post_kind", "url", "label",
                "sentiment_score", "positive_hits", "negative_hits", "read_count", "reply_count",
                "classification_method", "page_number",
            ],
        }
        compact = _pick_fields(
            result,
            [
                "symbol", "name", "days", "limit", "type", "source", "sources", "sentiment_score", "overall_score",
                "positive_count", "negative_count", "neutral_count", "total_discussion", "user_post_count",
                "syndicated_info_count", "xueqiu_hot_count", "returned_item_order", "sentiment_sample_scope", "engagement_weighted_score",
                "sentiment_confidence",
                "total_read", "total_reply", "diagnose_score", "eastmoney_diagnose_score", "diagnose_score_semantics", "top_keywords",
                "analysis", "source_chain", "rss_routes", "item_count", "excluded_weak_mention_count",
                "success", "partial", "errors", "warnings", "data_time", "retrieved_at", "freshness_unknown", "is_stale",
                "has_announcements", "coverage_start", "coverage_end", "source_scope", "fallback_attempted",
                "has_risk_events",
                "has_reports",
                "max_pages", "coverage_complete",
                "fallback_used", "fallback_recommended", "fallback_query", "cache_detail", "_cached", "_fetched_at",
            ],
        )
        if "daily_trend" in result:
            compact["daily_trend"] = _trim_list(result.get("daily_trend"), LLM_ARRAY_LIMIT)
        if "score_trend" in result:
            compact["score_trend"] = _trim_list(result.get("score_trend"), LLM_ARRAY_LIMIT)
        compact["items"] = _trim_list(result.get("items"), LLM_ARRAY_LIMIT, item_fields_map[tool_name])
        compact["item_count"] = len(result.get("items") or [])
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="news_family_item_window",
        )

    if tool_name == "search_financial_news":
        compact = _pick_fields(
            result,
            [
                "query", "topic", "days", "success", "partial", "item_count", "rss_routes", "rss_catalog_count",
                "attempted_route_count", "successful_route_count", "web_fallback", "source", "errors", "warnings",
                "data_time", "retrieved_at", "is_stale", "freshness_unknown", "fallback_attempted", "fallback_used",
                "fallback_recommended",
            ],
        )
        compact["items"] = _trim_list(
            result.get("items"),
            6,
            [
                "id", "title", "summary", "content_text", "link", "published", "author", "tags",
                "image", "attachments", "source", "source_type", "rss_route", "rss_params",
                "content_fallback",
            ],
            {"summary": 800, "content_text": 1800},
        )
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="semantic_rss_item_and_text_window",
        )

    if tool_name == "list_financial_sources":
        compact = _pick_fields(result, [
            "success", "partial", "catalog_count", "matched_count", "item_count", "returned_count", "has_more",
            "query_scope", "query_note", "applied_filters",
            "data_time", "is_stale", "freshness_unknown", "errors", "warnings",
        ])
        compact["items"] = _trim_list(result.get("items"), 50, [
            "route_path", "name", "namespace", "namespace_name", "description",
            "example", "params", "capabilities", "categories", "features", "maintainers",
            "requires_configuration",
        ], {"description": 600})
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="financial_source_catalog_window",
        )

    if tool_name == "inspect_financial_source":
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="complete",
            compacted=False,
        )

    if tool_name in {"read_financial_feed", "transform_webpage_to_feed"}:
        compact = _pick_fields(result, [
            "success", "partial", "route_path", "namespace", "params", "options",
            "feed_title", "feed_link", "item_count", "data_time", "is_stale",
            "freshness_unknown", "errors", "warnings", "_cached", "_fetched_at",
        ])
        compact["items"] = _trim_list(
            result.get("items"),
            20,
            [
                "id", "title", "link", "summary", "published", "author", "tags",
                "image", "content_html", "attachments",
            ],
            {"summary": 1200, "content_html": 6000},
        )
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="financial_feed_item_window",
        )

    if tool_name in {"read_financial_article", "export_financial_feed"}:
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="complete",
            compacted=False,
        )

    if tool_name == "get_stock_capital_flow":
        return _annotate_tool_payload(tool_name, {
            **_pick_fields(result, [
                "symbol", "market", "days", "latest", "summary", "item_count", "available_history_count",
                "amount_unit", "ratio_unit", "price_unit", "main_flow_definition", "interpretation_warning",
                "source", "source_url", "source_transport", "success", "errors", "warnings", "data_time",
                "source_data_time_granularity", "is_stale", "fallback_used", "_cached", "_fetched_at",
            ]),
            "recent": _trim_list(result.get("items"), 10, [
                "date", "close", "pct_chg", "main_net_inflow", "main_net_inflow_pct",
                "super_large_net_inflow", "super_large_net_inflow_pct", "large_net_inflow",
                "large_net_inflow_pct", "medium_net_inflow", "medium_net_inflow_pct",
                "small_net_inflow", "small_net_inflow_pct", "data_time", "bar_complete",
            ]),
        }, payload_policy="compacted", compacted=True, compaction_reason="capital_flow_recent_window")

    if tool_name == "get_business_segments":
        return _annotate_tool_payload(tool_name, {
            **_pick_fields(result, [
                "symbol", "category", "requested_periods", "periods", "available_categories", "item_count",
                "summaries", "amount_unit", "ratio_unit", "currency", "flow_basis", "source", "source_url",
                "success", "errors", "data_time", "is_stale", "fallback_used", "_cached", "_fetched_at",
            ]),
            "items": _trim_list(result.get("items"), 24, [
                "report_date", "flow_basis", "category", "segment_name", "revenue", "revenue_share_pct",
                "cost", "cost_share_pct", "gross_profit", "gross_profit_share_pct", "gross_margin_pct",
            ]),
        }, payload_policy="compacted", compacted=True, compaction_reason="business_segment_window")

    if tool_name == "get_consensus_estimates":
        return _annotate_tool_payload(tool_name, {
            **_pick_fields(result, [
                "symbol", "metric", "estimates", "institution_item_count", "latest_institution_report_date",
                "actuals", "financial_forecasts", "coverage_available", "coverage_status",
                "source_query_complete", "coverage_count_latest", "eps_unit",
                "net_profit_unit", "amount_unit_note", "forecast_warning", "source", "source_url", "success",
                "partial", "errors", "warnings", "data_time", "freshness_unknown", "is_stale", "fallback_used",
                "cache_detail", "_cached", "_fetched_at",
            ]),
            "institutions": _trim_list(result.get("institutions"), 10, [
                "institution", "analysts", "report_date", "forecasts",
            ]),
        }, payload_policy="compacted", compacted=True, compaction_reason="consensus_estimate_window")

    if tool_name == "get_peer_comparison":
        dimensions = {}
        for name, bucket in (result.get("dimensions") or {}).items():
            if not isinstance(bucket, dict):
                continue
            dimensions[name] = {
                **_pick_fields(bucket, [
                    "label", "report_date", "report_period", "sample_size", "target", "industry_median",
                    "industry_average", "target_rank", "ranking", "top_peer_item_count", "source_scope", "success",
                ]),
                "top_peers": _trim_list(bucket.get("top_peers"), 5),
            }
        return _annotate_tool_payload(tool_name, {
            **_pick_fields(result, [
                "symbol", "dimension", "amount_unit", "ratio_unit", "source", "source_url", "success", "partial",
                "errors", "data_time", "freshness_unknown", "is_stale", "fallback_used", "cache_detail", "_cached",
                "_fetched_at",
            ]),
            "dimensions": dimensions,
        }, payload_policy="compacted", compacted=True, compaction_reason="peer_dimension_evidence_window")

    if tool_name == "get_technical_indicators":
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="full",
            compacted=False,
        )

    if tool_name == "websearch":
        output = str(result.get("output") or "")
        llm_output = output[:12000]
        output_compacted = len(output) > 12000
        if output_compacted:
            llm_output = llm_output.rstrip() + "…"
        results = result.get("results") if isinstance(result.get("results"), list) else []
        compact = {
            **_pick_fields(result, ["query", "resolved_query", "success", "result_count", "provider", "attempts", "retrieved_at", "data_time", "fallback_used", "is_stale", "freshness_unknown", "latest_published_date", "content_requested", "content_result_count", "_truncated", "errors", "warnings"]),
            "results": _trim_list(results, 12, [
                "title", "url", "snippet", "content_text", "content_characters",
                "source", "source_name", "published_date", "result_type",
                "search_provider", "crawl_provider",
            ]),
        }
        if output:
            compact["output"] = llm_output
            compact["output_characters"] = len(output)
        was_compacted = len(results) > 12 or output_compacted
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted" if was_compacted else "full",
            compacted=was_compacted,
            compaction_reason="web_search_result_or_output_window" if was_compacted else None,
        )

    if tool_name == "webfetch":
        content = str(result.get("content") or "")
        llm_content = content[:12000]
        if len(content) > 12000:
            llm_content = llm_content.rstrip() + "…"
        compact = _pick_fields(result, [
            "url", "final_url", "format", "content_type", "title", "success", "provider", "attempts",
            "data_time", "content_time", "fallback_used", "is_stale", "freshness_unknown",
            "extraction_method", "_truncated", "errors", "warnings",
        ])
        compact["content"] = llm_content
        compact["content_characters"] = len(content)
        attachments = result.get("attachments") if isinstance(result.get("attachments"), list) else []
        if attachments:
            compact["attachments"] = _trim_list(attachments, 4, ["type", "mime"])
            compact["attachment_count"] = len(attachments)
        was_compacted = len(content) > 12000 or bool(attachments)
        reason = "web_content_character_cap" if len(content) > 12000 else (
            "web_attachment_binary_omitted" if attachments else None
        )
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted" if was_compacted else "full",
            compacted=was_compacted,
            compaction_reason=reason,
        )

    if tool_name == "get_index_data":
        compact = _pick_fields(
            result,
            [
                "index_code", "index_name", "days", "latest", "history_count", "units",
                "source", "source_chain", "success", "partial", "errors", "warnings",
                "data_time", "retrieved_at", "is_stale", "freshness_unknown",
                "fallback_used", "fallback_recommended", "_cached", "_fetched_at",
            ],
        )
        compact["history"] = _trim_list(result.get("history"), 12)
        return _annotate_tool_payload(tool_name, compact, payload_policy="compacted", compacted=True, compaction_reason="index_history_window")

    if tool_name == "get_bond_yield":
        compact = _pick_fields(
            result,
            [
                "country", "country_name", "term", "term_label", "latest", "latest_yield",
                "spread_10y_minus_2y", "spread", "spread_date", "history_count", "units",
                "source", "success", "partial", "errors", "warnings", "data_time",
                "retrieved_at", "is_stale", "freshness_unknown", "fallback_used",
                "fallback_recommended", "_cached", "_fetched_at",
            ],
        )
        compact["history"] = _trim_list(result.get("history"), 12)
        return _annotate_tool_payload(tool_name, compact, payload_policy="compacted", compacted=True, compaction_reason="bond_history_window")

    if tool_name == "get_macro_indicator":
        compact = _pick_fields(
            result,
            [
                "indicator", "indicator_name", "frequency", "unit", "threshold", "latest",
                "trend", "history_count", "expected_latest_period_end", "source", "success",
                "partial", "errors", "warnings", "data_time", "retrieved_at", "is_stale",
                "freshness_unknown", "fallback_used", "fallback_recommended", "_cached", "_fetched_at",
            ],
        )
        compact["history"] = _trim_list(result.get("history"), 12)
        return _annotate_tool_payload(tool_name, compact, payload_policy="compacted", compacted=True, compaction_reason="macro_history_window")

    if tool_name == "get_sector_flow":
        return _annotate_tool_payload(tool_name, {
            "type": result.get("type"),
            "period": result.get("period"),
            "period_label": result.get("period_label"),
            "top_n": result.get("top_n"),
            "sector_count": result.get("sector_count"),
            "inflow_top": _trim_list(
                result.get("inflow_top"),
                10,
                [
                    "sector_code", "name", "pct_chg", "main_net_inflow", "main_net_inflow_pct",
                    "super_large_net_inflow", "super_large_net_inflow_pct", "large_net_inflow",
                    "large_net_inflow_pct", "leading_stock", "leading_stock_code", "main_flow_rank",
                ],
            ),
            "outflow_top": _trim_list(
                result.get("outflow_top"),
                10,
                [
                    "sector_code", "name", "pct_chg", "main_net_inflow", "main_net_inflow_pct",
                    "super_large_net_inflow", "super_large_net_inflow_pct", "large_net_inflow",
                    "large_net_inflow_pct", "leading_stock", "leading_stock_code", "main_flow_rank",
                ],
            ),
            "amount_unit": result.get("amount_unit"),
            "ratio_unit": result.get("ratio_unit"),
            "price_unit": result.get("price_unit"),
            "main_flow_definition": result.get("main_flow_definition"),
            "source": result.get("source"),
            "source_url": result.get("source_url"),
            "success": result.get("success"),
            "errors": result.get("errors"),
            "warnings": result.get("warnings"),
            "data_time": result.get("data_time"),
            "is_stale": result.get("is_stale"),
            "fallback_used": result.get("fallback_used"),
            "_cached": result.get("_cached"),
            "_fetched_at": result.get("_fetched_at"),
        }, payload_policy="compacted", compacted=True, compaction_reason="sector_flow_top_lists")

    if tool_name == "get_market_breadth":
        return _annotate_tool_payload(tool_name, _pick_fields(
            result,
            [
                "up_count", "down_count", "flat_count", "advance_decline_ratio",
                "advance_rate_pct", "decline_rate_pct", "market_activity_pct", "halt_count",
                "consecutive_up_days", "consecutive_down_days", "limit_up_count", "limit_down_count",
                "real_limit_up_count", "real_limit_down_count", "broken_board_count", "broken_board_rate",
                "total_amount", "total_amount_unit", "turnover_scope", "breadth_scope", "breadth_source",
                "source", "errors", "warnings", "market_date", "data_time", "is_stale",
                "fallback_used", "success", "partial", "_cached", "_fetched_at",
            ],
        ), payload_policy="compacted", compacted=True, compaction_reason="market_breadth_key_fields")

    if tool_name == "search_research_library":
        compact = _pick_fields(
            result,
            [
                "query", "requested_category", "research_category", "days", "item_count",
                "source_coverage", "source", "success", "partial", "data_time", "retrieved_at",
                "is_stale", "freshness_unknown", "fallback_attempted", "fallback_used",
                "fallback_recommended", "errors", "warnings",
            ],
        )
        compact["items"] = _trim_list(
            result.get("items"),
            6,
            [
                "id", "title", "summary", "link", "published", "author", "tags", "image", "attachments",
                "source", "source_type", "research_category", "rating", "industry", "profit_forecasts",
                "rss_route", "rss_params", "content_text",
            ],
            {"summary": 800, "content_text": 1800},
        )
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="rss_intelligence_item_and_text_window",
        )

    if tool_name == "get_regulatory_updates":
        compact = _pick_fields(
            result,
            [
                "keyword", "resolved_code", "resolved_name", "event_type", "market", "days",
                "project_filters", "item_count", "has_updates", "rss_routes", "source",
                "source_scope", "success", "partial", "data_time", "retrieved_at", "is_stale",
                "freshness_unknown", "fallback_attempted", "fallback_used", "fallback_recommended",
                "fallback_channel", "errors", "warnings",
            ],
        )
        compact["items"] = _trim_list(
            result.get("items"), LLM_ARRAY_LIMIT,
            [
                "title", "published", "summary", "link", "source", "exchange", "event_type",
                "project_status", "company_code", "official", "source_type", "content_text",
            ],
        )
        return _annotate_tool_payload(
            tool_name, compact, payload_policy="compacted", compacted=True,
            compaction_reason="regulatory_item_window",
        )

    if tool_name == "get_monetary_policy_operations":
        compact = _pick_fields(
            result,
            [
                "days", "instrument_filter", "item_count", "total_operation_amount_yi",
                "available_item_count", "result_truncated",
                "net_liquidity_injection_yi", "net_liquidity_note", "rss_route",
                "coverage_start", "coverage_end", "coverage_complete", "source", "success",
                "partial", "data_time", "retrieved_at", "is_stale", "freshness_unknown",
                "fallback_attempted", "fallback_used", "fallback_recommended", "errors", "warnings",
            ],
        )
        compact["operations"] = _trim_list(
            result.get("operations"),
            50,
            [
                "title", "published", "link", "bulletin_year", "bulletin_number",
                "instrument_code", "instrument", "term_days", "term_months", "amount_yi",
                "rate_pct", "operation_legs", "tender_method", "fully_satisfied", "official",
                "source",
            ],
        )
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="monetary_operations_window",
        )

    return _annotate_tool_payload(tool_name, result, payload_policy="full", compacted=False)


def _resolve_search_subject(raw_symbol: Any) -> tuple[str | None, str | None]:
    symbol = str(raw_symbol or "").strip()
    if not symbol:
        return None, None
    try:
        from src.services.name_to_code_resolver import resolve_name_to_code
        from src.data.stock_mapping import STOCK_NAME_MAP

        code = resolve_name_to_code(symbol) or symbol
        name = STOCK_NAME_MAP.get(code, symbol)
        return code, name
    except Exception:
        return symbol, symbol


def _build_search_fallback_payload(fallback_type: str, code: str, name: str) -> Dict[str, Any] | None:
    try:
        from src.tools.websearch import websearch

        suffixes = {
            "price": "今日股价 最新行情 涨跌",
            "financials": "最新财报 财务指标 营收 净利润 现金流",
            "business_segments": "最新年报 主营业务 主营构成 收入占比",
            "valuation": "最新 PE PB 估值 市盈率 市净率",
            "consensus": "最新 券商一致预期 EPS 净利润预测",
            "peers": "行业同行比较 估值 成长性 ROE",
            "capital_flow": "今日 个股资金流 主力净流入",
            "shareholders": "最新 股东结构 十大股东 股东变动",
            "news": "最新消息 公告 新闻",
        }
        suffix = suffixes.get(fallback_type, suffixes["news"])
        payload = websearch(
            query=f"{name} {code} {suffix}",
            num_results=5,
            livecrawl="fallback",
            search_type="fast",
            context_max_characters=8000,
        )
        payload["type"] = fallback_type
        return payload
    except Exception as exc:
        logger.warning("[Agent] Search fallback failed: %s", exc)
        return {
            "type": fallback_type,
            "success": False,
            "error_message": str(exc),
            "results": [],
        }


def _maybe_attach_search_fallback(tool_name: str, args: Dict[str, Any], result: Any) -> Any:
    from api.v1.endpoints.agent.health import _assess_tool_data_health

    health = _assess_tool_data_health(tool_name, result)
    if not health.get("should_fallback"):
        return result

    if not isinstance(result, dict):
        return result

    # Only structured, stock-scoped tools use this generic approximation
    # layer. News/research/regulatory/macro tools own their domain-specific
    # fallback logic and must not be decorated with an unrelated symbol search
    # merely because their freshness metadata says ``is_stale``.
    fallback_types = {
        "get_realtime_quotes": "price",
        "get_kline": "price",
        "get_history_data": "price",
        "get_technical_indicators": "price",
        "get_financials": "financials",
        "get_balance_sheet": "financials",
        "get_income_statement": "financials",
        "get_cashflow": "financials",
        "get_business_segments": "business_segments",
        "get_valuation_ratios": "valuation",
        "get_consensus_estimates": "consensus",
        "get_peer_comparison": "peers",
        "get_stock_capital_flow": "capital_flow",
        "get_shareholder_structure": "shareholders",
    }
    fallback_type = fallback_types.get(tool_name)
    if fallback_type is None:
        return result

    symbol_arg = args.get("symbol") or args.get("symbols")
    if isinstance(symbol_arg, str) and "," in symbol_arg:
        symbol_arg = symbol_arg.split(",", 1)[0]
    code, name = _resolve_search_subject(symbol_arg)
    if not code or not name:
        enriched = dict(result)
        enriched["fallback_status"] = {"used": False, "reason": health.get("reason"), "message": "无法确定搜索对象"}
        return enriched

    fallback_payload = _build_search_fallback_payload(fallback_type, code, name)
    enriched = dict(result)
    enriched["fallback_status"] = {
        "used": True,
        "reason": health.get("reason"),
        "symbol": code,
        "name": name,
    }
    if health.get("latest_date"):
        enriched["fallback_status"]["latest_date"] = health["latest_date"]
    enriched["search_fallback"] = fallback_payload
    return enriched
