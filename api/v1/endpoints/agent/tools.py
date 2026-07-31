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
    return {field: item.get(field) for field in fields if field in item and item.get(field) is not None}


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




_WORKFLOW_TOOLS = {
    "manage_watchlist", "manage_watchlist_groups", "run_stock_analysis",
    "get_analysis_status", "search_analysis_history", "delete_analysis_history",
    "manage_analysis_templates", "run_batch_analysis", "manage_batch_run",
    "manage_analysis_schedule", "get_notification_status", "send_notification",
}

from . import _tool_compaction1 as _tool_compaction1
from . import _tool_compaction2 as _tool_compaction2
from . import _tool_compaction3 as _tool_compaction3
from . import _tool_compaction4 as _tool_compaction4


_COMPACTION_TOOL_NAMES = {
    'read_analysis_report',
    'screen_atr_volatility_stocks',
    'get_realtime_quotes',
    'get_multi_stock_snapshot',
    'get_multi_stock_financials',
    'get_multi_stock_decision_evidence',
    'evaluate_multi_stock_buy_criteria',
    'analyze_stock_catalysts',
    'get_domain_stock_candidates',
    'get_history_data',
    'get_kline',
    'get_market_status',
    'get_sector_list',
    'get_stock_info',
    'get_balance_sheet',
    'get_cashflow',
    'get_financials',
    'get_income_statement',
    'get_valuation_ratios',
    'get_shareholder_structure',
    'get_announcements',
    'get_research_report',
    'get_risk_events',
    'get_social_sentiment',
    'search_news',
    'search_financial_news',
    'list_financial_sources',
    'inspect_financial_source',
    'read_financial_feed',
    'transform_webpage_to_feed',
    'export_financial_feed',
    'read_financial_article',
    'get_stock_capital_flow',
    'get_business_segments',
    'get_consensus_estimates',
    'get_peer_comparison',
    'get_technical_indicators',
    'websearch',
    'webfetch',
    'get_index_data',
    'get_bond_yield',
    'get_macro_indicator',
    'get_sector_flow',
    'get_market_breadth',
    'search_research_library',
    'get_regulatory_updates',
    'get_monetary_policy_operations',

}


def _compact_tool_result(tool_name: str, result: Any) -> Any:
    """Shape endpoint results into LLM-friendly payloads with less noise."""
    if not isinstance(result, dict):
        return result
    if tool_name in _WORKFLOW_TOOLS:
        return _annotate_tool_payload(
            tool_name, result, payload_policy="complete", compacted=False,
            source_scope="persisted_analysis_workflow",
        )
    if tool_name in ['analyze_stock_catalysts', 'evaluate_multi_stock_buy_criteria', 'get_domain_stock_candidates', 'get_history_data', 'get_kline', 'get_market_status', 'get_multi_stock_decision_evidence', 'get_multi_stock_financials', 'get_multi_stock_snapshot', 'get_realtime_quotes', 'get_sector_list', 'get_stock_info', 'read_analysis_report', 'screen_atr_volatility_stocks']:
        return _tool_compaction1._compact_tool_group1(tool_name, result)
    if tool_name in ['get_announcements', 'get_balance_sheet', 'get_cashflow', 'get_financials', 'get_income_statement', 'get_research_report', 'get_risk_events', 'get_shareholder_structure', 'get_social_sentiment', 'get_valuation_ratios', 'inspect_financial_source', 'list_financial_sources', 'search_financial_news', 'search_news']:
        return _tool_compaction2._compact_tool_group2(tool_name, result)
    if tool_name in ['export_financial_feed', 'get_business_segments', 'get_consensus_estimates', 'get_index_data', 'get_peer_comparison', 'get_stock_capital_flow', 'get_technical_indicators', 'read_financial_article', 'read_financial_feed', 'transform_webpage_to_feed', 'webfetch', 'websearch']:
        return _tool_compaction3._compact_tool_group3(tool_name, result)
    if tool_name in ['get_bond_yield', 'get_macro_indicator', 'get_market_breadth', 'get_monetary_policy_operations', 'get_regulatory_updates', 'get_sector_flow', 'search_research_library']:
        return _tool_compaction4._compact_tool_group4(tool_name, result)
    return _annotate_tool_payload(
        tool_name,
        result,
        payload_policy="full",
        compacted=False,
    )



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
