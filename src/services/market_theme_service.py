"""Market mainline analysis service — facade delegating to sub-modules."""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

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
    generate_model_report_inline,
    generate_model_report_stream,
)
from .market_theme._utils import cache_get, cache_put

logger = logging.getLogger(__name__)

_report_generation_lock = threading.RLock()


def _cache_is_usable(payload: Optional[dict[str, Any]]) -> bool:
    """A degraded fallback must never suppress the next recovery attempt."""
    return bool(payload) and not bool(payload.get("degraded_reason"))


def _model_report_is_ready(payload: Optional[dict[str, Any]]) -> bool:
    """A shared batch snapshot exists only when it has usable mainline rows."""
    return bool(
        payload
        and not payload.get("report_pending")
        and payload.get("as_of_date")
        and (
            payload.get("current_mainlines")
            or payload.get("candidate_mainlines")
            or payload.get("future_mainlines")
        )
    )


def _task_snapshot(task: Any) -> dict[str, Any] | None:
    if task is None:
        return None
    status = getattr(task, "status", None)
    status_value = getattr(status, "value", status)
    return {
        "task_id": str(getattr(task, "task_id", "") or ""),
        "status": str(status_value or ""),
        "progress": int(getattr(task, "progress", 0) or 0),
        "message": str(getattr(task, "message", "") or ""),
        "error": (
            str(getattr(task, "error", "") or "")
            or None
        ),
    }


