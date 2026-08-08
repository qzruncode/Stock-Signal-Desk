"""Run Explorer persistence methods for generic LangGraph runs."""

from __future__ import annotations

import src.storage.mixins.agent_quality as _base

for _name, _value in vars(_base).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _projection_items(projection: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    return [
        item
        for item in projection.get(key) or []
        if isinstance(item, Mapping)
    ]


class _AgentQualityMethods1:
    def list_agent_runs_for_owner(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        status: str | None = None,
        tool: str | None = None,
        page: int = 1,
        limit: int = 30,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 100))
        safe_page = max(1, page)
        with self.get_session() as session:
            statement = select(AgentRun).where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.owner_id == owner_id,
            )
            if status:
                statement = statement.where(AgentRun.status == status)
            runs = (
                session.execute(
                    statement.order_by(AgentRun.created_at.desc()).limit(5000)
                )
                .scalars()
                .all()
            )
            run_ids = [run.id for run in runs]
            traces = (
                session.execute(
                    select(AgentRunTrace)
                    .where(AgentRunTrace.run_id.in_(run_ids))
                    .order_by(AgentRunTrace.updated_at.asc())
                )
                .scalars()
                .all()
                if run_ids
                else []
            )
            feedback_rows = (
                session.execute(
                    select(AgentRunFeedback).where(
                        AgentRunFeedback.run_id.in_(run_ids),
                        AgentRunFeedback.tenant_id == tenant_id,
                        AgentRunFeedback.owner_id == owner_id,
                    )
                )
                .scalars()
                .all()
                if run_ids
                else []
            )
            step_rows = (
                session.execute(
                    select(AgentStepExecution).where(
                        AgentStepExecution.run_id.in_(run_ids)
                    )
                )
                .scalars()
                .all()
                if run_ids
                else []
            )

        trace_by_run = {trace.run_id: trace for trace in traces}
        feedback_by_run = {row.run_id: row for row in feedback_rows}
        steps_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for step in step_rows:
            steps_by_run[step.run_id].append(
                {
                    "task_id": step.task_id,
                    "step_id": step.step_id,
                    "tool_name": step.tool_name,
                    "status": step.status,
                    "error_code": step.error_code,
                }
            )

        rows: list[dict[str, Any]] = []
        for run in runs:
            trace = trace_by_run.get(run.id)
            projection = _load_json(
                trace.quality_projection_json if trace else None,
                {},
            )
            results = _projection_items(projection, "tool_results")
            evidence = _projection_items(projection, "evidence")
            tools = sorted(
                {
                    str(item.get("tool_name") or "")
                    for item in results
                    if str(item.get("tool_name") or "")
                }
            )
            if tool and tool not in tools:
                continue
            score = score_agent_run_snapshot(
                {
                    "run": {
                        "status": run.status,
                        "final_text": run.final_text or "",
                        "tool_call_count": int(run.tool_call_count or 0),
                        "provider_call_count": int(run.provider_call_count or 0),
                        "estimated_token_count": int(run.estimated_token_count or 0),
                        "estimated_cost_micros": int(run.estimated_cost_micros or 0),
                    },
                    "quality_projection": projection,
                    "steps": steps_by_run.get(run.id, []),
                    "feedback": _feedback_dict(feedback_by_run.get(run.id)),
                }
            )
            evidence_links_score = float(
                _mapping(_mapping(score.get("dimensions")).get("evidence_links")).get(
                    "score",
                    0,
                )
            )
            duration_ms = None
            if run.started_at and run.finished_at:
                duration_ms = max(
                    0,
                    int((run.finished_at - run.started_at).total_seconds() * 1000),
                )
            rows.append(
                {
                    "run_id": run.id,
                    "conversation_id": run.conversation_id,
                    "engine": trace.orchestrator_mode if trace else None,
                    "status": run.status,
                    "error_code": run.error_code,
                    "tools": tools,
                    "tool_observation_count": len(results),
                    "evidence_count": len(evidence),
                    "evidence_links_verified": evidence_links_score >= 1.0,
                    "quality_score": score["total_score"],
                    "quality_status": score["status"],
                    "feedback": _feedback_dict(feedback_by_run.get(run.id)),
                    "tool_call_count": int(run.tool_call_count or 0),
                    "provider_call_count": int(run.provider_call_count or 0),
                    "estimated_token_count": int(run.estimated_token_count or 0),
                    "estimated_cost_micros": int(run.estimated_cost_micros or 0),
                    "duration_ms": duration_ms,
                    "created_at": _iso(run.created_at),
                    "started_at": _iso(run.started_at),
                    "finished_at": _iso(run.finished_at),
                    "final_text_preview": str(run.final_text or "")[:240],
                }
            )
        total = len(rows)
        start = (safe_page - 1) * safe_limit
        return {
            "items": rows[start : start + safe_limit],
            "total": total,
            "page": safe_page,
            "limit": safe_limit,
        }

    def get_agent_run_quality_snapshot(
        self,
        run_id: str,
        *,
        tenant_id: str,
        owner_id: str,
        include_evidence_payloads: bool = False,
    ) -> dict[str, Any] | None:
        with self.get_session() as session:
            run = (
                session.execute(
                    select(AgentRun).where(
                        AgentRun.id == run_id,
                        AgentRun.tenant_id == tenant_id,
                        AgentRun.owner_id == owner_id,
                    )
                )
                .scalars()
                .first()
            )
            if run is None:
                return None
            trace = (
                session.execute(
                    select(AgentRunTrace)
                    .where(AgentRunTrace.run_id == run_id)
                    .order_by(AgentRunTrace.updated_at.desc())
                )
                .scalars()
                .first()
            )
            steps = (
                session.execute(
                    select(AgentStepExecution)
                    .where(AgentStepExecution.run_id == run_id)
                    .order_by(
                        AgentStepExecution.created_at.asc(),
                        AgentStepExecution.step_id.asc(),
                    )
                )
                .scalars()
                .all()
            )
            artifacts = (
                session.execute(
                    select(AgentArtifact)
                    .where(AgentArtifact.run_id == run_id)
                    .order_by(AgentArtifact.produced_at.asc())
                )
                .scalars()
                .all()
            )
            feedback = (
                session.execute(
                    select(AgentRunFeedback).where(
                        AgentRunFeedback.run_id == run_id,
                        AgentRunFeedback.tenant_id == tenant_id,
                        AgentRunFeedback.owner_id == owner_id,
                    )
                )
                .scalars()
                .first()
            )
            snapshot = {
                "run": {
                    "run_id": run.id,
                    "conversation_id": run.conversation_id,
                    "status": run.status,
                    "final_text": run.final_text or "",
                    "error_code": run.error_code,
                    "attempt": int(run.attempt or 1),
                    "tool_call_count": int(run.tool_call_count or 0),
                    "provider_call_count": int(run.provider_call_count or 0),
                    "estimated_token_count": int(run.estimated_token_count or 0),
                    "estimated_cost_micros": int(run.estimated_cost_micros or 0),
                    "created_at": _iso(run.created_at),
                    "started_at": _iso(run.started_at),
                    "finished_at": _iso(run.finished_at),
                },
                "trace": {
                    "engine": trace.orchestrator_mode if trace else None,
                    "status": trace.status if trace else None,
                    "error_code": trace.error_code if trace else None,
                    "schema_version": trace.schema_version if trace else None,
                    "stage_durations": _load_json(
                        trace.stage_durations_json if trace else None,
                        {},
                    ),
                    "latest_stage": _load_json(
                        trace.latest_stage_json if trace else None,
                        None,
                    ),
                },
                "quality_projection": _load_json(
                    trace.quality_projection_json if trace else None,
                    {},
                ),
                "steps": [
                    {
                        "idempotency_key": step.idempotency_key,
                        "task_id": step.task_id,
                        "step_id": step.step_id,
                        "tool_name": step.tool_name,
                        "effect": step.effect,
                        "status": step.status,
                        "attempt": int(step.attempt or 0),
                        "max_attempts": int(step.max_attempts or 1),
                        "reuse_count": int(step.reuse_count or 0),
                        "error_code": step.error_code,
                        "error_detail": step.error_detail,
                        "arguments": _load_json(step.arguments_json, {}) if include_evidence_payloads else None,
                        "result": _load_json(step.result_json, None) if include_evidence_payloads else None,
                        "started_at": _iso(step.started_at),
                        "finished_at": _iso(step.finished_at),
                    }
                    for step in steps
                ],
                # Legacy artifacts are display-only. New LangGraph runs keep
                # orchestration evidence in the native checkpoint and generic
                # quality projection instead of writing domain artifacts.
                "artifacts": [
                    {
                        "id": artifact.id,
                        "resource_type": artifact.resource_type,
                        "producer_node_id": artifact.producer_node_id,
                        "schema_version": artifact.schema_version,
                        "coverage": _load_json(artifact.coverage_json, {}),
                        "sources": _load_json(artifact.sources_json, []),
                        "fingerprint": artifact.fingerprint,
                        "lineage": _load_json(artifact.lineage_json, []),
                        "payload": _load_json(artifact.payload_json, {}) if include_evidence_payloads else None,
                        "produced_at": _iso(artifact.produced_at),
                    }
                    for artifact in artifacts
                ],
                "feedback": _feedback_dict(feedback),
            }
            return redact_agent_trace(snapshot)


__all__ = ["_AgentQualityMethods1"]
