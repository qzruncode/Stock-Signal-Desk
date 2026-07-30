"""Program-owned orchestration for the user-defined eight buy dimensions."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import date as date_type
from typing import Any, Callable

from src.services.buy_criteria.base import CriterionEvidence, CriterionResult
from src.services.buy_criteria.professional_analysis import (
    DIMENSION_DEFINITIONS,
    PROFESSIONAL_BUY_ANALYSIS_MODE,
    PROFESSIONAL_BUY_CONTRACT_VERSION,
    analyze_professional_buy,
    resolve_investment_thesis,
)
from src.services.buy_criteria.mainline_policy import (
    MainlineStrategyProfile,
    normalize_mainline_strategy,
)

logger = logging.getLogger(__name__)

BUY_GATE_CONTRACT_VERSION = PROFESSIONAL_BUY_CONTRACT_VERSION
_DIMENSION_IDS = tuple(item[0] for item in DIMENSION_DEFINITIONS)


def _format_sse(event_type: str, data: dict[str, Any]) -> str:
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


def _get_stock_info_safe(symbol: str) -> dict[str, Any]:
    try:
        from api.v1.endpoints.stock_info import get_stock_info

        result = get_stock_info(symbol)
        return dict(result) if isinstance(result, dict) else {"symbol": symbol}
    except Exception as exc:
        logger.error(
            "[buy_criteria] failed to get stock_info for %s: %s",
            symbol,
            exc,
        )
        return {"symbol": symbol, "name": symbol, "industry": ""}


def _cache_matches_current_contract(cached: dict[str, Any] | None) -> bool:
    """Accept only a valid fail-fast prefix of the current eight dimensions."""
    if not isinstance(cached, dict):
        return False
    results = [
        item
        for item in cached.get("results") or []
        if isinstance(item, dict)
    ]
    if not results or len(results) > len(_DIMENSION_IDS):
        return False
    actual_ids = tuple(str(item.get("criterion_id") or "") for item in results)
    if actual_ids != _DIMENSION_IDS[:len(actual_ids)]:
        return False
    statuses = tuple(str(item.get("status") or "") for item in results)
    if any(status not in {"pass", "fail", "insufficient"} for status in statuses):
        return False
    return (
        all(status == "pass" for status in statuses[:-1])
        and (
            statuses[-1] != "pass"
            or len(statuses) == len(_DIMENSION_IDS)
        )
    )


def _analysis_to_results(analysis: dict[str, Any]) -> list[CriterionResult]:
    dimensions = {
        str(item.get("dimension_id") or ""): item
        for item in analysis.get("dimensions") or []
        if isinstance(item, dict)
    }
    results: list[CriterionResult] = []
    for index, (dimension_id, title) in enumerate(DIMENSION_DEFINITIONS):
        item = dimensions.get(dimension_id)
        if not isinstance(item, dict):
            break
        status = str(item.get("status") or "insufficient")
        if status == "not_evaluated":
            break
        if status not in {"pass", "fail", "insufficient"}:
            status = "insufficient"
        results.append(CriterionResult(
            criterion_id=dimension_id,
            criterion_name=title,
            index=index,
            passed=status == "pass",
            status=status,
            confidence="",
            verdict=str(
                item.get("analysis")
                or item.get("headline")
                or "当前维度未返回有效结论"
            ),
            evidence=CriterionEvidence(
                raw_data={
                    "key_evidence": item.get("key_evidence") or [],
                    "counter_evidence": item.get("counter_evidence") or [],
                },
                data_summary=str(item.get("headline") or ""),
            ),
            details={
                "headline": item.get("headline"),
                "evaluated_subjects": item.get("evaluated_subjects") or [],
                "key_evidence": item.get("key_evidence") or [],
                "counter_evidence": item.get("counter_evidence") or [],
                "monitoring_points": item.get("monitoring_points") or [],
                "mainline_classification": item.get(
                    "mainline_classification"
                ),
            },
        ))
        if status != "pass":
            break
    return results


def _build_summary(
    result_dicts: list[dict[str, Any]],
    *,
    from_cache: bool,
    analysis_status: str | None = None,
    model_error: str | None = None,
) -> dict[str, Any]:
    criteria = [
        {
            "criterion_id": item.get("criterion_id"),
            "criterion_name": item.get("criterion_name"),
            "index": item.get("index"),
            "passed": (
                str(item.get("status") or "") == "pass"
                and bool(item.get("passed"))
            ),
            "status": str(
                item.get("status")
                or ("pass" if item.get("passed") else "fail")
            ),
            "confidence": str(item.get("confidence") or ""),
            "verdict": str(item.get("verdict") or ""),
            "details": (
                item.get("details")
                if isinstance(item.get("details"), dict)
                else {}
            ),
        }
        for item in result_dicts
        if isinstance(item, dict)
    ]
    total = len(DIMENSION_DEFINITIONS)
    passed_count = sum(item["status"] == "pass" for item in criteria)
    failed_count = sum(item["status"] == "fail" for item in criteria)
    insufficient_count = sum(
        item["status"] == "insufficient"
        for item in criteria
    )
    stopped = next(
        (item for item in criteria if item["status"] != "pass"),
        None,
    )
    all_passed = len(criteria) == total and passed_count == total
    resolved_analysis_status = analysis_status or (
        "execution_failed"
        if model_error
        else "source_unavailable"
        if insufficient_count
        else "completed"
    )
    final_decision = (
        "分析失败"
        if resolved_analysis_status == "execution_failed"
        else "分析未完成"
        if resolved_analysis_status == "source_unavailable"
        else "可买入"
        if all_passed
        else "不可买入"
    )
    return {
        "contract_version": BUY_GATE_CONTRACT_VERSION,
        "analysis_mode": PROFESSIONAL_BUY_ANALYSIS_MODE,
        "analysis_status": resolved_analysis_status,
        "final_decision": final_decision,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "insufficient_count": insufficient_count,
        "not_evaluated_count": max(0, total - len(criteria)),
        "total": total,
        "stopped_at": stopped.get("criterion_id") if stopped else None,
        "stopped_at_name": stopped.get("criterion_name") if stopped else None,
        "stopped_verdict": stopped.get("verdict") if stopped else "",
        "blocking_reasons": [stopped] if stopped else [],
        "criteria": criteria,
        "analysis_complete": bool(criteria),
        "coverage_complete": (
            resolved_analysis_status == "completed"
            and bool(criteria)
            and (
                all_passed
                or (stopped is not None and stopped["status"] == "fail")
            )
        ),
        "gate_pass_complete": all_passed,
        "from_cache": from_cache,
    }


class CriterionOrchestrator:
    """Expose one authoritative eight-dimension Boolean state machine."""

    def run(
        self,
        symbol: str,
        pre_fetched_data: dict[str, Any] | None = None,
        *,
        save_to_db: bool = True,
        thesis: str = "",
        thesis_context: dict[str, Any] | None = None,
        mainline_strategy: MainlineStrategyProfile | str = (
            MainlineStrategyProfile.CONFIRMED_MAINLINE
        ),
    ) -> list[CriterionResult]:
        analysis = analyze_professional_buy(
            symbol,
            thesis=thesis,
            thesis_context=thesis_context,
            mainline_strategy=mainline_strategy,
            pre_fetched_data=pre_fetched_data,
        )
        results = _analysis_to_results(analysis)
        if save_to_db and results:
            self._save_results(symbol, results)
        return results

    def analyze_for_agent(
        self,
        symbol: str,
        *,
        thesis: str = "",
        thesis_context: dict[str, Any] | None = None,
        mainline_strategy: MainlineStrategyProfile | str = (
            MainlineStrategyProfile.CONFIRMED_MAINLINE
        ),
        pre_fetched_data: dict[str, Any] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        analysis = analyze_professional_buy(
            symbol,
            thesis=thesis,
            thesis_context=thesis_context,
            mainline_strategy=mainline_strategy,
            pre_fetched_data=pre_fetched_data,
            on_reasoning=on_reasoning,
        )
        results = _analysis_to_results(analysis)
        summary = _build_summary(
            [result.to_dict() for result in results],
            from_cache=False,
            analysis_status=str(analysis.get("analysis_status") or ""),
            model_error=str(analysis.get("model_error") or ""),
        )
        return {
            **analysis,
            **summary,
            "symbol": symbol,
            "thesis": resolve_investment_thesis(
                thesis,
                thesis_context,
            ) or None,
            "thesis_context": thesis_context,
            "mainline_strategy": normalize_mainline_strategy(
                mainline_strategy
            ).value,
        }

    def analyze_for_batch(
        self,
        symbol: str,
        *,
        reuse_cache: bool = True,
    ) -> dict[str, Any]:
        if reuse_cache:
            from src.storage import get_db

            try:
                cached = get_db().get_buy_criteria_record(
                    symbol,
                    date_type.today(),
                )
            except Exception as exc:
                logger.warning(
                    "[buy_criteria] cache lookup failed for %s: %s",
                    symbol,
                    exc,
                )
                cached = None
            if _cache_matches_current_contract(cached):
                return _build_summary(
                    cached.get("results") or [],
                    from_cache=True,
                )
        results = self.run(symbol, save_to_db=True)
        return _build_summary(
            [result.to_dict() for result in results],
            from_cache=False,
        )

    @staticmethod
    def _build_batch_summary(
        result_dicts: list[dict[str, Any]],
        *,
        from_cache: bool,
    ) -> dict[str, Any]:
        return _build_summary(result_dicts, from_cache=from_cache)

    @staticmethod
    def _save_results(
        symbol: str,
        results: list[CriterionResult],
    ) -> None:
        summary = _build_summary(
            [result.to_dict() for result in results],
            from_cache=False,
        )
        stock_name = _get_stock_info_safe(symbol).get("name") or symbol
        try:
            from src.storage import get_db

            get_db().save_buy_criteria_record(
                symbol=symbol,
                trade_date=date_type.today(),
                stock_name=stock_name,
                final_decision=summary["final_decision"],
                passed_count=summary["passed_count"],
                failed_count=(
                    summary["failed_count"]
                    + summary["insufficient_count"]
                ),
                not_evaluated_count=summary["not_evaluated_count"],
                stopped_at=summary["stopped_at"],
                summary=(
                    f"{summary['passed_count']}项通过，"
                    f"{summary['failed_count'] + summary['insufficient_count']}项阻断，"
                    f"{summary['not_evaluated_count']}项未执行，"
                    f"{summary['final_decision']}"
                ),
                results=[result.to_dict() for result in results],
            )
        except Exception as exc:
            logger.error(
                "[buy_criteria] failed to save record for %s: %s",
                symbol,
                exc,
            )

    @staticmethod
    def make_sse_endpoint(
        symbol: str,
        pre_fetched_data: dict[str, Any] | None = None,
    ):
        from fastapi.responses import StreamingResponse

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        done = object()

        def worker() -> None:
            try:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("connected", {
                        "cached": False,
                        "contract_version": BUY_GATE_CONTRACT_VERSION,
                    }),
                )
                results = CriterionOrchestrator().run(
                    symbol,
                    pre_fetched_data,
                    save_to_db=True,
                )
                for result in results:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("criterion_complete", result.to_dict()),
                    )
                summary = _build_summary(
                    [result.to_dict() for result in results],
                    from_cache=False,
                )
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("analysis_complete", summary),
                )
            except Exception as exc:
                logger.error(
                    "[buy_criteria] orchestrator error: %s",
                    exc,
                    exc_info=True,
                )
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("error", {"criterion_id": "", "message": str(exc)}),
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, (done, None))

        threading.Thread(target=worker, daemon=True).start()

        async def event_generator():
            while True:
                try:
                    event_type, data = await asyncio.wait_for(
                        queue.get(),
                        timeout=15,
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event_type is done:
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


__all__ = [
    "BUY_GATE_CONTRACT_VERSION",
    "CriterionOrchestrator",
    "_cache_matches_current_contract",
    "_format_sse",
    "_get_stock_info_safe",
]
