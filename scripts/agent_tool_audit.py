#!/usr/bin/env python3
"""Audit every registered Agent tool through the same HTTP surface used by UI.

Safe read-only tools are executed. Mutating tools and tools that require a
runtime-selected record/feed are schema-validated without creating user data.
The audit prints one JSON record per tool and exits non-zero on any inventory,
schema or safe-execution failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date, timedelta
from typing import Any

import httpx


def tool_cases() -> dict[str, dict[str, Any]]:
    end = date.today()
    start = end - timedelta(days=120)
    return {
        "search_stocks": {"query": "贵州茅台", "limit": 5},
        "manage_watchlist": {"action": "list"},
        "manage_watchlist_groups": {"action": "list"},
        "filter_watchlist_by_theme": {"theme": "AI和机器人"},
        "get_data_health": {},
        "get_analysis_status": {"limit": 5},
        "search_analysis_history": {"page": 1, "limit": 5},
        "manage_analysis_templates": {"action": "list"},
        "manage_batch_run": {"action": "list", "limit": 5},
        "manage_analysis_schedule": {"action": "get"},
        "get_notification_status": {},
        "get_realtime_quotes": {"symbols": "600519,000858"},
        "get_kline": {"symbol": "600519", "count": 30, "use_cache": True},
        "get_history_data": {
            "symbol": "600519",
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
            "use_cache": True,
        },
        "get_technical_indicators": {"symbol": "600519", "count": 120},
        "get_multi_stock_snapshot": {"symbols": "600519,000858"},
        "get_multi_stock_financials": {"symbols": "600519,000858"},
        "get_multi_stock_decision_evidence": {
            "symbols": "600519,000858",
            "thesis": "高端白酒盈利质量与估值比较",
        },
        "get_theme_stock_candidates": {"theme": "人形机器人", "limit": 1000},
        "get_domain_stock_candidates": {
            "domains": [
                {
                    "label": "空心杯电机",
                    "board_queries": ["机器人执行器"],
                    "mapping_type": "proxy_board",
                    "rationale": "发布审计使用已绑定的结构化板块参数",
                    "unresolved_parts": [],
                },
                {
                    "label": "减速器",
                    "board_queries": ["减速器"],
                    "mapping_type": "exact_board",
                    "rationale": "发布审计使用同名结构化板块参数",
                    "unresolved_parts": [],
                },
            ],
            "context_theme": "人形机器人",
            "limit_per_domain": 300,
        },
        "get_market_status": {},
        "get_market_breadth": {},
        "get_sector_list": {"type": "industry"},
        "get_sector_flow": {"type": "industry", "period": "5d", "top_n": 10},
        "get_stock_capital_flow": {"symbol": "600519", "days": 20},
        "get_stock_info": {"symbol": "600519"},
        "get_financials": {"symbol": "600519", "periods": 4},
        "get_balance_sheet": {"symbol": "600519", "periods": 4},
        "get_income_statement": {"symbol": "600519", "periods": 4},
        "get_cashflow": {"symbol": "600519", "periods": 4},
        "get_business_segments": {"symbol": "600519", "category": "all", "periods": 2},
        "get_valuation_ratios": {"symbol": "600519", "with_history": True},
        "get_consensus_estimates": {"symbol": "600519", "metric": "all"},
        "get_peer_comparison": {"symbol": "600519", "dimension": "all"},
        "get_shareholder_structure": {"symbol": "600519"},
        "search_news": {"symbol": "600519", "days": 90, "limit": 10, "use_cache": True},
        "search_financial_news": {
            "query": "人形机器人 订单 量产 产业链",
            "topic": "industry",
            "subjects": ["人形机器人"],
            "days": 365,
            "limit": 12,
            "include_content": True,
            "fallback_to_web": True,
        },
        "search_research_library": {
            "query": "人形机器人 产业链 价值量",
            "category": "industry",
            "subjects": ["人形机器人"],
            "days": 1095,
            "limit": 12,
            "include_content": True,
            "fallback_to_web": True,
        },
        "list_financial_sources": {"limit": 100},
        "inspect_financial_source": {"route_path": "/eeo/kuaixun"},
        "read_financial_feed": {"route_path": "/eeo/kuaixun", "limit": 5},
        "get_regulatory_updates": {
            "keyword": "贵州茅台",
            "event_type": "all",
            "market": "all",
            "days": 180,
            "limit": 10,
            "include_content": True,
            "fallback_to_web": True,
        },
        "get_monetary_policy_operations": {
            "days": 30,
            "instrument": "all",
            "limit": 10,
            "include_content": True,
            "fallback_to_web": True,
        },
        "get_announcements": {"symbol": "600519", "days": 180, "type": "all", "limit": 10},
        "get_risk_events": {"symbol": "600519", "days": 180, "limit": 10},
        "get_research_report": {"symbol": "600519", "days": 365, "limit": 10},
        "get_social_sentiment": {"symbol": "600519", "days": 30, "limit": 10, "max_pages": 2},
        "get_index_data": {"index_code": "000001", "days": 30},
        "get_bond_yield": {"country": "CN", "term": "10Y", "days": 90},
        "get_macro_indicator": {"indicator": "PMI", "periods": 6},
        "websearch": {
            "query": "贵州茅台 2025 年报 营业收入 官方",
            "numResults": 6,
            "livecrawl": "fallback",
            "type": "fast",
            "contextMaxCharacters": 8000,
            "includeContent": False,
        },
        "webfetch": {"url": "https://www.sse.com.cn/", "format": "text", "timeout": 30},
    }


def schema_only_tools() -> set[str]:
    """Tools that must not be fired blindly by an automated release audit."""
    return {
        "run_stock_analysis",
        "read_analysis_report",
        "delete_analysis_history",
        "run_batch_analysis",
        "send_notification",
        "screen_atr_volatility_stocks",
        "evaluate_multi_stock_buy_criteria",
        "analyze_stock_catalysts",
        "read_financial_article",
        "transform_webpage_to_feed",
        "export_financial_feed",
    }


def _result_count(result: Any) -> int | None:
    if not isinstance(result, dict):
        return None
    for key in ("item_count", "result_count", "count", "total", "sector_count"):
        value = result.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    items = result.get("items")
    return len(items) if isinstance(items, list) else None


async def run_audit(base_url: str, concurrency: int) -> int:
    cases = tool_cases()
    cookie = os.getenv("DSA_SESSION_COOKIE", "").strip()
    headers = {"Cookie": f"dsa_session={cookie}"} if cookie else {}
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=httpx.Timeout(130.0, connect=5.0),
    ) as client:
        registry_response = await client.get("/api/v1/agent/tool-registry")
        registry_response.raise_for_status()
        registry_items = registry_response.json().get("tools") or []
        registered = {
            str(item.get("name"))
            for item in registry_items
            if isinstance(item, dict) and item.get("name")
        }
        schema_by_name = {
            str(item.get("name")): item
            for item in registry_items
            if isinstance(item, dict) and item.get("name")
        }
        schema_only = schema_only_tools()
        configured = set(cases) | schema_only
        inventory_errors = {
            "missing_cases": sorted(registered - configured),
            "stale_cases": sorted(configured - registered),
        }

        async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                try:
                    response = await client.post(
                        "/api/v1/agent/tool-registry/execute",
                        json={"tool_name": name, "arguments": arguments},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    result = payload.get("result")
                    return {
                        "tool": name,
                        "success": payload.get("success") is True,
                        "duration_ms": payload.get("duration_ms"),
                        "count": _result_count(result),
                        "source": result.get("source") if isinstance(result, dict) else None,
                        "partial": result.get("partial") if isinstance(result, dict) else None,
                        "error": payload.get("error"),
                        "errors": (result.get("errors") or [])[:3] if isinstance(result, dict) else [],
                        "warnings": (result.get("warnings") or [])[:3] if isinstance(result, dict) else [],
                    }
                except Exception as exc:  # noqa: BLE001 - audit must keep checking the rest
                    return {
                        "tool": name,
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }

        rows = []
        for name in sorted(schema_only):
            schema = schema_by_name.get(name)
            valid = bool(
                isinstance(schema, dict)
                and schema.get("description")
                and isinstance(schema.get("parameters"), list)
            )
            row = {
                "tool": name,
                "mode": "schema_only",
                "success": valid,
                "error": None if valid else "registry schema is incomplete",
            }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)

        tasks = [
            asyncio.create_task(execute(name, arguments))
            for name, arguments in cases.items()
        ]
        for task in asyncio.as_completed(tasks):
            row = await task
            row["mode"] = "execute"
            rows.append(row)
            # Stream progress so a worker crash or a slow source does not turn
            # the audit into several minutes of opaque silence.
            print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)

    rows.sort(key=lambda item: str(item.get("tool") or ""))
    failed = [row["tool"] for row in rows if not row.get("success")]
    summary = {
        "registered": len(registered),
        "configured": len(configured),
        "executed": len(cases),
        "schema_only": len(schema_only),
        "passed": len(rows) - len(failed),
        "failed": len(failed),
        "failed_tools": failed,
        **inventory_errors,
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False, sort_keys=True))
    return 1 if failed or any(inventory_errors.values()) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Live audit for every registered AI Assistant tool")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    return asyncio.run(run_audit(args.base_url, args.concurrency))


if __name__ == "__main__":
    raise SystemExit(main())
