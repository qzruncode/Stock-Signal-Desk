# -*- coding: utf-8 -*-
"""``get_buy_criteria_analysis`` tool."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from src.tools.symbols import resolve_symbol

logger = logging.getLogger(__name__)


def get_buy_criteria_analysis(symbol: str, skip_cache: bool = False) -> Any:
    resolved_symbol = resolve_symbol(symbol)

    if not skip_cache:
        from src.storage import get_db

        cached = get_db().get_buy_criteria_record(resolved_symbol, date.today())
        if cached is not None:
            logger.info("buy_criteria serving cached result for %s", resolved_symbol)
            return {
                "symbol": cached["symbol"],
                "stock_name": cached.get("stock_name", ""),
                "trade_date": str(cached["trade_date"]),
                "cached": True,
                "final_decision": cached["final_decision"],
                "passed_count": cached["passed_count"],
                "failed_count": cached["failed_count"],
                "not_evaluated_count": cached["not_evaluated_count"],
                "stopped_at": cached["stopped_at"],
                "summary": cached["summary"],
                "criteria": [
                    {
                        "criterion_id": item["criterion_id"],
                        "criterion_name": item["criterion_name"],
                        "index": item["index"],
                        "passed": item["passed"],
                        "verdict": item["verdict"],
                        "evidence": {"data_summary": item.get("evidence", {}).get("data_summary", "")},
                        "analyzed_at": item.get("analyzed_at", ""),
                    }
                    for item in cached.get("results", [])
                ],
            }

    logger.info("buy_criteria running fresh analysis for %s", resolved_symbol)
    from src.services.buy_criteria.orchestrator import CriterionOrchestrator

    results = CriterionOrchestrator().run(resolved_symbol)
    passed_count = sum(1 for item in results if item.passed)
    failed_count = sum(1 for item in results if not item.passed)
    stopped_at = next((item.criterion_id for item in results if not item.passed), None)

    try:
        from api.v1.endpoints.stock_info import get_stock_info
        stock_name = get_stock_info(resolved_symbol).get("name", resolved_symbol)
    except Exception:
        stock_name = resolved_symbol

    return {
        "symbol": resolved_symbol,
        "stock_name": stock_name,
        "trade_date": date.today().isoformat(),
        "cached": False,
        "final_decision": "可买入" if passed_count == 8 and failed_count == 0 else "不可买入",
        "passed_count": passed_count,
        "failed_count": failed_count,
        "not_evaluated_count": 8 - len(results),
        "stopped_at": stopped_at,
        "summary": "，".join(
            [f"{passed_count}项通过"] + ([f"{failed_count}项未通过"] if failed_count else [])
        ) + ("，可买入" if passed_count == 8 else "，不可买入"),
        "criteria": [
            {
                "criterion_id": item.criterion_id,
                "criterion_name": item.criterion_name,
                "index": item.index,
                "passed": item.passed,
                "verdict": item.verdict,
                "evidence": {"data_summary": item.evidence.data_summary},
                "analyzed_at": item.analyzed_at,
            }
            for item in results
        ],
    }
