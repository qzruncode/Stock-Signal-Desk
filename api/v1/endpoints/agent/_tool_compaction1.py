"""Tool-result compaction handlers, group 1."""

from __future__ import annotations

from api.v1.endpoints.agent.tools import (
    json,
    logging,
    datetime,
    Any,
    Dict,
    List,
    logger,
    LLM_ARRAY_LIMIT,
    LLM_SERIES_LIMIT,
    _format_result,
    _pick_fields,
    _trim_list,
    _annotate_tool_payload,
    _normalize_stock_info,
    _compact_time_series,
 )

__all__ = ['_compact_tool_group1']


def _compact_tool_group1(tool_name: str, result: dict[str, object]) -> object:
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
            "name",
            "price",
            "high",
            "low",
            "volume",
            "amount",
            "turnover_rate",
            "total_mv",
            "circ_mv",
            "source",
            "trade_time",
            "data_time",
            "is_stale",
            "fallback_used",
            "volume_unit",
            "amount_unit",
            "market_value_unit",
            "quote_mode",
            "quote_mode_label",
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
        return _annotate_tool_payload(
            tool_name,
            {
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
            },
            payload_policy="compacted",
            compacted=True,
            compaction_reason="quotes_item_window",
        )

    if tool_name == "get_multi_stock_snapshot":
        compact_items: list[Dict[str, Any]] = []
        for item in _trim_list(result.get("items", []), 12):
            if not isinstance(item, dict):
                continue
            quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
            technical = item.get("technical") if isinstance(item.get("technical"), dict) else {}
            financial = item.get("financial") if isinstance(item.get("financial"), dict) else None
            compact_items.append(
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "quote": {
                        **_pick_fields(
                            quote,
                            [
                                "price",
                                "change_pct",
                                "turnover_rate",
                                "pb_ratio",
                                "total_mv",
                                "data_time",
                                "is_stale",
                                "source",
                            ],
                        ),
                        **({"pe_dynamic": quote.get("pe_ratio")} if quote.get("pe_ratio") is not None else {}),
                    },
                    "technical": _pick_fields(
                        technical,
                        [
                            "success",
                            "data_time",
                            "is_stale",
                            "source",
                            "indicators",
                            "errors",
                        ],
                    ),
                    "financial": financial,
                }
            )
        return _annotate_tool_payload(
            tool_name,
            {
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
            },
            payload_policy="compacted",
            compacted=True,
            compaction_reason="multi_stock_decision_fields",
        )

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
        # This tool already returns its own bounded professional evidence view.
        # Each upstream source is compacted inside the tool while every
        # requested company remains present.
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="professional_decision_evidence_view",
            source_scope="professional_multi_stock_evidence",
        )

    if tool_name == "evaluate_multi_stock_buy_criteria":
        # The professional decision payload contains the complete ordered
        # eight-dimension Boolean-gate result, counter-evidence and source links.
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="complete",
            compacted=False,
            source_scope="professional_eight_dimension_boolean_gate",
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
                compact_items.append(
                    {
                        "symbol": item.get("symbol"),
                        "name": item.get("name"),
                        "sector": item.get("sector"),
                        "boards": item.get("boards", []),
                        "matched_domains": item.get("matched_domains", []),
                        "lookup_themes": item.get("lookup_themes", []),
                        "evidence_level": item.get("evidence_level"),
                    }
                )
            compact_domains.append(
                {
                    "domain": domain_result.get("domain"),
                    "lookup_themes": domain_result.get("lookup_themes", []),
                    "mapping_type": domain_result.get("mapping_type"),
                    "mapping_rationale": domain_result.get("mapping_rationale"),
                    "unresolved_parts": domain_result.get("unresolved_parts", []),
                    "mapping_basis": domain_result.get("mapping_basis"),
                    "success": domain_result.get("success"),
                    "partial": domain_result.get("partial"),
                    "coverage_complete": domain_result.get("coverage_complete"),
                    "candidate_count": domain_result.get("candidate_count"),
                    "items": compact_items,
                    "matched_boards": domain_result.get("matched_boards", []),
                    "warnings": domain_result.get("warnings", []),
                    "errors": domain_result.get("errors", []),
                }
            )
        compact_result = {
            "success": result.get("success"),
            "partial": result.get("partial"),
            "requested_domains": result.get("requested_domains", []),
            "domain_specs": result.get("domain_specs", []),
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

    if tool_name in {"get_kline", "get_history_data"}:
        return _annotate_tool_payload(
            tool_name,
            _compact_time_series(result, "data"),
            payload_policy="compacted",
            compacted=True,
            compaction_reason="time_series_recent_window",
        )

    if tool_name == "get_market_status":
        return _annotate_tool_payload(
            tool_name,
            _pick_fields(
                result,
                [
                    "is_trading_time",
                    "up_count",
                    "down_count",
                    "flat_count",
                    "limit_up_count",
                    "limit_down_count",
                    "total_amount",
                    "north_flow",
                    "halt_count",
                    "total_amount_unit",
                    "turnover_scope",
                    "breadth_scope",
                    "breadth_source",
                    "indices",
                    "sh_index",
                    "north_flow_available",
                    "north_flow_note",
                    "source",
                    "errors",
                    "warnings",
                    "market_date",
                    "data_time",
                    "is_stale",
                    "fallback_used",
                    "success",
                    "partial",
                    "_cached",
                    "_fetched_at",
                ],
            ),
            payload_policy="compacted",
            compacted=True,
            compaction_reason="market_status_key_fields",
        )

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
        return _annotate_tool_payload(
            tool_name,
            {
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
            },
            payload_policy="compacted",
            compacted=True,
            compaction_reason="sector_top_bottom_window",
        )

    if tool_name == "get_stock_info":
        return _annotate_tool_payload(
            tool_name,
            _pick_fields(
                result,
                [
                    "symbol",
                    "company_name",
                    "company_name_en",
                    "short_name",
                    "former_names",
                    "market",
                    "market_code",
                    "industry",
                    "industry_eastmoney",
                    "legal_representative",
                    "registered_capital",
                    "registered_capital_unit",
                    "established_date",
                    "listing_date",
                    "official_website",
                    "email",
                    "phone",
                    "registered_address",
                    "office_address",
                    "main_business",
                    "business_scope",
                    "company_profile",
                    "included_indices",
                    "capital_snapshot",
                    "profile_available",
                    "capital_snapshot_available",
                    "sources",
                    "source",
                    "success",
                    "errors",
                    "warnings",
                    "data_time",
                    "is_stale",
                    "fallback_used",
                    "_cached",
                    "cache_detail",
                    "_fetched_at",
                ],
            ),
            payload_policy="compacted",
            compacted=True,
            compaction_reason="stock_info_key_fields",
        )

    return result