class MarketThemeService:
    """Build a market-mainline analysis using public market and information sources."""

    REPORT_KEY = "market_mainline"

    def analyze(self, *, force: bool = False, use_llm: bool = True) -> dict[str, Any]:
        if not force:
            cached = cache_get("all")
            if _cache_is_usable(cached):
                cached["_cached"] = True
                return cached

        result = run_isolated(force=force, layer="all")
        if result is None:
            cached = cache_get("all")
            if _cache_is_usable(cached):
                cached["_cached"] = True
                cached.setdefault("degraded_reason", "isolated_runner_failed")
                return cached

            result = build_minimal_fallback()

        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        result.setdefault("data_time", datetime.now().date().isoformat())
        result.setdefault("llm_used", False)
        result.setdefault("model_used", None)
        result["fallback_used"] = not bool(result.get("llm_used"))
        if use_llm and result.get("semantic_status") != "completed":
            result["report_pending"] = True
        if not result.get("_cached") and not result.get("degraded_reason"):
            cache_put("all", result)
        return result

    def get_model_report(
        self,
        *,
        force: bool = False,
        trigger_generation: bool = False,
    ) -> dict[str, Any]:
        latest = None if force else get_latest_report(self.REPORT_KEY)
        if _model_report_is_ready(latest):
            latest["_cached"] = True
            latest["report_pending"] = False
            latest["llm_used"] = bool(latest.get("llm_used"))
            latest.setdefault("model_used", None)
            return latest

        generation_task = None
        if trigger_generation:
            with _report_generation_lock:
                latest = None if force else get_latest_report(self.REPORT_KEY)
                if _model_report_is_ready(latest):
                    latest["_cached"] = True
                    latest["report_pending"] = False
                    latest["llm_used"] = bool(latest.get("llm_used"))
                    latest.setdefault("model_used", None)
                    return latest
                try:
                    generation_task = self.submit_model_report_task(force=True)
                    logger.info(
                        "[MarketTheme] Ensured market mainline report generation task=%s",
                        getattr(generation_task, "task_id", None),
                    )
                except Exception:
                    logger.exception(
                        "[MarketTheme] Failed to ensure report generation"
                    )

        payload = build_minimal_model_report()
        payload["_cached"] = False
        payload["llm_used"] = False
        payload["model_used"] = None
        payload["report_pending"] = True
        task_payload = _task_snapshot(generation_task)
        if task_payload:
            payload["generation_task"] = task_payload
        return payload

    def ensure_model_report(
        self,
        *,
        force: bool = False,
        poll_interval_seconds: float = 0.5,
    ) -> dict[str, Any]:
        """Wait for one current report so a portfolio uses one frozen baseline."""
        initial = self.get_model_report(
            force=force,
            trigger_generation=True,
        )
        if _model_report_is_ready(initial):
            return initial

        task_payload = (
            initial.get("generation_task")
            if isinstance(initial.get("generation_task"), dict)
            else {}
        )
        task_id = str(task_payload.get("task_id") or "")
        latest_task_payload = dict(task_payload)

        from src.services.task_queue import TaskStatus, get_task_queue

        task_queue = get_task_queue()
        while True:
            latest = get_latest_report(self.REPORT_KEY)
            if _model_report_is_ready(latest):
                latest["_cached"] = True
                latest["report_pending"] = False
                latest["llm_used"] = bool(latest.get("llm_used"))
                latest.setdefault("model_used", None)
                if latest_task_payload:
                    latest["generation_task"] = latest_task_payload
                return latest

            task = task_queue.get_task(task_id) if task_id else None
            snapshot = _task_snapshot(task)
            if snapshot:
                latest_task_payload = snapshot
            if task is not None and task.status == TaskStatus.FAILED:
                return {
                    **build_minimal_model_report(),
                    "_cached": False,
                    "llm_used": False,
                    "model_used": None,
                    "report_pending": True,
                    "generation_task": latest_task_payload,
                    "generation_failed": True,
                }
            if task is not None and task.status == TaskStatus.COMPLETED:
                return {
                    **build_minimal_model_report(),
                    "_cached": False,
                    "llm_used": False,
                    "model_used": None,
                    "report_pending": True,
                    "generation_task": latest_task_payload,
                    "generation_failed": True,
                    "generation_error": (
                        "市场主线生成任务已结束，但未持久化可用报告"
                    ),
                }
            time.sleep(max(0.05, float(poll_interval_seconds)))

    def ensure_model_report_inline(
        self,
        *,
        force: bool = False,
        on_progress: Callable[[int, str, str | None], None] | None = None,
    ) -> dict[str, Any]:
        """Return one frozen report without escaping into the global task queue."""
        latest = None if force else get_latest_report(self.REPORT_KEY)
        if _model_report_is_ready(latest):
            latest["_cached"] = True
            latest["report_pending"] = False
            latest["llm_used"] = bool(latest.get("llm_used"))
            latest.setdefault("model_used", None)
            if on_progress:
                on_progress(100, "已复用当日市场主线快照", None)
            return latest

        with _report_generation_lock:
            latest = None if force else get_latest_report(self.REPORT_KEY)
            if _model_report_is_ready(latest):
                latest["_cached"] = True
                latest["report_pending"] = False
                latest["llm_used"] = bool(latest.get("llm_used"))
                latest.setdefault("model_used", None)
                if on_progress:
                    on_progress(100, "已复用当日市场主线快照", None)
                return latest
            result = generate_model_report_inline(
                force=force,
                on_progress=on_progress,
            )
            result["_cached"] = False
            result["report_pending"] = False
            return result

    def get_cached_evidence(self) -> dict[str, Any] | None:
        """Return the current evidence cache without starting a slow fetch."""
        cached = cache_get("evidence")
        if not _cache_is_usable(cached):
            return None
        cached["_cached"] = True
        return cached

    def get_model_report_for_tool(self, *, include_debug_input: bool = False) -> dict[str, Any]:
        payload = dict(self.get_model_report(force=False, trigger_generation=False))
        if not include_debug_input:
            payload.pop("debug_input", None)
        return payload

    def submit_model_report_task(self, *, force: bool = True):
        from src.services.task_queue import get_task_queue

        task_queue = get_task_queue()
        with _report_generation_lock:
            active = next(
                (
                    task
                    for task in task_queue.list_pending_tasks()
                    if task.stock_code == "MARKET_MAINLINE"
                    and task.report_type == "market_mainline_report"
                ),
                None,
            )
            if active is not None:
                return active

            task_id = __import__("uuid").uuid4().hex

            def _run_task() -> dict[str, Any]:
                return generate_model_report_stream(
                    force=force, task_queue=task_queue, task_id=task_id,
                )

            return task_queue.submit_background_task(
                _run_task,
                stock_code="MARKET_MAINLINE",
                stock_name="市场主线",
                report_type="market_mainline_report",
                message="市场主线模型研判任务已加入队列",
                task_id=task_id,
            )

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

        payload = run_isolated(layer="report_llm", force=force)
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
        result = build_summary_response(
            context,
            model_report=get_latest_report(self.REPORT_KEY),
        )
        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        if not result.get("_cached"):
            cache_put("summary", result)
        return result

    def get_evidence(self, *, force: bool = False) -> dict[str, Any]:
        if not force:
            cached = cache_get("evidence")
            if _cache_is_usable(cached):
                cached["_cached"] = True
                return cached

        result = run_isolated(force=force, layer="evidence")
        if result is None:
            cached = cache_get("evidence")
            if _cache_is_usable(cached):
                cached["_cached"] = True
                cached.setdefault("degraded_reason", "isolated_runner_failed")
                return cached
            result = build_minimal_evidence_fallback()

        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        if not result.get("_cached") and not result.get("degraded_reason"):
            cache_put("evidence", result)
        return result

    def get_insight(self, *, force: bool = False, use_llm: bool = False) -> dict[str, Any]:
        cache_layer = "insight_llm" if use_llm else "insight"
        if not force:
            cached = cache_get(cache_layer)
            if _cache_is_usable(cached):
                cached["_cached"] = True
                cached.setdefault("llm_used", use_llm)
                return cached

        if use_llm:
            result = run_isolated(force=force, layer="insight_llm")
            if result is None:
                cached = cache_get(cache_layer)
                if _cache_is_usable(cached):
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
                if _cache_is_usable(cached):
                    cached["_cached"] = True
                    cached.setdefault("llm_used", use_llm)
                    cached.setdefault("degraded_reason", "isolated_runner_failed")
                    return cached
                evidence = self.get_evidence(force=force)
                result = build_insight_response(evidence)

        result["_fetched_at"] = datetime.now().isoformat()
        result.setdefault("_cached", False)
        result.setdefault("llm_used", False)
        if not result.get("_cached") and not result.get("degraded_reason"):
            cache_put(cache_layer, result)
        return result


