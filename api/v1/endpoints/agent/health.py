# -*- coding: utf-8 -*-
"""Agent data health assessment — staleness checks and fallback decisions."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from src.agent.run_registry import active_run_registry
from src.agent.orchestrator_v2.registry import migration_coverage
from src.agent.runtime_safety import (
    agent_production_issues,
    configured_worker_count,
    get_agent_runtime_limits,
    is_production_environment,
)
from src.auth import is_auth_enabled
from src.storage import DatabaseManager
from src.tools.registry import ToolRegistry
from src.tools.process_runner import execute_tool_isolated
from src.storage.migrations import SCHEMA_VERSION

logger = logging.getLogger(__name__)

_health_registry = ToolRegistry()
_DEPENDENCY_PROBE_TTL_SECONDS = 60.0
_dependency_probe_cache: Dict[str, Any] = {
    "checked_at": 0.0,
    "value": None,
}
_dependency_probe_lock = threading.Lock()


async def _live_dependency_probe() -> Dict[str, Any]:
    with _dependency_probe_lock:
        cached_at = float(_dependency_probe_cache.get("checked_at") or 0.0)
        cached_value = _dependency_probe_cache.get("value")
    if isinstance(cached_value, dict) and time.monotonic() - cached_at < _DEPENDENCY_PROBE_TTL_SECONDS:
        return {**cached_value, "cached": True}

    checks: Dict[str, Any] = {}
    try:
        import litellm
        from src.llm.anthropic_gateway import (
            build_litellm_kwargs,
            resolve_anthropic_gateway_config,
        )

        model_config = resolve_anthropic_gateway_config()
        kwargs = build_litellm_kwargs(
            model_config,
            stream=False,
            messages=[
                {
                    "role": "user",
                    "content": "Reply with OK only.",
                }
            ],
            temperature=0,
            max_tokens=4,
        )
        async with asyncio.timeout(8.0):
            await litellm.acompletion(**kwargs)
        checks["model_provider"] = {"ok": True}
    except Exception as exc:
        checks["model_provider"] = {
            "ok": False,
            "error": type(exc).__name__,
        }

    try:
        async with asyncio.timeout(10.0):
            result = await asyncio.to_thread(
                execute_tool_isolated,
                "get_market_status",
                {},
                deadline_seconds=8.0,
            )
        checks["market_data"] = {
            "ok": isinstance(result, dict) and result.get("success") is not False,
        }
    except Exception as exc:
        checks["market_data"] = {
            "ok": False,
            "error": type(exc).__name__,
        }

    value = {
        "ok": all(check.get("ok") is True for check in checks.values()),
        "cached": False,
        "checks": checks,
        "probed_at": datetime.now().astimezone().isoformat(),
    }
    with _dependency_probe_lock:
        _dependency_probe_cache["checked_at"] = time.monotonic()
        _dependency_probe_cache["value"] = value
    return value


def _comparison_datetime(value: datetime) -> datetime:
    """Normalize mixed ISO timestamps for safe ordering.

    Tool payloads legitimately contain both local timestamps without an
    offset (AKShare/pages) and offset-aware timestamps (RSSHub/ISO).  Treat a
    naive timestamp as local time and convert aware values to the same local
    zone before comparing them.
    """
    local_tz = datetime.now().astimezone().tzinfo
    if value.tzinfo is None:
        return value.replace(tzinfo=local_tz)
    return value.astimezone(local_tz)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for candidate in (text, text[:10]):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    for candidate, fmt in ((text[:10], "%Y-%m-%d"), (text[:8], "%Y%m%d")):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _latest_date_from_items(items: Any, keys: List[str]) -> datetime | None:
    if not isinstance(items, list):
        return None
    latest: datetime | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            dt = _parse_iso_datetime(item.get(key))
            if dt and (latest is None or _comparison_datetime(dt) > _comparison_datetime(latest)):
                latest = dt
    return latest


def _assess_tool_data_health(tool_name: str, result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"should_fallback": False, "reason": None}

    if result.get("error"):
        return {"should_fallback": True, "reason": "tool_error"}

    if result.get("is_stale") is True:
        return {
            "should_fallback": True,
            "reason": f"stale_{tool_name}",
            "latest_date": result.get("data_time"),
        }

    if tool_name == "get_realtime_quotes":
        items = result.get("items") or []
        if not items:
            return {"should_fallback": True, "reason": "empty_quotes"}
        return {"should_fallback": False, "reason": None}

    if tool_name in {"get_kline", "get_history_data"}:
        series = result.get("recent") or result.get("data") or []
        if not series:
            return {"should_fallback": True, "reason": "empty_kline"}
        if result.get("data_time"):
            latest = _parse_iso_datetime(result.get("data_time"))
            if latest and latest.date() < (datetime.now().date() - timedelta(days=7)):
                return {"should_fallback": True, "reason": "stale_kline", "latest_date": latest.date().isoformat()}
        latest = _latest_date_from_items(series, ["date"])
        if latest and latest.date() < (datetime.now().date() - timedelta(days=7)):
            return {"should_fallback": True, "reason": "stale_kline", "latest_date": latest.date().isoformat()}
        return {"should_fallback": False, "reason": None}

    if tool_name in {
        "search_news",
        "get_announcements",
        "get_risk_events",
        "get_research_report",
        "get_social_sentiment",
    }:
        items = result.get("items") or []
        if not items:
            return {"should_fallback": True, "reason": "empty_news_family"}
        if result.get("data_time"):
            latest = _parse_iso_datetime(result.get("data_time"))
            days = int(result.get("days") or 30)
            cutoff = datetime.now().astimezone() - timedelta(days=max(days, 1))
            if latest and _comparison_datetime(latest) < cutoff:
                return {
                    "should_fallback": True,
                    "reason": "stale_news_family",
                    "latest_date": latest.date().isoformat(),
                }
        latest = _latest_date_from_items(items, ["publish_time", "publish_date", "date_str"])
        days = int(result.get("days") or 30)
        cutoff = datetime.now().astimezone() - timedelta(days=max(days, 1))
        if latest and _comparison_datetime(latest) < cutoff:
            return {"should_fallback": True, "reason": "stale_news_family", "latest_date": latest.date().isoformat()}
        return {"should_fallback": False, "reason": None}

    return {"should_fallback": False, "reason": None}


@router.get("/agent/readiness")
async def agent_readiness(
    request: Request,
    deep: bool = Query(False),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """Return a non-secret production readiness and runtime-capacity snapshot."""
    checks: Dict[str, Any] = {}
    try:
        with db_manager.get_session() as session:
            session.execute(text("SELECT 1"))
            schema_version = session.execute(text("SELECT value FROM _meta WHERE key = 'schema_version'")).scalar()
        checks["database"] = {
            "ok": schema_version == SCHEMA_VERSION,
            "schema_version": schema_version,
            "expected_schema_version": SCHEMA_VERSION,
        }
    except Exception as exc:
        logger.exception("[AgentReadiness] database check failed")
        checks["database"] = {"ok": False, "error": type(exc).__name__}

    try:
        from src.llm.anthropic_gateway import resolve_anthropic_gateway_config

        model_config = resolve_anthropic_gateway_config()
        checks["model"] = {
            "ok": True,
            "model": model_config.get("model"),
        }
    except Exception as exc:
        checks["model"] = {"ok": False, "error": str(exc)}

    runtime_issues = agent_production_issues(getattr(request.app.state, "static_dir", None))
    limits = get_agent_runtime_limits()
    checks["runtime"] = {
        "ok": not runtime_issues,
        "production": is_production_environment(),
        "workers": configured_worker_count(),
        "auth_enabled": is_auth_enabled(),
        "orchestrator_mode": "unified",
        "orchestrator_capabilities": migration_coverage(),
        "issues": runtime_issues,
        "limits": {
            "max_active_runs": limits.max_active_runs,
            "max_active_runs_per_owner": limits.max_active_runs_per_owner,
            "requests_per_minute": limits.requests_per_minute,
            "max_messages": limits.max_messages,
            "max_request_chars": limits.max_request_chars,
            "run_deadline_seconds": limits.run_deadline_seconds,
            "max_plan_tool_calls": limits.max_plan_tool_calls,
            "max_provider_calls": limits.max_provider_calls,
            "max_estimated_tokens": limits.max_estimated_tokens,
            "max_estimated_cost_micros": limits.max_estimated_cost_micros,
        },
        **active_run_registry.stats(),
    }
    checks["tools"] = {
        "ok": True,
        "registered": len(_health_registry.get_tool_names()),
    }
    # Readiness stays shallow by default. Coupling pod admission to an
    # optional market/model dependency would evict every healthy worker during
    # an upstream incident and amplify the outage. Operators can request the
    # cached deep probe explicitly from an authenticated diagnostics path.
    if deep:
        checks["dependencies"] = await _live_dependency_probe()

    ready = all(check.get("ok") is True for check in checks.values())
    payload = {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "checked_at": datetime.now().astimezone().isoformat(),
        "checks": checks,
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@router.get("/agent/metrics")
def agent_metrics(
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """Structured SLI snapshot used by dashboards and alert rules."""
    metrics = db_manager.agent_runtime_metrics()
    slo = metrics.get("slo_24h") or {}
    try:
        minimum_success_rate = float(os.getenv("AGENT_SLO_MIN_SUCCESS_RATE", "0.95"))
        max_p95_ms = int(os.getenv("AGENT_SLO_MAX_DURATION_P95_MS", "900000"))
    except (TypeError, ValueError):
        minimum_success_rate = 0.95
        max_p95_ms = 900_000
    alerts: List[Dict[str, Any]] = []
    success_rate = slo.get("success_rate")
    if success_rate is not None and success_rate < minimum_success_rate:
        alerts.append(
            {
                "code": "agent_success_rate_below_slo",
                "severity": "critical",
                "value": success_rate,
                "threshold": minimum_success_rate,
            }
        )
    p95 = slo.get("duration_ms_p95")
    if p95 is not None and p95 > max_p95_ms:
        alerts.append(
            {
                "code": "agent_latency_above_slo",
                "severity": "warning",
                "value": p95,
                "threshold": max_p95_ms,
            }
        )
    if metrics.get("expired_run_leases"):
        alerts.append(
            {
                "code": "agent_expired_run_leases",
                "severity": "warning",
                "value": metrics["expired_run_leases"],
                "threshold": 0,
            }
        )
    if metrics.get("open_circuits"):
        alerts.append(
            {
                "code": "agent_dependency_circuit_open",
                "severity": "warning",
                "value": metrics["open_circuits"],
                "threshold": 0,
            }
        )
    return {
        "checked_at": datetime.now().astimezone().isoformat(),
        "metrics": metrics,
        "alerts": alerts,
        "healthy": not any(alert["severity"] == "critical" for alert in alerts),
    }


@router.get("/agent/metrics/prometheus", response_class=Response)
def agent_metrics_prometheus(
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """Minimal Prometheus exposition without adding a runtime dependency."""
    metrics = db_manager.agent_runtime_metrics()
    lines = [
        "# HELP dsa_agent_runs_total Durable Agent runs by status.",
        "# TYPE dsa_agent_runs_total counter",
    ]
    for status, count in sorted((metrics.get("runs") or {}).items()):
        lines.append(f'dsa_agent_runs_total{{status="{status}"}} {int(count)}')
    lines.extend(
        [
            "# TYPE dsa_agent_expired_run_leases gauge",
            f"dsa_agent_expired_run_leases {int(metrics.get('expired_run_leases') or 0)}",
            "# TYPE dsa_agent_open_circuits gauge",
            f"dsa_agent_open_circuits {int(metrics.get('open_circuits') or 0)}",
            "# TYPE dsa_agent_active_resource_leases gauge",
            "dsa_agent_active_resource_leases " f"{int(metrics.get('active_resource_leases') or 0)}",
            "# TYPE dsa_agent_step_idempotency_reuses_total counter",
            "dsa_agent_step_idempotency_reuses_total " f"{int(metrics.get('step_idempotency_reuses') or 0)}",
            "# TYPE dsa_agent_recovery_attempts_24h gauge",
            "dsa_agent_recovery_attempts_24h " f"{int(metrics.get('recovery_attempts_24h') or 0)}",
        ]
    )
    slo = metrics.get("slo_24h") or {}
    if slo.get("success_rate") is not None:
        lines.extend(
            [
                "# TYPE dsa_agent_success_rate_24h gauge",
                f"dsa_agent_success_rate_24h {float(slo['success_rate'])}",
            ]
        )
    if slo.get("duration_ms_p95") is not None:
        lines.extend(
            [
                "# TYPE dsa_agent_duration_ms_p95_24h gauge",
                f"dsa_agent_duration_ms_p95_24h {int(slo['duration_ms_p95'])}",
            ]
        )
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4",
    )
