"""Orchestrate the authoritative professional buy-analysis workflow."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import date as date_type
from typing import Any

from src.services.buy_criteria.base import CriterionEvidence, CriterionResult
from src.services.buy_criteria.professional_analysis import (
    DIMENSION_DEFINITIONS,
    PROFESSIONAL_BUY_CONTRACT_VERSION,
    analyze_professional_buy,
)

logger = logging.getLogger(__name__)


def _format_sse(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _get_stock_info_safe(symbol: str) -> dict[str, Any]:
    try:
        from api.v1.endpoints.stock_info import get_stock_info

        result = get_stock_info(symbol)
        return dict(result) if isinstance(result, dict) else {"symbol": symbol}
    except Exception as exc:
        logger.error("[buy_criteria] failed to get stock_info for %s: %s", symbol, exc)
        return {"symbol": symbol, "name": symbol, "industry": ""}


def _professional_meta(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        key: analysis.get(key)
        for key in (
            "contract_version",
            "analysis_mode",
            "symbol",
            "name",
            "thesis",
            "investment_profile",
            "overall_summary",
            "core_thesis",
            "biggest_issue",
            "recommendation_code",
            "recommendation",
            "recommendation_reason",
            "score",
            "score_total",
            "counts",
            "bull_case_chain",
            "risk_chain",
            "monitoring_points",
            "evidence_gaps",
            "source_links",
            "data_time",
            "quote_basis",
            "quote_is_intraday",
            "coverage_complete",
            "model_error",
        )
    }


def _analysis_to_results(analysis: dict[str, Any]) -> list[CriterionResult]:
    """Map the eight-axis response onto the persisted criterion record shape."""
    results: list[CriterionResult] = []
    meta = _professional_meta(analysis)
    dimensions = [
        item for item in analysis.get("dimensions") or [] if isinstance(item, dict)
    ]
    by_id = {
        str(item.get("dimension_id") or ""): item
        for item in dimensions
    }
    for index, (dimension_id, title) in enumerate(DIMENSION_DEFINITIONS):
        item = by_id.get(dimension_id) or {
            "dimension_id": dimension_id,
            "status": "insufficient",
            "headline": "该维度没有返回有效分析",
            "analysis": "程序没有取得这一维度的结构化结果。",
            "key_evidence": [],
            "counter_evidence": [],
            "monitoring_points": [f"重新核验“{title}”"],
        }
        status = str(item.get("status") or "insufficient")
        headline = str(item.get("headline") or "").strip()
        analysis_text = str(item.get("analysis") or "").strip()
        verdict = "。".join(
            value.rstrip("。")
            for value in (headline, analysis_text)
            if value
        )
        details = {
            "key_evidence": list(item.get("key_evidence") or []),
            "counter_evidence": list(item.get("counter_evidence") or []),
            "monitoring_points": list(item.get("monitoring_points") or []),
        }
        if index == 0:
            details["professional_summary"] = meta
        results.append(CriterionResult(
            criterion_id=dimension_id,
            criterion_name=title,
            index=index,
            passed=status == "pass",
            status=status,
            confidence="",
            verdict=verdict or "该维度没有返回有效分析",
            evidence=CriterionEvidence(),
            details=details,
        ))
    return results


def _cache_matches_current_contract(cached: dict[str, Any] | None) -> bool:
    if not isinstance(cached, dict):
        return False
    results = [item for item in cached.get("results") or [] if isinstance(item, dict)]
    if len(results) != len(DIMENSION_DEFINITIONS):
        return False
    expected = [item[0] for item in DIMENSION_DEFINITIONS]
    actual = [str(item.get("criterion_id") or "") for item in results]
    if actual != expected:
        return False
    statuses = [str(item.get("status") or "") for item in results]
    if any(
        status not in {"pass", "partial", "fail", "insufficient"}
        for status in statuses
    ):
        return False
    first_details = results[0].get("details")
    first_details = first_details if isinstance(first_details, dict) else {}
    meta = first_details.get("professional_summary")
    return (
        isinstance(meta, dict)
        and meta.get("contract_version") == PROFESSIONAL_BUY_CONTRACT_VERSION
    )


class CriterionOrchestrator:
    """Run one coherent eight-dimension analyst review per company."""

    def run(
        self,
        symbol: str,
        pre_fetched_data: dict[str, Any] | None = None,
        *,
        save_to_db: bool = True,
        thesis: str = "",
        thesis_context: dict[str, Any] | None = None,
    ) -> list[CriterionResult]:
        from src.services.buy_criteria.data_service import _clear_cache

        _clear_cache()
        analysis = analyze_professional_buy(
            symbol,
            thesis=thesis,
            thesis_context=thesis_context,
            pre_fetched_data=pre_fetched_data,
        )
        results = _analysis_to_results(analysis)
        if save_to_db:
            self._save_results(symbol, results)
        return results

    def analyze_for_agent(
        self,
        symbol: str,
        *,
        thesis: str = "",
        thesis_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from src.services.buy_criteria.data_service import _clear_cache

        _clear_cache()
        return analyze_professional_buy(
            symbol,
            thesis=thesis,
            thesis_context=thesis_context,
        )

    def analyze_for_batch(
        self,
        symbol: str,
        *,
        reuse_cache: bool = True,
    ) -> dict[str, Any]:
        trade_date = date_type.today()
        if reuse_cache:
            from src.storage import get_db

            try:
                cached = get_db().get_buy_criteria_record(symbol, trade_date)
            except Exception as exc:
                logger.warning("[buy_criteria] cache lookup failed for %s: %s", symbol, exc)
                cached = None
            if _cache_matches_current_contract(cached):
                return self._build_batch_summary(
                    cached.get("results") or [],
                    from_cache=True,
                )
        results = self.run(symbol, save_to_db=True)
        return self._build_batch_summary(
            [result.to_dict() for result in results],
            from_cache=False,
        )

    @staticmethod
    def _build_batch_summary(
        result_dicts: list[dict[str, Any]],
        *,
        from_cache: bool,
    ) -> dict[str, Any]:
        criteria = [
            {
                "criterion_id": item.get("criterion_id"),
                "criterion_name": item.get("criterion_name"),
                "index": item.get("index"),
                "passed": str(item.get("status") or "") == "pass",
                "status": str(item.get("status") or "insufficient"),
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
        counts = {
            status: sum(1 for item in criteria if item["status"] == status)
            for status in ("pass", "partial", "fail", "insufficient")
        }
        score = round(counts["pass"] + counts["partial"] * 0.5, 1)
        meta: dict[str, Any] = {}
        if criteria:
            candidate = criteria[0]["details"].get("professional_summary")
            if isinstance(candidate, dict):
                meta = candidate
        return {
            **meta,
            "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
            "analysis_mode": "professional_eight_dimension_buy_analysis",
            "final_decision": (
                meta.get("recommendation")
                or "证据不足，暂停判断"
            ),
            "recommendation_code": (
                meta.get("recommendation_code")
                or "evidence_insufficient"
            ),
            "passed_count": counts["pass"],
            "partial_count": counts["partial"],
            "failed_count": counts["fail"],
            "insufficient_count": counts["insufficient"],
            "not_evaluated_count": 0,
            "total": len(DIMENSION_DEFINITIONS),
            "score": score,
            "score_total": len(DIMENSION_DEFINITIONS),
            "counts": counts,
            "stopped_at": None,
            "stopped_at_name": None,
            "stopped_verdict": "",
            "blocking_reasons": [
                {
                    "criterion_id": item["criterion_id"],
                    "criterion_name": item["criterion_name"],
                    "status": item["status"],
                    "verdict": item["verdict"],
                }
                for item in criteria
                if item["status"] in {"fail", "insufficient"}
            ],
            "criteria": criteria,
            "dimensions": criteria,
            "coverage_complete": (
                len(criteria) == len(DIMENSION_DEFINITIONS)
                and counts["insufficient"] == 0
            ),
            "from_cache": from_cache,
        }

    @staticmethod
    def _save_results(symbol: str, results: list[CriterionResult]) -> None:
        from src.storage import get_db

        summary = CriterionOrchestrator._build_batch_summary(
            [result.to_dict() for result in results],
            from_cache=False,
        )
        stock_name = (
            summary.get("name")
            or _get_stock_info_safe(symbol).get("name")
            or symbol
        )
        try:
            get_db().save_buy_criteria_record(
                symbol=symbol,
                trade_date=date_type.today(),
                stock_name=stock_name,
                final_decision=summary["final_decision"],
                passed_count=summary["passed_count"],
                failed_count=(
                    summary["failed_count"] + summary["insufficient_count"]
                ),
                not_evaluated_count=0,
                stopped_at=None,
                summary=(
                    f"✅{summary['passed_count']}｜◐{summary['partial_count']}｜"
                    f"❌{summary['failed_count']}｜?{summary['insufficient_count']}，"
                    f"{summary['score']:g}/8，{summary['final_decision']}"
                ),
                results=[result.to_dict() for result in results],
            )
        except Exception as exc:
            logger.error("[buy_criteria] failed to save record for %s: %s", symbol, exc)

    @staticmethod
    def make_sse_endpoint(
        symbol: str,
        pre_fetched_data: dict[str, Any] | None = None,
    ):
        """Stream all eight completed dimensions; no dimension stops another."""
        from fastapi.responses import StreamingResponse

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        sentinel_done = object()

        def worker() -> None:
            try:
                trade_date = date_type.today()
                from src.storage import get_db

                cached = get_db().get_buy_criteria_record(symbol, trade_date)
                if _cache_matches_current_contract(cached):
                    result_dicts = cached.get("results") or []
                    from_cache = True
                else:
                    results = CriterionOrchestrator().run(
                        symbol,
                        pre_fetched_data,
                        save_to_db=True,
                    )
                    result_dicts = [result.to_dict() for result in results]
                    from_cache = False

                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("connected", {
                        "cached": from_cache,
                        "trade_date": trade_date.isoformat(),
                        "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
                    }),
                )
                for result in result_dicts:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("criterion_complete", result),
                    )
                summary = CriterionOrchestrator._build_batch_summary(
                    result_dicts,
                    from_cache=from_cache,
                )
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("analysis_complete", summary),
                )
            except Exception as exc:
                logger.error("[buy_criteria] orchestrator error: %s", exc, exc_info=True)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("error", {"criterion_id": "", "message": str(exc)}),
                )
            finally:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    (sentinel_done, None),
                )

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
                if event_type is sentinel_done:
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
    "CriterionOrchestrator",
    "_analysis_to_results",
    "_cache_matches_current_contract",
    "_format_sse",
    "_get_stock_info_safe",
]
