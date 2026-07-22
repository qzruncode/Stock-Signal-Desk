# src/services/buy_criteria/orchestrator.py
"""Orchestrate the single authoritative sequential buy-decision chain."""
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


def _cache_matches_current_contract(cached: dict[str, Any] | None) -> bool:
    """Accept only a valid prefix produced by the current Boolean gate chain."""
    if not isinstance(cached, dict):
        return False
    results = [item for item in cached.get("results") or [] if isinstance(item, dict)]
    if not results:
        return False
    expected = [evaluator.criterion_id for evaluator in EVALUATOR_CLASSES]
    actual = [str(item.get("criterion_id") or "") for item in results]
    if actual != expected[:len(actual)] or not all("status" in item for item in results):
        return False
    statuses = [str(item.get("status") or "") for item in results]
    if any(status not in {"pass", "fail", "insufficient"} for status in statuses):
        return False
    # A partial record is valid only when its last evaluated gate blocked the
    # chain.  A full record is valid only when all nine gates passed or the
    # ninth gate itself blocked the decision.
    return all(status == "pass" for status in statuses[:-1]) and (
        statuses[-1] != "pass" or len(statuses) == len(expected)
    )


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
    """Run the authoritative nine-step Boolean buy-decision chain."""

    def run(
        self,
        symbol: str,
        pre_fetched_data: dict[str, Any] | None = None,
        *,
        save_to_db: bool = True,
        thesis: str = "",
        thesis_context: dict[str, Any] | None = None,
    ) -> list[CriterionResult]:
        """Run all evaluators. Returns list of results (may be partial if early-terminated).

        If ``save_to_db`` is True (default), results are persisted for today's date
        so the frontend page can reuse them via cache.
        """
        from src.services.buy_criteria.data_service import _clear_cache

        _clear_cache()
        results = self._run_evaluators(
            symbol,
            pre_fetched_data,
            thesis=thesis,
            thesis_context=thesis_context,
        )

        if save_to_db and results:
            self._save_results(symbol, results)

        return results

    def analyze_for_agent(
        self,
        symbol: str,
        *,
        thesis: str = "",
        thesis_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a fresh fail-fast decision envelope for the conversational Agent.

        Agent requests are never served from the date-only page cache because
        both the referenced investment thesis and the current entry position
        can change between questions on the same day.
        """
        from src.services.buy_criteria.data_service import _clear_cache

        _clear_cache()
        results = self._run_evaluators(
            symbol,
            None,
            thesis=thesis,
            thesis_context=thesis_context,
        )
        summary = self._build_batch_summary(
            [result.to_dict() for result in results],
            from_cache=False,
        )
        summary["symbol"] = symbol
        summary["thesis"] = thesis or None
        summary["thesis_context"] = thesis_context
        return summary

    def analyze_for_batch(
        self,
        symbol: str,
        *,
        reuse_cache: bool = True,
    ) -> dict[str, Any]:
        """Run (or reuse cached) buy-criteria analysis for one stock in a batch.

        Unlike :meth:`make_sse_endpoint`, this is a blocking call that returns a
        normalized summary dict so the batch runner can map it onto a per-stock
        result. When ``reuse_cache`` is True and today's record exists, it is
        reused without any LLM call.
        """
        trade_date = date_type.today()

        if reuse_cache:
            from src.storage import get_db

            try:
                cached = get_db().get_buy_criteria_record(symbol, trade_date)
            except Exception as exc:
                logger.warning("[buy_criteria] cache lookup failed for %s: %s", symbol, exc)
                cached = None
            if _cache_matches_current_contract(cached):
                logger.info("[buy_criteria] batch reusing cached result for %s", symbol)
                return self._build_batch_summary(cached.get("results", []), from_cache=True)

        results = self.run(symbol, save_to_db=True)
        return self._build_batch_summary(
            [r.to_dict() for r in results], from_cache=False
        )

    @staticmethod
    def _build_batch_summary(
        result_dicts: list[dict[str, Any]],
        *,
        from_cache: bool,
    ) -> dict[str, Any]:
        """Normalize a list of criterion result dicts into a batch summary."""
        total = len(EVALUATOR_CLASSES)
        criteria = [
            {
                "criterion_id": r.get("criterion_id"),
                "criterion_name": r.get("criterion_name"),
                "index": r.get("index"),
                "passed": bool(r.get("passed")),
                "status": str(
                    r.get("status") or ("pass" if r.get("passed") else "fail")
                ),
                "confidence": str(r.get("confidence") or ""),
                "verdict": str(r.get("verdict") or ""),
                "details": r.get("details") if isinstance(r.get("details"), dict) else {},
            }
            for r in result_dicts
        ]
        passed_count = sum(1 for c in criteria if c["status"] == "pass")
        failed_count = sum(1 for c in criteria if c["status"] == "fail")
        insufficient_count = sum(
            1 for c in criteria if c["status"] == "insufficient"
        )
        not_evaluated = max(0, total - len(criteria))
        stopped = next((c for c in criteria if c["status"] != "pass"), None)
        coverage_complete = len(criteria) == total
        if coverage_complete and passed_count == total:
            final_decision = "可买入"
        else:
            final_decision = "不可买入"
        entry_details = (
            criteria[-1].get("details")
            if criteria and criteria[-1].get("criterion_id") == "entry_risk_reward"
            else {}
        )
        return {
            "final_decision": final_decision,
            "passed_count": passed_count,
            "failed_count": failed_count,
            "insufficient_count": insufficient_count,
            "not_evaluated_count": not_evaluated,
            "total": total,
            "stopped_at": stopped["criterion_id"] if stopped else None,
            "stopped_at_name": stopped["criterion_name"] if stopped else None,
            "stopped_verdict": stopped["verdict"] if stopped else "",
            "blocking_reasons": ([{
                "criterion_id": stopped["criterion_id"],
                "criterion_name": stopped["criterion_name"],
                "status": stopped["status"],
                "verdict": stopped["verdict"],
            }] if stopped else []),
            "criteria": criteria,
            "coverage_complete": coverage_complete,
            "position_advice": {
                "initial_position_pct": entry_details.get("recommended_initial_position_pct", 0),
                "max_position_pct": entry_details.get("recommended_max_position_pct", 0),
            },
            "invalidation_conditions": entry_details.get("invalidation_conditions", []),
            "from_cache": from_cache,
        }

    def _run_evaluators(
        self,
        symbol: str,
        pre_fetched_data: dict[str, Any] | None = None,
        *,
        thesis: str = "",
        thesis_context: dict[str, Any] | None = None,
    ) -> list[CriterionResult]:
        """Evaluate in fixed order and stop immediately at the first false gate."""
        stock_info = dict(_get_stock_info_safe(symbol))
        stock_info["_investment_thesis"] = str(thesis or "").strip()
        stock_info["_investment_thesis_context"] = thesis_context
        results: list[CriterionResult] = []

        for evaluator_cls in EVALUATOR_CLASSES:
            evaluator = evaluator_cls()
            result = evaluator.evaluate(symbol, stock_info, pre_fetched_data)
            results.append(result)
            if not result.passed:
                break

        return results

    @staticmethod
    def _save_results(symbol: str, results: list[CriterionResult]) -> None:
        """Persist evaluation results to DB for today's date."""
        trade_date = date_type.today()
        passed_count = sum(1 for r in results if r.passed)
        failed_count = sum(1 for r in results if not r.passed)
        total = len(EVALUATOR_CLASSES)
        not_evaluated = total - len(results)
        stopped_at = next((r.criterion_id for r in results if not r.passed), None)
        final_decision = "可买入" if passed_count == total and failed_count == 0 else "不可买入"
        summary_parts = [f"{passed_count}项通过"]
        if failed_count:
            summary_parts.append(f"{failed_count}项未通过")
        summary = "，".join(summary_parts) + ("，可买入" if passed_count == total else "，不可买入")

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
                symbol, final_decision, passed_count, total,
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

                if _cache_matches_current_contract(cached):
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
                total = len(EVALUATOR_CLASSES)
                not_evaluated = total - len(results)
                stopped_at = next((r.criterion_id for r in results if not r.passed), None)
                final_decision = "可买入" if passed_count == total and failed_count == 0 else "不可买入"
                summary_parts = [f"{passed_count}项通过"]
                if failed_count:
                    summary_parts.append(f"{failed_count}项未通过")
                summary = "，".join(summary_parts) + ("，可买入" if passed_count == total else "，不可买入")

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
