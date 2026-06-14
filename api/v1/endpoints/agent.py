# -*- coding: utf-8 -*-
"""
Agent Chat endpoint — ReAct loop with assistant-stream DataStreamResponse.

POST /api/v1/agent/chat

Body: { "messages": [ { "role": "user", "content": "贵州茅台行情" } ] }
Response: DataStreamResponse (line-delimited type-code:json chunks)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List

import litellm
from assistant_stream import RunController, create_run
from assistant_stream.serialization.data_stream import DataStreamResponse
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from api.deps import get_database_manager
from src.agent.tool_registry import ToolRegistry
from src.config import get_config, extra_litellm_params, get_api_keys_for_model
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_REACT_ITERATIONS = 10
LLM_ARRAY_LIMIT = 8
LLM_SERIES_LIMIT = 30

# Shared tool registry (22 tools)
_registry = ToolRegistry()

SYSTEM_PROMPT = """\
你是 A 股智能分析助手，擅长股票分析、行业研究和投资辅助。

## 核心原则
1. 所有数据必须通过工具获取，不得编造任何数字
2. 分析要有理有据，每个结论都要有数据支撑
3. 风险提示优先，宁可不推荐也不做错误推荐
4. 回答使用中文
5. 当用户给出股票名称时，先用 get_stock_info 确认股票代码再查询
6. 数字用千分位，百分比保留两位小数
7. 回答中使用 Markdown 格式化

## 分析框架
- 基本面分析：财务指标 → 估值水平 → 股东结构
- 技术面分析：K线形态 → 量价关系 → 资金流向
- 消息面分析：新闻舆情 → 公告研报 → 社交情绪
- 宏观面分析：大盘指数 → 宏观指标 → 板块轮动

## 工作方式
1. 先理解用户意图，判断需要哪些数据
2. 调用工具获取数据，每次调 1-3 个
3. 分析数据，形成判断
4. 如果信息不足，继续调用工具补充
5. 给出结构化的分析结论

