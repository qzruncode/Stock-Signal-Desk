"""AgentRuntimeMixin method group 4."""

from __future__ import annotations

from src.storage.mixins.agent_runtime import (
    datetime,
    timedelta,
    hashlib,
    json,
    Any,
    Mapping,
    Sequence,
    delete,
    func,
    or_,
    select,
    IntegrityError,
    AgentArtifact,
    AgentCircuitBreaker,
    AgentEffectOutbox,
    AgentRateLimitBucket,
    AgentResourceLease,
    AgentRuntimeControl,
    AgentRun,
    AgentRunEvent,
    AgentRunTrace,
    AgentStepExecution,
    ChatConversation,
    ChatMessage,
    _ACTIVE_RUN_STATUSES,
    _TERMINAL_RUN_STATUSES,
    _RUNNING_STEP_STATUSES,
    _json,
    _load_json,
    _run_dict,
 )

class _AgentRuntimeMixinMethods4:
    def agent_runtime_metrics(self) -> dict[str, Any]:
        now = datetime.now()
        with self.get_session() as session:
            status_rows = session.execute(
                select(AgentRun.status, func.count(AgentRun.id)).group_by(AgentRun.status)
            ).all()
            step_rows = session.execute(
                select(
                    AgentStepExecution.tool_name,
                    AgentStepExecution.status,
                    func.count(AgentStepExecution.idempotency_key),
                ).group_by(
                    AgentStepExecution.tool_name,
                    AgentStepExecution.status,
                )
            ).all()
            expired = (
                session.execute(
                    select(func.count(AgentRun.id)).where(
                        AgentRun.active_slot.is_not(None),
                        AgentRun.lease_expires_at < now,
                    )
                ).scalar()
                or 0
            )
            recent_runs = (
                session.execute(
                    select(AgentRun)
                    .where(AgentRun.created_at >= now - timedelta(hours=24))
                    .order_by(AgentRun.created_at.desc())
                    .limit(10_000)
                )
                .scalars()
                .all()
            )
            recent_traces = (
                session.execute(
                    select(AgentRunTrace)
                    .where(AgentRunTrace.created_at >= now - timedelta(hours=24))
                    .order_by(AgentRunTrace.created_at.desc())
                    .limit(10_000)
                )
                .scalars()
                .all()
            )
            terminal_recent = [record for record in recent_runs if record.status in _TERMINAL_RUN_STATUSES]
            durations_ms = sorted(
                int((record.finished_at - record.started_at).total_seconds() * 1000)
                for record in terminal_recent
                if record.started_at is not None and record.finished_at is not None
            )
            planning_stages = {
                "outline",
                "parameterization",
                "normalization",
                "resource_binding",
                "compilation",
                "policy",
            }
            planning_durations_ms = sorted(
                sum(
                    max(0, int(value))
                    for key, value in durations.items()
                    if str(key).split(":", 1)[0] in planning_stages
                )
                for record in recent_traces
                if isinstance(
                    durations := _load_json(
                        record.stage_durations_json,
                        {},
                    ),
                    Mapping,
                )
                and durations
            )

            def percentile(values: list[int], ratio: float) -> int | None:
                if not values:
                    return None
                index = min(
                    len(values) - 1,
                    max(0, int(round((len(values) - 1) * ratio))),
                )
                return values[index]

            successes = sum(record.status == "completed" for record in terminal_recent)
            open_circuits = (
                session.execute(
                    select(func.count(AgentCircuitBreaker.resource_name)).where(
                        AgentCircuitBreaker.state == "open",
                        or_(
                            AgentCircuitBreaker.opened_until.is_(None),
                            AgentCircuitBreaker.opened_until > now,
                        ),
                    )
                ).scalar()
                or 0
            )
            half_open_circuits = (
                session.execute(
                    select(func.count(AgentCircuitBreaker.resource_name)).where(
                        AgentCircuitBreaker.state == "half_open",
                    )
                ).scalar()
                or 0
            )
            expired_circuits = (
                session.execute(
                    select(func.count(AgentCircuitBreaker.resource_name)).where(
                        AgentCircuitBreaker.state == "open",
                        AgentCircuitBreaker.opened_until.is_not(None),
                        AgentCircuitBreaker.opened_until <= now,
                    )
                ).scalar()
                or 0
            )
            active_resource_leases = (
                session.execute(
                    select(func.count(AgentResourceLease.id)).where(
                        AgentResourceLease.lease_owner.is_not(None),
                        AgentResourceLease.lease_expires_at >= now,
                    )
                ).scalar()
                or 0
            )
            step_reuses = session.execute(select(func.sum(AgentStepExecution.reuse_count))).scalar() or 0
            recovery_attempts = sum(max(0, int(record.attempt or 1) - 1) for record in recent_runs)
            recovered_terminal = [record for record in terminal_recent if int(record.attempt or 1) > 1]
            recovered_successes = sum(record.status == "completed" for record in recovered_terminal)
            event_count = sum(int(record.event_cursor or 0) for record in recent_runs)
            provider_calls = sum(int(record.provider_call_count or 0) for record in recent_runs)
            tool_calls = sum(int(record.tool_call_count or 0) for record in recent_runs)
            estimated_tokens = sum(int(record.estimated_token_count or 0) for record in recent_runs)
            estimated_cost_micros = sum(int(record.estimated_cost_micros or 0) for record in recent_runs)
            usages = [_load_json(record.usage_json, {}) for record in recent_runs]
            reported_calls = sum(item.get("reported_calls", 0) for item in usages)
            actual_usage = {
                "reported_calls": reported_calls,
                "unreported_calls": max(0, provider_calls - reported_calls),
                "input_tokens": sum(item.get("total", {}).get("input_tokens", 0) for item in usages),
                "output_tokens": sum(item.get("total", {}).get("output_tokens", 0) for item in usages),
                "total_tokens": sum(item.get("total", {}).get("total_tokens", 0) for item in usages),
                "source": "provider",
            } if reported_calls else None
            return {
                "runs": {status: int(count) for status, count in status_rows},
                "steps": [
                    {
                        "tool_name": tool_name,
                        "status": status,
                        "count": int(count),
                    }
                    for tool_name, status, count in step_rows
                ],
                "expired_run_leases": int(expired),
                "active_resource_leases": int(active_resource_leases),
                "open_circuits": int(open_circuits),
                "half_open_circuits": int(half_open_circuits),
                "expired_circuits": int(expired_circuits),
                "step_idempotency_reuses": int(step_reuses),
                "recovery_attempts_24h": int(recovery_attempts),
                "recovery_24h": {
                    "terminal_runs": len(recovered_terminal),
                    "successful_runs": recovered_successes,
                    "success_rate": (
                        round(
                            recovered_successes / len(recovered_terminal),
                            6,
                        )
                        if recovered_terminal
                        else None
                    ),
                },
                "workload_24h": {
                    "events": event_count,
                    "events_per_run": (round(event_count / len(recent_runs), 3) if recent_runs else 0.0),
                    "provider_calls": provider_calls,
                    "tool_calls": tool_calls,
                    "estimated_tokens": estimated_tokens,
                    "estimated_cost_micros": estimated_cost_micros,
                    "actual_usage": actual_usage,
                },
                "slo_24h": {
                    "terminal_runs": len(terminal_recent),
                    "successful_runs": successes,
                    "success_rate": (round(successes / len(terminal_recent), 6) if terminal_recent else None),
                    "duration_ms_p50": percentile(durations_ms, 0.50),
                    "duration_ms_p95": percentile(durations_ms, 0.95),
                    "planning_ms_p50": percentile(
                        planning_durations_ms,
                        0.50,
                    ),
                    "planning_ms_p95": percentile(
                        planning_durations_ms,
                        0.95,
                    ),
                },
            }
    def prune_agent_runtime_data(
        self,
        *,
        finished_before: datetime,
        trace_before: datetime | None = None,
        limit: int = 500,
    ) -> dict[str, int]:
        """Delete bounded terminal runtime history; cascades events and steps."""
        with self.session_scope() as session:
            run_ids = (
                session.execute(
                    select(AgentRun.id)
                    .where(
                        AgentRun.status.in_(_TERMINAL_RUN_STATUSES),
                        AgentRun.finished_at < finished_before,
                        ~AgentRun.id.in_(select(AgentFinancialConclusion.run_id)),
                    )
                    .order_by(AgentRun.finished_at.asc())
                    .limit(max(1, min(limit, 5000)))
                )
                .scalars()
                .all()
            )
            trace_cutoff = trace_before or finished_before
            safe_limit = max(1, min(limit, 5000))
            trace_ids = (
                session.execute(
                    select(AgentRunTrace.id)
                    .where(
                        AgentRunTrace.created_at < trace_cutoff,
                        ~AgentRunTrace.run_id.in_(select(AgentFinancialConclusion.run_id)),
                    )
                    .order_by(AgentRunTrace.created_at.asc())
                    .limit(safe_limit)
                )
                .scalars()
                .all()
            )
            traces = (
                session.execute(delete(AgentRunTrace).where(AgentRunTrace.id.in_(trace_ids))).rowcount or 0
                if trace_ids
                else 0
            )
            # Artifacts are semantic conversation state and may be referenced
            # by later turns. They follow conversation deletion, not trace TTL.
            artifacts = 0
            bucket_ids = (
                session.execute(
                    select(AgentRateLimitBucket.id)
                    .where(AgentRateLimitBucket.expires_at < datetime.now())
                    .order_by(AgentRateLimitBucket.expires_at.asc())
                    .limit(safe_limit)
                )
                .scalars()
                .all()
            )
            rate_buckets = (
                session.execute(delete(AgentRateLimitBucket).where(AgentRateLimitBucket.id.in_(bucket_ids))).rowcount
                or 0
                if bucket_ids
                else 0
            )
            if not run_ids:
                return {
                    "runs": 0,
                    "traces": int(traces),
                    "artifacts": int(artifacts),
                    "rate_limit_buckets": int(rate_buckets),
                }
            # Explicit deletes keep behavior consistent when a legacy SQLite
            # connection was created before foreign_keys=ON was introduced.
            session.execute(delete(AgentRunEvent).where(AgentRunEvent.run_id.in_(run_ids)))
            session.execute(delete(AgentStepExecution).where(AgentStepExecution.run_id.in_(run_ids)))
            session.execute(delete(AgentEffectOutbox).where(AgentEffectOutbox.run_id.in_(run_ids)))
            result = session.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
            return {
                "runs": int(result.rowcount or 0),
                "traces": int(traces),
                "artifacts": int(artifacts),
                "rate_limit_buckets": int(rate_buckets),
            }
