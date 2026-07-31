"""Tool-result compaction handlers, group 4."""

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

__all__ = ['_compact_tool_group4']


def _compact_tool_group4(tool_name: str, result: dict[str, object]) -> object:
    if tool_name == "get_bond_yield":
        compact = _pick_fields(
            result,
            [
                "country",
                "country_name",
                "term",
                "term_label",
                "latest",
                "latest_yield",
                "spread_10y_minus_2y",
                "spread",
                "spread_date",
                "history_count",
                "units",
                "source",
                "success",
                "partial",
                "errors",
                "warnings",
                "data_time",
                "retrieved_at",
                "is_stale",
                "freshness_unknown",
                "fallback_used",
                "fallback_recommended",
                "_cached",
                "_fetched_at",
            ],
        )
        compact["history"] = _trim_list(result.get("history"), 12)
        return _annotate_tool_payload(
            tool_name, compact, payload_policy="compacted", compacted=True, compaction_reason="bond_history_window"
        )

    if tool_name == "get_macro_indicator":
        compact = _pick_fields(
            result,
            [
                "indicator",
                "indicator_name",
                "frequency",
                "unit",
                "latest",
                "trend",
                "history_count",
                "expected_latest_period_end",
                "source",
                "success",
                "partial",
                "errors",
                "warnings",
                "data_time",
                "retrieved_at",
                "is_stale",
                "freshness_unknown",
                "fallback_used",
                "fallback_recommended",
                "_cached",
                "_fetched_at",
            ],
        )
        compact["history"] = _trim_list(result.get("history"), 12)
        return _annotate_tool_payload(
            tool_name, compact, payload_policy="compacted", compacted=True, compaction_reason="macro_history_window"
        )

    if tool_name == "get_sector_flow":
        return _annotate_tool_payload(
            tool_name,
            {
                "type": result.get("type"),
                "period": result.get("period"),
                "period_label": result.get("period_label"),
                "top_n": result.get("top_n"),
                "sector_count": result.get("sector_count"),
                "inflow_top": _trim_list(
                    result.get("inflow_top"),
                    10,
                    [
                        "sector_code",
                        "name",
                        "pct_chg",
                        "main_net_inflow",
                        "main_net_inflow_pct",
                        "super_large_net_inflow",
                        "super_large_net_inflow_pct",
                        "large_net_inflow",
                        "large_net_inflow_pct",
                        "leading_stock",
                        "leading_stock_code",
                        "main_flow_rank",
                    ],
                ),
                "outflow_top": _trim_list(
                    result.get("outflow_top"),
                    10,
                    [
                        "sector_code",
                        "name",
                        "pct_chg",
                        "main_net_inflow",
                        "main_net_inflow_pct",
                        "super_large_net_inflow",
                        "super_large_net_inflow_pct",
                        "large_net_inflow",
                        "large_net_inflow_pct",
                        "leading_stock",
                        "leading_stock_code",
                        "main_flow_rank",
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
            },
            payload_policy="compacted",
            compacted=True,
            compaction_reason="sector_flow_top_lists",
        )

    if tool_name == "get_market_breadth":
        return _annotate_tool_payload(
            tool_name,
            _pick_fields(
                result,
                [
                    "up_count",
                    "down_count",
                    "flat_count",
                    "advance_decline_ratio",
                    "advance_rate_pct",
                    "decline_rate_pct",
                    "market_activity_pct",
                    "halt_count",
                    "consecutive_up_days",
                    "consecutive_down_days",
                    "limit_up_count",
                    "limit_down_count",
                    "real_limit_up_count",
                    "real_limit_down_count",
                    "broken_board_count",
                    "broken_board_rate",
                    "total_amount",
                    "total_amount_unit",
                    "turnover_scope",
                    "breadth_scope",
                    "breadth_source",
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
            compaction_reason="market_breadth_key_fields",
        )

    if tool_name == "search_research_library":
        compact = _pick_fields(
            result,
            [
                "query",
                "requested_category",
                "research_category",
                "days",
                "item_count",
                "source_coverage",
                "source",
                "success",
                "partial",
                "data_time",
                "retrieved_at",
                "is_stale",
                "freshness_unknown",
                "fallback_attempted",
                "fallback_used",
                "fallback_recommended",
                "errors",
                "warnings",
            ],
        )
        compact["items"] = _trim_list(
            result.get("items"),
            6,
            [
                "id",
                "title",
                "summary",
                "link",
                "published",
                "author",
                "tags",
                "image",
                "attachments",
                "source",
                "source_type",
                "research_category",
                "rating",
                "industry",
                "profit_forecasts",
                "rss_route",
                "rss_params",
                "content_text",
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
                "keyword",
                "resolved_code",
                "resolved_name",
                "event_type",
                "market",
                "days",
                "project_filters",
                "item_count",
                "has_updates",
                "rss_routes",
                "source",
                "source_scope",
                "success",
                "partial",
                "data_time",
                "retrieved_at",
                "is_stale",
                "freshness_unknown",
                "fallback_attempted",
                "fallback_used",
                "fallback_recommended",
                "fallback_channel",
                "errors",
                "warnings",
            ],
        )
        compact["items"] = _trim_list(
            result.get("items"),
            LLM_ARRAY_LIMIT,
            [
                "title",
                "published",
                "summary",
                "link",
                "source",
                "exchange",
                "event_type",
                "project_status",
                "company_code",
                "official",
                "source_type",
                "content_text",
            ],
        )
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="regulatory_item_window",
        )

    if tool_name == "get_monetary_policy_operations":
        compact = _pick_fields(
            result,
            [
                "days",
                "instrument_filter",
                "item_count",
                "total_operation_amount_yi",
                "available_item_count",
                "result_truncated",
                "net_liquidity_injection_yi",
                "net_liquidity_note",
                "rss_route",
                "coverage_start",
                "coverage_end",
                "coverage_complete",
                "source",
                "success",
                "partial",
                "data_time",
                "retrieved_at",
                "is_stale",
                "freshness_unknown",
                "fallback_attempted",
                "fallback_used",
                "fallback_recommended",
                "errors",
                "warnings",
            ],
        )
        compact["operations"] = _trim_list(
            result.get("operations"),
            50,
            [
                "title",
                "published",
                "link",
                "bulletin_year",
                "bulletin_number",
                "instrument_code",
                "instrument",
                "term_days",
                "term_months",
                "amount_yi",
                "rate_pct",
                "operation_legs",
                "tender_method",
                "fully_satisfied",
                "official",
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

    return result
