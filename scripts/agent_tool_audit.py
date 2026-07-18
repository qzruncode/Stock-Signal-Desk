#!/usr/bin/env python3
"""Execute every registered Agent tool through the same HTTP probe used by UI.

The audit validates the public registry inventory, argument normalization,
execution timeout, compacted result contract and live upstream acquisition.
It prints one JSON record per tool and exits non-zero when any tool fails.
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
        "get_multi_stock_decision_evidence": {
            "symbols": "600519,000858",
            "thesis": "高端白酒盈利质量与估值比较",
        },
        "get_theme_stock_candidates": {"theme": "人形机器人", "limit": 1000},
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
            "days": 365,
            "limit": 12,
            "include_content": True,
            "fallback_to_web": True,
        },
        "search_research_library": {
            "query": "人形机器人 产业链 价值量",
            "category": "industry",
            "days": 1095,
            "limit": 12,
            "include_content": True,
            "fallback_to_web": True,
        },
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
        registered = {
            str(item.get("name"))
            for item in registry_response.json().get("tools") or []
            if isinstance(item, dict) and item.get("name")
        }
        configured = set(cases)
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

        tasks = [
            asyncio.create_task(execute(name, arguments))
            for name, arguments in cases.items()
        ]
        rows = []
        for task in asyncio.as_completed(tasks):
            row = await task
            rows.append(row)
            # Stream progress so a worker crash or a slow source does not turn
            # the audit into several minutes of opaque silence.
            print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)

    rows.sort(key=lambda item: str(item.get("tool") or ""))
    failed = [row["tool"] for row in rows if not row.get("success")]
    summary = {
        "registered": len(registered),
        "configured": len(configured),
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
