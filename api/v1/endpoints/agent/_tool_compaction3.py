"""Tool-result compaction handlers, group 3."""

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

__all__ = ['_compact_tool_group3']


def _compact_tool_group3(tool_name: str, result: dict[str, object]) -> object:
    if tool_name in {"read_financial_feed", "transform_webpage_to_feed"}:
        compact = _pick_fields(
            result,
            [
                "success",
                "partial",
                "route_path",
                "namespace",
                "params",
                "options",
                "feed_title",
                "feed_link",
                "item_count",
                "data_time",
                "is_stale",
                "freshness_unknown",
                "errors",
                "warnings",
                "_cached",
                "_fetched_at",
            ],
        )
        compact["items"] = _trim_list(
            result.get("items"),
            20,
            [
                "id",
                "title",
                "link",
                "summary",
                "published",
                "author",
                "tags",
                "image",
                "content_html",
                "attachments",
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
        return _annotate_tool_payload(
            tool_name,
            {
                **_pick_fields(
                    result,
                    [
                        "symbol",
                        "market",
                        "days",
                        "latest",
                        "summary",
                        "item_count",
                        "available_history_count",
                        "amount_unit",
                        "ratio_unit",
                        "price_unit",
                        "main_flow_definition",
                        "interpretation_warning",
                        "source",
                        "source_url",
                        "source_transport",
                        "success",
                        "errors",
                        "warnings",
                        "data_time",
                        "source_data_time_granularity",
                        "is_stale",
                        "fallback_used",
                        "_cached",
                        "_fetched_at",
                    ],
                ),
                "recent": _trim_list(
                    result.get("items"),
                    10,
                    [
                        "date",
                        "close",
                        "pct_chg",
                        "main_net_inflow",
                        "main_net_inflow_pct",
                        "super_large_net_inflow",
                        "super_large_net_inflow_pct",
                        "large_net_inflow",
                        "large_net_inflow_pct",
                        "medium_net_inflow",
                        "medium_net_inflow_pct",
                        "small_net_inflow",
                        "small_net_inflow_pct",
                        "data_time",
                        "bar_complete",
                    ],
                ),
            },
            payload_policy="compacted",
            compacted=True,
            compaction_reason="capital_flow_recent_window",
        )

    if tool_name == "get_business_segments":
        return _annotate_tool_payload(
            tool_name,
            {
                **_pick_fields(
                    result,
                    [
                        "symbol",
                        "category",
                        "requested_periods",
                        "periods",
                        "available_categories",
                        "item_count",
                        "summaries",
                        "amount_unit",
                        "ratio_unit",
                        "currency",
                        "flow_basis",
                        "source",
                        "source_url",
                        "success",
                        "errors",
                        "data_time",
                        "is_stale",
                        "fallback_used",
                        "_cached",
                        "_fetched_at",
                    ],
                ),
                "items": _trim_list(
                    result.get("items"),
                    24,
                    [
                        "report_date",
                        "flow_basis",
                        "category",
                        "segment_name",
                        "revenue",
                        "revenue_share_pct",
                        "cost",
                        "cost_share_pct",
                        "gross_profit",
                        "gross_profit_share_pct",
                        "gross_margin_pct",
                    ],
                ),
            },
            payload_policy="compacted",
            compacted=True,
            compaction_reason="business_segment_window",
        )

    if tool_name == "get_consensus_estimates":
        return _annotate_tool_payload(
            tool_name,
            {
                **_pick_fields(
                    result,
                    [
                        "symbol",
                        "metric",
                        "estimates",
                        "institution_item_count",
                        "latest_institution_report_date",
                        "actuals",
                        "financial_forecasts",
                        "coverage_available",
                        "coverage_status",
                        "source_query_complete",
                        "coverage_count_latest",
                        "eps_unit",
                        "net_profit_unit",
                        "amount_unit_note",
                        "forecast_warning",
                        "source",
                        "source_url",
                        "success",
                        "partial",
                        "errors",
                        "warnings",
                        "data_time",
                        "freshness_unknown",
                        "is_stale",
                        "fallback_used",
                        "cache_detail",
                        "_cached",
                        "_fetched_at",
                    ],
                ),
                "institutions": _trim_list(
                    result.get("institutions"),
                    10,
                    [
                        "institution",
                        "analysts",
                        "report_date",
                        "forecasts",
                    ],
                ),
            },
            payload_policy="compacted",
            compacted=True,
            compaction_reason="consensus_estimate_window",
        )

    if tool_name == "get_peer_comparison":
        dimensions = {}
        for name, bucket in (result.get("dimensions") or {}).items():
            if not isinstance(bucket, dict):
                continue
            dimensions[name] = {
                **_pick_fields(
                    bucket,
                    [
                        "label",
                        "report_date",
                        "report_period",
                        "sample_size",
                        "target",
                        "industry_median",
                        "industry_average",
                        "target_rank",
                        "ranking",
                        "top_peer_item_count",
                        "source_scope",
                        "success",
                    ],
                ),
                "top_peers": _trim_list(bucket.get("top_peers"), 5),
            }
        return _annotate_tool_payload(
            tool_name,
            {
                **_pick_fields(
                    result,
                    [
                        "symbol",
                        "dimension",
                        "amount_unit",
                        "ratio_unit",
                        "source",
                        "source_url",
                        "success",
                        "partial",
                        "errors",
                        "data_time",
                        "freshness_unknown",
                        "is_stale",
                        "fallback_used",
                        "cache_detail",
                        "_cached",
                        "_fetched_at",
                    ],
                ),
                "dimensions": dimensions,
            },
            payload_policy="compacted",
            compacted=True,
            compaction_reason="peer_dimension_evidence_window",
        )

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
            **_pick_fields(
                result,
                [
                    "query",
                    "resolved_query",
                    "success",
                    "result_count",
                    "provider",
                    "attempts",
                    "retrieved_at",
                    "data_time",
                    "fallback_used",
                    "is_stale",
                    "freshness_unknown",
                    "latest_published_date",
                    "content_requested",
                    "content_result_count",
                    "_truncated",
                    "errors",
                    "warnings",
                ],
            ),
            "results": _trim_list(
                results,
                12,
                [
                    "title",
                    "url",
                    "snippet",
                    "content_text",
                    "content_characters",
                    "source",
                    "source_name",
                    "published_date",
                    "result_type",
                    "search_provider",
                    "crawl_provider",
                ],
            ),
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
        compact = _pick_fields(
            result,
            [
                "url",
                "final_url",
                "format",
                "content_type",
                "title",
                "success",
                "provider",
                "attempts",
                "data_time",
                "content_time",
                "fallback_used",
                "is_stale",
                "freshness_unknown",
                "extraction_method",
                "_truncated",
                "errors",
                "warnings",
            ],
        )
        compact["content"] = llm_content
        compact["content_characters"] = len(content)
        attachments = result.get("attachments") if isinstance(result.get("attachments"), list) else []
        if attachments:
            compact["attachments"] = _trim_list(attachments, 4, ["type", "mime"])
            compact["attachment_count"] = len(attachments)
        was_compacted = len(content) > 12000 or bool(attachments)
        reason = (
            "web_content_character_cap"
            if len(content) > 12000
            else ("web_attachment_binary_omitted" if attachments else None)
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
                "index_code",
                "index_name",
                "days",
                "latest",
                "history_count",
                "units",
                "source",
                "source_chain",
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
            tool_name, compact, payload_policy="compacted", compacted=True, compaction_reason="index_history_window"
        )

    return result
