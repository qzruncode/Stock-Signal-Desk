"""Market mainline analysis service — facade delegating to sub-modules."""

from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime
from typing import Any, Optional

from src.storage import DatabaseManager

from .market_theme._analysis import (
    build_evidence_response,
    build_insight_response,
    build_response,
    build_summary_response,
    run_isolated,
)
from .market_theme._context import (
    collect_context,
    get_latest_report,
    build_minimal_evidence_fallback,
    build_minimal_fallback,
    build_minimal_model_report,
)
from .market_theme._llm import (
    build_llm_insight_response,
    build_llm_model_report,
)
from .market_theme._streaming import (
    build_llm_model_report_streaming,
    generate_model_report_stream,
)
from .market_theme._utils import cache_get, cache_put

logger = logging.getLogger(__name__)

_report_generation_lock = threading.Lock()
_report_generation_triggered = False


class MarketThemeService:
    """Build a market-mainline analysis using public market and information sources."""

    REPORT_KEY = "market_mainline"

    def analyze(self, *, force: bool = False, use_llm: bool = True) -> dict[str, Any]:
        del use_llm
        if not force:
            cached = cache_get("all")
            if cached:
                cached["_cached"] = True
                cached["llm_used"] = False
                cached["model_used"] = None
                cached["fallback_used"] = True
                return cached

        result = run_isolated(force=force, layer="all")
        if result is None:
            cached = cache_get("all")
            if cached:
                cached["_cached"] = True
                cached["llm_used"] = False
                cached["model_used"] = None
                cached["fallback_used"] = True
                cached.setdefault("degraded_reason", "isolated_runner_failed")
                return cached

            result = build_minimal_fallback()

        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        result.setdefault("data_time", datetime.now().date().isoformat())
        result["llm_used"] = False
        result["model_used"] = None
        result["fallback_used"] = True
        if not result.get("_cached"):
            cache_put("all", result)
        return result

    def get_model_report(self, *, force: bool = False) -> dict[str, Any]:
        del force
        latest = get_latest_report(self.REPORT_KEY)
        if latest:
            latest["_cached"] = True
            latest["report_pending"] = False
            latest["llm_used"] = bool(latest.get("llm_used"))
            latest.setdefault("model_used", None)
            return latest

        global _report_generation_triggered
        if not _report_generation_triggered:
            with _report_generation_lock:
                if not _report_generation_triggered:
                    try:
                        self.submit_model_report_task(force=True)
                        _report_generation_triggered = True
                        logger.info("[MarketTheme] Auto-triggered market mainline report generation")
                    except Exception:
                        logger.exception("[MarketTheme] Failed to auto-trigger report generation")

        payload = build_minimal_model_report()
        payload["_cached"] = False
        payload["llm_used"] = False
        payload["model_used"] = None
        payload["report_pending"] = True
        return payload

    def get_model_report_for_tool(self, *, include_debug_input: bool = False) -> dict[str, Any]:
        payload = dict(self.get_model_report(force=False))
        if not include_debug_input:
            payload.pop("debug_input", None)
        return payload

    def submit_model_report_task(self, *, force: bool = True):
        from src.services.task_queue import get_task_queue

        task_queue = get_task_queue()
        task_id = __import__("uuid").uuid4().hex

        def _run_task() -> dict[str, Any]:
            return generate_model_report_stream(
                force=force, task_queue=task_queue, task_id=task_id,
            )

        task_info = task_queue.submit_background_task(
            _run_task,
            stock_code="MARKET_MAINLINE",
            stock_name="市场主线",
            report_type="market_mainline_report",
            message="市场主线模型研判任务已加入队列",
            task_id=task_id,
        )
        return task_info

    def _generate_model_report_isolated_task(
        self,
        *,
        force: bool,
        task_queue: Any,
        task_id: str,
    ) -> dict[str, Any]:
        task_queue.update_task_progress(task_id, 5, "正在启动独立研判进程")
        task_queue.update_task_result(
            task_id,
            {
                "phase": "starting_subprocess",
                "stream_text": "",
                "report_draft": {},
            },
            progress=12,
            message="市场主线模型研判已切换到隔离进程执行",
        )

        payload = run_isolated(layer="report_llm", force=force, timeout=180)
        if payload is None:
            raise RuntimeError("独立研判进程异常退出，未生成报告")

        task_queue.update_task_result(
            task_id,
            {
                "phase": "finalizing",
                "stream_text": str(payload.get("full_report") or ""),
                "report_draft": {
                    "overview": payload.get("overview"),
                    "full_report": payload.get("full_report"),
                    "as_of_date": payload.get("as_of_date"),
                    "market_stage": payload.get("market_stage"),
                },
                "debug_input": payload.get("debug_input"),
            },
            progress=95,
            message="独立研判进程已完成，正在整理结果",
        )

        return {
            "phase": "completed",
            "stream_text": str(payload.get("full_report") or ""),
            "report_draft": {
                "overview": payload.get("overview"),
                "full_report": payload.get("full_report"),
                "as_of_date": payload.get("as_of_date"),
                "market_stage": payload.get("market_stage"),
            },
            "debug_input": payload.get("debug_input"),
            "report": payload,
            "llm_used": payload.get("llm_used", False),
            "model_used": payload.get("model_used"),
        }

    def get_summary(self, *, force: bool = False) -> dict[str, Any]:
        if not force:
            cached = cache_get("summary")
            if cached:
                cached["_cached"] = True
                return cached

        context = collect_context(force=force, include_rss=False)
        result = build_summary_response(context)
        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        if not result.get("_cached"):
            cache_put("summary", result)
        return result

    def get_evidence(self, *, force: bool = False) -> dict[str, Any]:
        if not force:
            cached = cache_get("evidence")
            if cached:
                cached["_cached"] = True
                return cached

        result = run_isolated(force=force, layer="evidence")
        if result is None:
            cached = cache_get("evidence")
            if cached:
                cached["_cached"] = True
                cached.setdefault("degraded_reason", "isolated_runner_failed")
                return cached
            result = build_minimal_evidence_fallback()

        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        if not result.get("_cached"):
            cache_put("evidence", result)
        return result

    def get_insight(self, *, force: bool = False, use_llm: bool = False) -> dict[str, Any]:
        cache_layer = "insight_llm" if use_llm else "insight"
        if not force:
            cached = cache_get(cache_layer)
            if cached:
                cached["_cached"] = True
                cached.setdefault("llm_used", use_llm)
                return cached

        if use_llm:
            result = run_isolated(force=force, layer="insight_llm")
            if result is None:
                cached = cache_get(cache_layer)
                if cached:
                    cached["_cached"] = True
                    cached.setdefault("llm_used", use_llm)
                    cached.setdefault("degraded_reason", "isolated_runner_failed")
                    return cached
                evidence = self.get_evidence(force=force)
                result = build_insight_response(evidence)
        else:
            result = run_isolated(force=force, layer="insight")
            if result is None:
                cached = cache_get(cache_layer)
                if cached:
                    cached["_cached"] = True
                    cached.setdefault("llm_used", use_llm)
                    cached.setdefault("degraded_reason", "isolated_runner_failed")
                    return cached
                evidence = self.get_evidence(force=force)
                result = build_insight_response(evidence)

        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        result.setdefault("llm_used", False)
        if not result.get("_cached"):
            cache_put(cache_layer, result)
        return result