## 工具调用约束
- 优先使用默认参数，不要主动放大时间窗口，除非用户明确要求更长周期
- 优先轻量工具：先看摘要型数据，再决定是否需要更深层明细
- 新闻/公告/舆情默认只看近30天，研报默认近365天，除非用户明确要求长期回溯
- 财务分析优先最近 4-6 个报告期，只有在比较长期趋势时才扩大 periods
- 技术分析优先最近 60 根日线；只有在复盘长周期趋势时才请求更长 K 线
- 板块和宏观工具优先使用摘要结果，不要重复请求相似维度的数据
- 若用户输入的是股票名称，可直接调用支持名称解析的工具，无需先额外做一次无意义查询
"""


def _get_llm_config():
    """Get the LLM config for the agent chat endpoint.

    The model_list may contain placeholder model_names like ``__legacy_openai__``
    which are meant for litellm.Router routing — they are NOT valid litellm
    model identifiers.  Always resolve the actual model from
    ``config.litellm_model`` (e.g. ``openai/gpt-5.5``) and pull API
    credentials from the first matching model_list entry.
    """
    config = get_config()
    # Always use the resolved model name (e.g. "openai/gpt-5.5"),
    # never the Router placeholder tokens like "__legacy_openai__".
    model = config.litellm_model or "gpt-4o"
    api_key = None
    api_base = config.openai_base_url or None

    models = config.llm_model_list if config.llm_model_list else []
    if models:
        first = models[0]
        lp = first.get("litellm_params", {})
        api_key = lp.get("api_key")
        if lp.get("api_base"):
            api_base = lp["api_base"]

    # For legacy_env source, resolve API key via provider-specific key lists
    if not api_key:
        keys = get_api_keys_for_model(model, config)
        if keys:
            api_key = keys[0]

    # Apply extra params (api_base, extra_headers) for the resolved model
    extra = extra_litellm_params(model, config)
    if extra.get("api_base") and not api_base:
        api_base = extra["api_base"]
    extra_headers = extra.get("extra_headers")

    return {
        "model": model,
        "api_key": api_key,
        "api_base": api_base,
        "extra_headers": extra_headers,
    }


async def _stream_final_answer_without_tools(
    controller: RunController,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
) -> str:
    """Force one final synthesis pass without tool use to avoid silent exits."""
    forced_messages = [
        *messages,
        {
            "role": "system",
            "content": (
                "你已经拿到了前面工具返回的数据。"
                "现在必须直接给出最终分析结论，不要再调用任何工具。"
                "如果信息仍有缺口，要明确说明缺口、风险点和结论置信度。"
            ),
        },
    ]

    kwargs: Dict[str, Any] = {
        "model": llm_cfg["model"],
        "messages": forced_messages,
        "stream": True,
    }
    if llm_cfg.get("api_key"):
        kwargs["api_key"] = llm_cfg["api_key"]
    if llm_cfg.get("api_base"):
        kwargs["api_base"] = llm_cfg["api_base"]
    if llm_cfg.get("extra_headers"):
        kwargs["extra_headers"] = llm_cfg["extra_headers"]

    try:
        response = await litellm.acompletion(**kwargs)
    except Exception as e:
        logger.exception("[Agent] Forced final answer failed")
        controller.append_text(
            "\n\n已完成多轮数据查询，但生成最终总结时出错。"
            f"请稍后重试，或缩小问题范围后再问一次。\n\n错误：{e}"
        )
        return ""

    content_text = ""
    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if not delta or not delta.content:
            continue
        content_text += delta.content
        controller.append_text(delta.content)

    if content_text.strip():
        return content_text

    controller.append_text(
        "\n\n已完成多轮数据查询，但模型没有产出最终总结。"
        "建议重试一次，或把问题拆成更小的比较维度来问。"
    )
    return ""


def _format_result(result: Any) -> str:
    """Format a tool result for LLM context without implicit truncation.

    Tool-facing compaction must stay explicit and tool-specific inside
    ``_compact_tool_result`` so neither the model nor the user silently
    receives clipped payloads.
    """
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
        items = result.get("items", [])
        compact_items = _trim_list(
            items,
            12,
            [
                "symbol", "name", "price", "change", "pct_chg", "open", "high", "low",
                "volume", "amount", "turnover_rate", "pe", "pb", "total_mv", "circ_mv",
            ],
        )
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
                "sh_index", "data_time", "is_stale", "fallback_used", "_cached", "_fetched_at",
            ],
        ), payload_policy="compacted", compacted=True, compaction_reason="market_status_key_fields")

    if tool_name == "get_market_mainline_report":
        return _annotate_tool_payload(
            tool_name,
            result,
            payload_policy="full",
            compacted=False,
            compaction_reason=None,
            source_scope="page_and_storage_aligned",
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
        return _annotate_tool_payload(tool_name, _pick_fields(
            result,
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


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for candidate in (text, text[:10]):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    for candidate, fmt in ((text[:10], "%Y-%m-%d"), (text[:8], "%Y%m%d")):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _latest_date_from_items(items: Any, keys: List[str]) -> datetime | None:
    if not isinstance(items, list):
        return None
    latest: datetime | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            dt = _parse_iso_datetime(item.get(key))
            if dt and (latest is None or dt > latest):
                latest = dt
    return latest


def _assess_tool_data_health(tool_name: str, result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"should_fallback": False, "reason": None}

    if result.get("error"):
        return {"should_fallback": True, "reason": "tool_error"}

    if result.get("is_stale") is True:
        return {
            "should_fallback": True,
            "reason": f"stale_{tool_name}",
            "latest_date": result.get("data_time"),
        }

    if tool_name == "get_realtime_quotes":
        items = result.get("items") or []
        if not items:
            return {"should_fallback": True, "reason": "empty_quotes"}
        return {"should_fallback": False, "reason": None}

    if tool_name in {"get_kline", "get_history_data"}:
        series = result.get("recent") or result.get("data") or []
        if not series:
            return {"should_fallback": True, "reason": "empty_kline"}
        if result.get("data_time"):
            latest = _parse_iso_datetime(result.get("data_time"))
            if latest and latest.date() < (datetime.now().date() - timedelta(days=7)):
                return {"should_fallback": True, "reason": "stale_kline", "latest_date": latest.date().isoformat()}
        latest = _latest_date_from_items(series, ["date"])
        if latest and latest.date() < (datetime.now().date() - timedelta(days=7)):
            return {"should_fallback": True, "reason": "stale_kline", "latest_date": latest.date().isoformat()}
        return {"should_fallback": False, "reason": None}

    if tool_name in {"search_news", "get_announcements", "get_risk_events", "get_sentiment", "get_research_report", "get_social_sentiment"}:
        items = result.get("items") or []
        if not items:
            return {"should_fallback": True, "reason": "empty_news_family"}
        if result.get("data_time"):
            latest = _parse_iso_datetime(result.get("data_time"))
            days = int(result.get("days") or 30)
            if latest and latest < (datetime.now() - timedelta(days=max(days, 1))):
                return {"should_fallback": True, "reason": "stale_news_family", "latest_date": latest.date().isoformat()}
        latest = _latest_date_from_items(items, ["publish_time", "publish_date", "date_str"])
        days = int(result.get("days") or 30)
        if latest and latest < (datetime.now() - timedelta(days=max(days, 1))):
            return {"should_fallback": True, "reason": "stale_news_family", "latest_date": latest.date().isoformat()}
        return {"should_fallback": False, "reason": None}

    return {"should_fallback": False, "reason": None}


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


async def _flush_substreams(controller: RunController) -> None:
    """Wait for all pending add_stream reader tasks to finish.

    ``controller.add_tool_call()`` internally uses ``add_stream`` which
    spawns an ``asyncio.create_task(reader())`` to relay sub-stream chunks
    into the main queue.  Because the reader is a *separate* task, its
    chunks can be delayed relative to synchronous ``append_text()`` calls
    made in the same event-loop tick.  Awaiting the stream tasks here
    guarantees that all tool-call chunks are already in the main queue
    before the next ReAct iteration begins emitting text.
    """
    for task in controller._stream_tasks:
        if not task.done():
            await task


async def _run_react_loop(
    controller: RunController,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
) -> str:
    """Execute the ReAct loop: LLM thinks → calls tools → observes → repeats."""

    tools = _registry.get_all_schemas()
    tool_names = set(_registry.get_tool_names())

    # Build messages with system prompt
    full_messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *messages,
    ]

    # Build litellm kwargs
    kwargs: Dict[str, Any] = {
        "model": llm_cfg["model"],
        "messages": full_messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,
    }
    if llm_cfg.get("api_key"):
        kwargs["api_key"] = llm_cfg["api_key"]
    if llm_cfg.get("api_base"):
        kwargs["api_base"] = llm_cfg["api_base"]
    if llm_cfg.get("extra_headers"):
        kwargs["extra_headers"] = llm_cfg["extra_headers"]

    controller.append_text("正在理解问题并规划需要查询的数据...\n\n")

    for iteration in range(MAX_REACT_ITERATIONS):
        # Collect streaming response
        chunks: List[Any] = []
        tool_calls_acc: Dict[int, Dict[str, Any]] = {}
        content_text = ""

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as e:
            logger.exception("[Agent] LLM call failed")
            controller.append_text(f"\n\n分析出错：{e}")
            return ""

        # Process stream
        async for chunk in response:
            chunks.append(chunk)
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            # Text content
            if delta.content:
                content_text += delta.content
                controller.append_text(delta.content)

            # Tool calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function and tc.function.name:
                        tool_calls_acc[idx]["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_calls_acc[idx]["arguments"] += tc.function.arguments

        # If LLM returned content without tool calls → done
        if not tool_calls_acc:
            return content_text

        # Execute tool calls
        controller.append_text("\n\n正在调用数据工具...\n\n")
        for tc in tool_calls_acc.values():
            tool_name = tc["name"].strip()
            tool_call_id = tc["id"] or f"call_{uuid.uuid4().hex}"
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"].strip() else {}
            except json.JSONDecodeError:
                args = {}

            # Notify frontend about tool call
            tool = await controller.add_tool_call(tool_name, tool_call_id=tool_call_id)
            tool.append_args_text(json.dumps(args, ensure_ascii=False))

            if tool_name not in tool_names:
                result_str = f"工具 '{tool_name}' 不存在，可用工具: {', '.join(sorted(tool_names))}"
                tool.set_response({"error": result_str}, is_error=True)
            else:
                try:
                    result = _registry.execute(tool_name, args)
                    llm_result = _compact_tool_result(tool_name, result)
                    llm_result = _maybe_attach_search_fallback(tool_name, args, llm_result)
                    result_str = _format_result(llm_result)
                    tool.set_response(llm_result if isinstance(llm_result, dict) else {"result": result_str})
                except Exception as e:
                    logger.exception("[Agent] Tool execution failed: %s", tool_name)
                    result_str = f"工具执行失败: {e}"
                    tool.set_response({"error": result_str}, is_error=True)

            # Add tool result to messages for next iteration
            full_messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": tc["arguments"]},
                }],
            })
            full_messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result_str,
            })

        # Flush all sub-stream reader tasks so that tool-call chunks
        # reach the main queue before the next iteration's text deltas.
        # Without this the next iteration's append_text() can overtake
        # tool-call events (because add_stream uses asyncio.create_task
        # which is deferred until the next event-loop tick), causing the
        # frontend ReadableStream to fail with "Cannot enqueue a chunk
        # into a readable stream that is closed".
        await _flush_substreams(controller)

        # Update kwargs messages
        kwargs["messages"] = full_messages

    logger.warning("[Agent] ReAct loop hit max iterations without final answer")
    controller.append_text("\n\n已完成多轮数据查询，正在生成最终总结...\n\n")
    return await _stream_final_answer_without_tools(controller, full_messages, llm_cfg)


@router.get("/agent/conversations")
def list_agent_conversations(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = ChatSessionService(db_manager)
    return service.list_conversations(page=page, limit=limit)


@router.post("/agent/conversations")
def create_agent_conversation(
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = ChatSessionService(db_manager)
    return service.create_conversation()


@router.get("/agent/conversations/{conversation_id}")
def get_agent_conversation(
    conversation_id: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = ChatSessionService(db_manager)
    conversation = service.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conversation


@router.patch("/agent/conversations/{conversation_id}")
def rename_agent_conversation(
    conversation_id: str,
    payload: Dict[str, Any] = Body(...),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title 不能为空")
    service = ChatSessionService(db_manager)
    conversation = service.rename_conversation(conversation_id, title)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conversation


@router.delete("/agent/conversations/{conversation_id}")
def delete_agent_conversation(
    conversation_id: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = ChatSessionService(db_manager)
    deleted = service.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="对话不存在")
    return {"deleted": deleted}


@router.put("/agent/conversations/{conversation_id}/snapshot")
def sync_agent_conversation_snapshot(
    conversation_id: str,
    payload: Dict[str, Any] = Body(...),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    service = ChatSessionService(db_manager)
    messages = payload.get("messages", [])
    thread_state = payload.get("thread_state")
    conversation = service.save_conversation_snapshot(
        conversation_id,
        messages if isinstance(messages, list) else [],
        thread_state=thread_state if isinstance(thread_state, dict) else None,
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conversation


@router.post("/agent/chat")
async def agent_chat(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """Chat endpoint using assistant-stream DataStream protocol."""
    body = await request.json()
    messages = body.get("messages", [])
    conversation_id = body.get("conversation_id")
    llm_cfg = _get_llm_config()
    session_service = ChatSessionService(db_manager)
    conversation = session_service.ensure_conversation(conversation_id)
    final_response_text = ""

    logger.info(
        f"[Agent] Chat request with {len(messages)} messages, "
        f"model={llm_cfg['model']}, conversation_id={conversation['id']}"
    )

    async def run_callback(controller: RunController):
        nonlocal final_response_text
        final_response_text = await _run_react_loop(controller, messages, llm_cfg)

        persisted_messages = list(messages)
        if final_response_text.strip():
            persisted_messages.append(
                {
                    "id": f"assistant-{uuid.uuid4().hex}",
                    "role": "assistant",
                    "content": final_response_text,
                    "created_at": datetime.now().isoformat(),
                }
            )
        session_service.save_conversation_snapshot(conversation["id"], persisted_messages)

    stream = create_run(run_callback)
    return DataStreamResponse(stream)