def run_public_analysis(force: bool = False) -> dict[str, Any]:
    context = collect_context(force=force, include_rss=True)
    result = build_response(
        context,
        model_report=get_latest_report(MarketThemeService.REPORT_KEY),
    )
    result["_cached"] = False
    result["data_time"] = context["source_snapshot"]["market_status"].get("data_time")
    result.setdefault("llm_used", False)
    result.setdefault("model_used", None)
    result["fallback_used"] = not bool(result.get("llm_used"))
    return result


def run_public_evidence(force: bool = False) -> dict[str, Any]:
    context = collect_context(force=force, include_rss=True)
    result = build_evidence_response(context)
    result["_cached"] = False
    result["data_time"] = context["source_snapshot"]["market_status"].get("data_time")
    return result


def run_public_insight(force: bool = False) -> dict[str, Any]:
    evidence = run_public_evidence(force=force)
    report = get_latest_report(MarketThemeService.REPORT_KEY) or {}
    result = build_insight_response({**evidence, **report})
    result["_cached"] = False
    result["data_time"] = evidence.get("data_time")
    return result


def run_public_insight_llm(force: bool = False) -> dict[str, Any]:
    context = collect_context(force=force, include_rss=True)
    report = build_llm_model_report(context)
    evidence = build_evidence_response(context)
    result = build_insight_response({**evidence, **(report or {})})
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
