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


def _trim_list(items: Any, limit: int, fields: List[str] | None = None) -> list[Any]:
    if not isinstance(items, list):
        return []
    trimmed = items[:limit]
    if fields is None:
        return trimmed
    result: list[Any] = []
    for item in trimmed:
        if isinstance(item, dict):
            result.append(_pick_fields(item, fields))
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
        "symbol": result.get("symbol") or result.get("index_code"),
        "count": len(series),
        "latest": latest,
        "recent": series[-LLM_SERIES_LIMIT:],
        "source": result.get("source"),
        "data_time": result.get("data_time") or latest.get("date"),
        "is_stale": result.get("is_stale"),
        "fallback_used": result.get("fallback_used"),
        "_cached": result.get("_cached"),
        "_fetched_at": result.get("_fetched_at"),
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
            "pe_ratio": "pe",
            "pb_ratio": "pb",
        }
        _QUOTE_PASSTHROUGH = (
            "name", "price", "high", "low", "volume", "amount",
            "turnover_rate", "total_mv", "circ_mv",
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
            "total": result.get("total", len(compact_items)),
            "items": compact_items,
            "data_time": result.get("data_time"),
            "is_stale": result.get("is_stale"),
            "fallback_used": result.get("fallback_used"),
            "_cached": result.get("_cached"),
        }, payload_policy="compacted", compacted=True, compaction_reason="quotes_item_window")

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
                "breadth_source", "sh_index", "data_time", "is_stale", "fallback_used", "_cached", "_fetched_at",
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
            "type": result.get("type"),
            "total": len(items),
            "top_movers": top,
            "bottom_movers": bottom,
            "data_time": result.get("data_time"),
            "is_stale": result.get("is_stale"),
            "fallback_used": result.get("fallback_used"),
            "_cached": result.get("_cached"),
            "_fetched_at": result.get("_fetched_at"),
        }, payload_policy="compacted", compacted=True, compaction_reason="sector_top_bottom_window")

    if tool_name == "get_stock_info":
        # endpoint 返回嵌套中文结构,先归一化成英文 key 再挑字段,
        # 否则白名单里除 symbol 外全部取不到值(只剩 symbol + meta)。
        normalized = _normalize_stock_info(result)
        return _annotate_tool_payload(tool_name, _pick_fields(
            normalized,
            [
                "symbol", "name", "short_name", "industry", "market", "listing_date",
                "main_business", "total_shares", "circ_shares", "pe_dynamic",
                "pe_static", "pb_ratio", "total_mv", "circ_mv", "_cached", "_fetched_at",
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
            "periods": len(series) if isinstance(series, list) else result.get("periods"),
            "latest": series[-1] if isinstance(series, list) and series else {},
            "recent_periods": series[-6:] if isinstance(series, list) else [],
            "source": result.get("source"),
            "_cached": result.get("_cached"),
            "_fetched_at": result.get("_fetched_at"),
        }, payload_policy="compacted", compacted=True, compaction_reason="financial_recent_periods")

    if tool_name == "get_valuation_ratios":
        return _annotate_tool_payload(tool_name, _pick_fields(
            result,
            [
                "symbol", "trade_date", "pe_static", "pe_dynamic", "pe_ttm", "pb", "ps",
                "pcf", "peg", "dividend_yield", "dividend_date", "pe_percentiles",
                "industry_average", "source_chain", "errors", "_cached", "_fetched_at",
            ],
        ), payload_policy="compacted", compacted=True, compaction_reason="valuation_key_fields")

    if tool_name == "get_shareholder_structure":
        return _annotate_tool_payload(tool_name, {
            **_pick_fields(
                result,
                [
                    "symbol", "holder_count", "holder_count_previous", "holder_count_change",
                    "holder_count_change_pct", "holder_report_date", "institution_holding_ratio",
                    "actual_controller", "control_change_date", "source_chain", "errors",
                    "_cached", "_fetched_at",
                ],
            ),
            "top_holders": _trim_list(
                result.get("top_holders"),
                10,
                ["rank", "holder_name", "holding_amount", "holding_ratio", "holder_type"],
            ),
            "holder_changes": _trim_list(
                result.get("holder_changes"),
                8,
                ["holder_name", "change_type", "change_amount", "change_ratio", "date"],
            ),
        }, payload_policy="compacted", compacted=True, compaction_reason="shareholder_top_lists")

    if tool_name in {"search_news", "get_announcements", "get_risk_events", "get_sentiment", "get_research_report", "get_social_sentiment"}:
        item_fields_map = {
            "search_news": ["title", "publish_time", "source", "category", "event_type", "polarity", "importance", "summary"],
            "get_announcements": ["title", "publish_date", "notice_type", "url"],
            "get_risk_events": ["title", "date", "source", "source_type", "severity", "risk_label", "risk_summary", "tags"],
            "get_sentiment": ["title", "label", "sentiment_score", "source", "event_type", "importance", "tags"],
            "get_research_report": ["title", "org", "rating", "publish_date", "industry", "profit_forecasts", "monthly_report_count"],
            "get_social_sentiment": ["title", "publish_time", "source", "label", "sentiment_score", "read_count", "reply_count"],
        }
        compact = _pick_fields(
            result,
            [
                "symbol", "days", "type", "source", "sentiment_score", "overall_score",
                "positive_count", "negative_count", "neutral_count", "total_discussion",
                "total_read", "total_reply", "diagnose_score", "top_keywords",
                "analysis", "source_chain", "errors", "data_time", "is_stale", "fallback_used", "_cached", "_fetched_at",
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

    if tool_name == "list_rss_sources":
        # Catalog listing — cap the source count to keep the payload bounded;
        # _rss_catalog already trimmed each entry to a slim view.
        sources = _trim_list(result.get("sources"), 30)
        return _annotate_tool_payload(
            tool_name,
            {"sources": sources, "source_count": len(result.get("sources") or [])},
            payload_policy="compacted",
            compacted=True,
            compaction_reason="rss_source_catalog_cap",
        )

    if tool_name == "read_rss_feed":
        compact = _pick_fields(
            result,
            ["feed_title", "feed_link", "item_count", "errors", "_cached"],
        )
        # _rss_reader already trimmed items (summary ≤180 chars, no content_html);
        # cap the array the LLM sees.
        compact["items"] = _trim_list(
            result.get("items"),
            LLM_ARRAY_LIMIT,
            ["title", "summary", "link", "published", "source", "image"],
        )
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="rss_feed_item_window",
        )

    if tool_name == "read_rss_item":
        # Single item — _rss_reader already capped content_text to 2000 chars.
        compact = _pick_fields(
            result,
            ["title", "content_text", "link", "published", "source", "_fallback", "_truncated", "_not_found", "errors"],
        )
        return _annotate_tool_payload(
            tool_name,
            compact,
            payload_policy="compacted",
            compacted=True,
            compaction_reason="rss_item_text_passthrough",
        )

    if tool_name == "get_index_data":
        compact = _pick_fields(
            result,
            ["index_code", "index_name", "latest", "source", "errors", "data_time", "is_stale", "fallback_used", "_cached", "_fetched_at"],
        )
        compact["history"] = _trim_list(result.get("history"), 12)
        return _annotate_tool_payload(tool_name, compact, payload_policy="compacted", compacted=True, compaction_reason="index_history_window")

    if tool_name == "get_bond_yield":
        compact = _pick_fields(
            result,
            ["country", "term", "latest_yield", "spread", "source", "errors", "data_time", "is_stale", "fallback_used", "_cached", "_fetched_at"],
        )
        compact["history"] = _trim_list(result.get("history"), 12)
        return _annotate_tool_payload(tool_name, compact, payload_policy="compacted", compacted=True, compaction_reason="bond_history_window")

    if tool_name == "get_macro_indicator":
        compact = _pick_fields(
            result,
            ["indicator", "indicator_name", "latest", "trend", "source", "errors", "data_time", "is_stale", "fallback_used", "_cached", "_fetched_at"],
        )
        compact["history"] = _trim_list(result.get("history"), 12)
        return _annotate_tool_payload(tool_name, compact, payload_policy="compacted", compacted=True, compaction_reason="macro_history_window")

    if tool_name == "get_sector_flow":
        return _annotate_tool_payload(tool_name, {
            "type": result.get("type"),
            "top_n": result.get("top_n"),
            "inflow_top": _trim_list(
                result.get("inflow_top"),
                10,
                ["name", "pct_chg", "main_net_inflow", "super_large_net_inflow", "large_net_inflow", "leading_stock"],
            ),
            "outflow_top": _trim_list(
                result.get("outflow_top"),
                10,
                ["name", "pct_chg", "main_net_inflow", "super_large_net_inflow", "large_net_inflow", "leading_stock"],
            ),
            "source": result.get("source"),
            "errors": result.get("errors"),
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
                "new_high_60d", "new_low_60d", "consecutive_up_days", "consecutive_down_days",
                "limit_up_count", "limit_down_count", "broken_board_rate", "volume",
                "source", "errors", "data_time", "is_stale", "fallback_used", "_cached", "_fetched_at",
            ],
        ), payload_policy="compacted", compacted=True, compaction_reason="market_breadth_key_fields")

    if tool_name in {"search_web_news", "search_web_price_fallback"}:
        compact = _pick_fields(
            result,
            ["query", "provider", "success", "error_message", "search_time"],
        )
        compact["results"] = _trim_list(
            result.get("results"),
            LLM_ARRAY_LIMIT,
            ["title", "snippet", "url", "source", "published_date"],
        )
        return _annotate_tool_payload(tool_name, compact, payload_policy="compacted", compacted=True, compaction_reason="web_search_result_window")

    if tool_name == "fetch_web_content":
        return _annotate_tool_payload(tool_name, {
            "url": result.get("url"),
            "content": str(result.get("content") or "")[:2500],
        }, payload_policy="compacted", compacted=True, compaction_reason="web_content_preview")

    return _annotate_tool_payload(tool_name, result, payload_policy="full", compacted=False)


def _serialize_search_response(response: Any) -> Dict[str, Any]:
    return {
        "query": getattr(response, "query", ""),
        "provider": getattr(response, "provider", ""),
        "success": bool(getattr(response, "success", False)),
        "error_message": getattr(response, "error_message", None),
        "search_time": getattr(response, "search_time", 0.0),
        "results": [
            {
                "title": item.title,
                "snippet": item.snippet,
                "url": item.url,
                "source": item.source,
                "published_date": item.published_date,
            }
            for item in getattr(response, "results", [])[:5]
        ],
    }


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
        from src.search_service import get_search_service

        service = get_search_service()
        if not service.is_available:
            return {
                "type": fallback_type,
                "success": False,
                "error_message": "未配置搜索能力",
                "results": [],
            }

        if fallback_type == "price":
            response = service.search_stock_price_fallback(code, name, max_attempts=2, max_results=5)
        else:
            response = service.search_stock_news(code, name, max_results=5)
        payload = _serialize_search_response(response)
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

    symbol_arg = args.get("symbol") or args.get("symbols")
    if isinstance(symbol_arg, str) and "," in symbol_arg:
        symbol_arg = symbol_arg.split(",", 1)[0]
    code, name = _resolve_search_subject(symbol_arg)
    if not code or not name:
        enriched = dict(result)
        enriched["fallback_status"] = {"used": False, "reason": health.get("reason"), "message": "无法确定搜索对象"}
        return enriched

    fallback_type = "price" if tool_name in {"get_realtime_quotes", "get_kline", "get_history_data"} else "news"
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