def run_public_analysis(force: bool = False) -> dict[str, Any]:
    context = collect_context(force=force, include_rss=True)
    result = build_response(context)
    result["_cached"] = False
    result["data_time"] = context["source_snapshot"]["market_status"].get("data_time")
    result["llm_used"] = False
    result["model_used"] = None
    result["fallback_used"] = True
    return result


def run_public_evidence(force: bool = False) -> dict[str, Any]:
    context = collect_context(force=force, include_rss=True)
    result = build_evidence_response(context)
    result["_cached"] = False
    result["data_time"] = context["source_snapshot"]["market_status"].get("data_time")
    return result


def run_public_insight(force: bool = False) -> dict[str, Any]:
    evidence = run_public_evidence(force=force)
    result = build_insight_response(evidence)
    result["_cached"] = False
    result["data_time"] = evidence.get("data_time")
    return result


def run_public_insight_llm(force: bool = False) -> dict[str, Any]:
    evidence = run_public_evidence(force=force)
    result = build_llm_insight_response(evidence) or build_insight_response(evidence)
    result["_cached"] = False
    result["data_time"] = evidence.get("data_time")
    return result


def run_public_model_report(force: bool = False) -> dict[str, Any]:
    context = collect_context(force=force, include_rss=True)
    result = build_llm_model_report(context) or build_minimal_model_report()
    result["_cached"] = False
    result["data_time"] = context["source_snapshot"]["market_status"].get("data_time")
    return result


def _main() -> int:
    force = "--force" in sys.argv[1:]
    layer = "all"
    if "--layer" in sys.argv[1:]:
        try:
            layer = sys.argv[sys.argv.index("--layer") + 1]
        except (ValueError, IndexError):
            layer = "all"

    if layer == "summary":
        payload = MarketThemeService().get_summary(force=force)
    elif layer == "evidence":
        payload = run_public_evidence(force=force)
    elif layer == "insight":
        payload = run_public_insight(force=force)
    elif layer == "insight_llm":
        payload = run_public_insight_llm(force=force)
    elif layer == "report_llm":
        payload = run_public_model_report(force=force)
    else:
        payload = run_public_analysis(force=force)
    sys.stdout.write(f"{'__MARKET_THEME_JSON__='}{json.dumps(payload, ensure_ascii=False)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())