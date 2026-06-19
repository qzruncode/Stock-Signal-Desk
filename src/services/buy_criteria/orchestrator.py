# src/services/buy_criteria/orchestrator.py
"""Orchestrates sequential execution of 8 criterion evaluators with SSE output."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from src.services.buy_criteria.base import CriterionResult
from src.services.buy_criteria.evaluators import EVALUATOR_CLASSES

logger = logging.getLogger(__name__)


def _format_sse(event_type: str, data: dict[str, Any]) -> str:
    """Format a single SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _get_stock_info_safe(symbol: str) -> dict[str, Any]:
    """Get stock info with error handling."""
    try:
        from api.v1.endpoints.stock_info import get_stock_info
        return get_stock_info(symbol)
    except Exception as exc:
        logger.error("[buy_criteria] failed to get stock_info for %s: %s", symbol, exc)
        return {"symbol": symbol, "name": symbol, "industry": ""}


class CriterionOrchestrator:
    """Runs 8 evaluators sequentially. Stops on first failure. Emits SSE events."""

    def run(self, symbol: str) -> list[CriterionResult]:
        """Run all evaluators. Returns list of results (may be partial if early-terminated)."""
        stock_info = _get_stock_info_safe(symbol)
        results: list[CriterionResult] = []

        for evaluator_cls in EVALUATOR_CLASSES:
            evaluator = evaluator_cls()
            result = evaluator.evaluate(symbol, stock_info)
            results.append(result)

            if not result.passed:
                logger.info(
                    "[buy_criteria] %s failed for %s — early termination",
                    evaluator.criterion_id, symbol,
                )
                break

        return results

    @staticmethod
    def make_sse_endpoint(symbol: str):
        """Create an SSE StreamingResponse for the given symbol.

        Usage in a FastAPI endpoint:
            return CriterionOrchestrator.make_sse_endpoint(symbol)
        """
        from fastapi.responses import StreamingResponse

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        SENTINEL_DONE = object()

        def _worker():
            try:
                stock_info_data = _get_stock_info_safe(symbol)
                results: list[CriterionResult] = []

                for evaluator_cls in EVALUATOR_CLASSES:
                    evaluator = evaluator_cls()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("criterion_start", {
                            "criterion_id": evaluator.criterion_id,
                            "criterion_name": evaluator.criterion_name,
                            "index": evaluator.index,
                        }),
                    )
                    result = evaluator.evaluate(symbol, stock_info_data)
                    results.append(result)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("criterion_complete", result.to_dict()),
                    )
                    if not result.passed:
                        break

                passed_count = sum(1 for r in results if r.passed)
                failed_count = sum(1 for r in results if not r.passed)
                not_evaluated = 8 - len(results)
                stopped_at = next((r.criterion_id for r in results if not r.passed), None)
                final_decision = "可买入" if passed_count == 8 and failed_count == 0 else "不可买入"
                summary_parts = [f"{passed_count}项通过"]
                if failed_count:
                    summary_parts.append(f"{failed_count}项未通过")
                summary = "，".join(summary_parts) + ("，可买入" if passed_count == 8 else "，不可买入")

                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("analysis_complete", {
                        "final_decision": final_decision,
                        "passed_count": passed_count,
                        "failed_count": failed_count,
                        "not_evaluated_count": not_evaluated,
                        "stopped_at": stopped_at,
                        "summary": summary,
                    }),
                )
            except Exception as exc:
                logger.error("[buy_criteria] orchestrator error: %s", exc, exc_info=True)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("error", {"criterion_id": "", "message": str(exc)}),
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, (SENTINEL_DONE, None))

        threading.Thread(target=_worker, daemon=True).start()

        async def event_generator():
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                event_type, data = item
                if event_type is SENTINEL_DONE:
                    break
                yield _format_sse(event_type, data)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
