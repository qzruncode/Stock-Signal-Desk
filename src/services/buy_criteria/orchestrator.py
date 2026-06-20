# src/services/buy_criteria/orchestrator.py
"""Orchestrates sequential execution of 8 criterion evaluators with SSE output."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import date as date_type
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

    def run(
        self,
        symbol: str,
        pre_fetched_data: dict[str, Any] | None = None,
        *,
        save_to_db: bool = True,
    ) -> list[CriterionResult]:
        """Run all evaluators. Returns list of results (may be partial if early-terminated).

        If ``save_to_db`` is True (default), results are persisted for today's date
        so the frontend page can reuse them via cache.
        """
        from src.services.buy_criteria.data_service import _clear_cache

        _clear_cache()
        results = self._run_evaluators(symbol, pre_fetched_data)

        if save_to_db and results:
            self._save_results(symbol, results)

        return results

    def _run_evaluators(
        self,
        symbol: str,
        pre_fetched_data: dict[str, Any] | None = None,
    ) -> list[CriterionResult]:
        """Core evaluator loop: collect + evaluate, stop on first failure."""
        stock_info = _get_stock_info_safe(symbol)
        results: list[CriterionResult] = []

        for evaluator_cls in EVALUATOR_CLASSES:
            evaluator = evaluator_cls()
            result = evaluator.evaluate(symbol, stock_info, pre_fetched_data)
            results.append(result)

            if not result.passed:
                logger.info(
                    "[buy_criteria] %s failed for %s — early termination",
                    evaluator.criterion_id, symbol,
                )
                break

        return results

    @staticmethod
    def _save_results(symbol: str, results: list[CriterionResult]) -> None:
        """Persist evaluation results to DB for today's date."""
        trade_date = date_type.today()
        passed_count = sum(1 for r in results if r.passed)
        failed_count = sum(1 for r in results if not r.passed)
        not_evaluated = 8 - len(results)
        stopped_at = next((r.criterion_id for r in results if not r.passed), None)
        final_decision = "可买入" if passed_count == 8 and failed_count == 0 else "不可买入"
        summary_parts = [f"{passed_count}项通过"]
        if failed_count:
            summary_parts.append(f"{failed_count}项未通过")
        summary = "，".join(summary_parts) + ("，可买入" if passed_count == 8 else "，不可买入")

        stock_name = _get_stock_info_safe(symbol).get("name") or symbol
        results_dicts = [r.to_dict() for r in results]

        from src.storage import get_db

        db = get_db()
        try:
            db.save_buy_criteria_record(
                symbol=symbol,
                trade_date=trade_date,
                stock_name=stock_name,
                final_decision=final_decision,
                passed_count=passed_count,
                failed_count=failed_count,
                not_evaluated_count=not_evaluated,
                stopped_at=stopped_at,
                summary=summary,
                results=results_dicts,
            )
            logger.info(
                "[buy_criteria] saved %s result to DB (%s, %d/%d passed)",
                symbol, final_decision, passed_count, 8,
            )
        except Exception as exc:
            logger.error("[buy_criteria] failed to save record for %s: %s", symbol, exc)

    @staticmethod
    def make_sse_endpoint(
        symbol: str,
        pre_fetched_data: dict[str, Any] | None = None,
    ):
        """Create an SSE StreamingResponse for the given symbol.

        Usage in a FastAPI endpoint:
            return CriterionOrchestrator.make_sse_endpoint(symbol, pre_fetched_data)
        """
        from fastapi.responses import StreamingResponse

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        SENTINEL_DONE = object()

        def _worker():
            try:
                # ── Step 1: Check DB cache for today ──────────────────────────
                trade_date = date_type.today()

                from src.storage import get_db
                db = get_db()
                cached = db.get_buy_criteria_record(symbol, trade_date)

                if cached is not None:
                    # Stream cached results with a "cached" event first
                    logger.info(
                        "[buy_criteria] serving cached result for %s @ %s",
                        symbol, trade_date,
                    )
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("connected", {"cached": True, "trade_date": cached["trade_date"]}),
                    )
                    results = cached.get("results", [])
                    for r in results:
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            ("criterion_complete", r),
                        )
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("analysis_complete", {
                            "final_decision": cached["final_decision"],
                            "passed_count": cached["passed_count"],
                            "failed_count": cached["failed_count"],
                            "not_evaluated_count": cached["not_evaluated_count"],
                            "stopped_at": cached["stopped_at"],
                            "summary": cached["summary"],
                        }),
                    )
                    loop.call_soon_threadsafe(queue.put_nowait, (SENTINEL_DONE, None))
                    return

                # ── Step 2: No cache — run fresh analysis ────────────────────
                from src.services.buy_criteria.data_service import _clear_cache
                _clear_cache()
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
                    result = evaluator.evaluate(symbol, stock_info_data, pre_fetched_data)
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

                # ── Step 3: Save to DB ────────────────────────────────────────
                CriterionOrchestrator._save_results(symbol, results)

                # ── Step 4: Emit completion event ───────────────────────────
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("connected", {"cached": False, "trade_date": trade_date.isoformat()}),
                )
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
