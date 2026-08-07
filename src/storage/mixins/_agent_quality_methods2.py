"""Method group extracted from agent_quality."""

from __future__ import annotations

import src.storage.mixins.agent_quality as _base

for _name, _value in vars(_base).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


class _AgentQualityMethods2:
    def create_agent_evaluation_case(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        source_run_id: str,
        suite: str,
        name: str,
        description: str | None,
        expectations: Mapping[str, Any],
        tags: Sequence[str] = (),
    ) -> dict[str, Any]:
        snapshot = self.get_agent_run_quality_snapshot(
            source_run_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            include_evidence_payloads=True,
        )
        if snapshot is None:
            raise KeyError("agent_run_not_found")
        run_status = str(
            (snapshot.get("run") or {}).get("status") or ""
        )
        if run_status not in {
            "completed",
            "partial",
            "failed",
            "cancelled",
            "blocked",
        }:
            raise ValueError(
                "evaluation cases require a terminal source run"
            )
        source_run = self.get_agent_run(run_id=source_run_id) or {}
        with self.session_scope() as session:
            latest_version = (
                session.execute(
                    select(func.max(AgentEvaluationCase.version)).where(
                        AgentEvaluationCase.tenant_id == tenant_id,
                        AgentEvaluationCase.owner_id == owner_id,
                        AgentEvaluationCase.suite == suite,
                        AgentEvaluationCase.name == name,
                    )
                ).scalar()
                or 0
            )
            record = AgentEvaluationCase(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                owner_id=owner_id,
                suite=suite,
                name=name,
                description=description,
                status="active",
                version=int(latest_version) + 1,
                source_run_id=source_run_id,
                request_snapshot_json=_json(
                    {
                        "run_id": source_run_id,
                        "request": source_run.get("request"),
                    }
                ),
                evidence_snapshot_json=_json(snapshot),
                expectations_json=_json(dict(expectations)),
                tags_json=_json(
                    list(dict.fromkeys(str(item) for item in tags))
                ),
            )
            session.add(record)
            session.flush()
            return _case_dict(record)

    def list_agent_evaluation_cases(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        suite: str | None = None,
        status: str | None = "active",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.get_session() as session:
            statement = select(AgentEvaluationCase).where(
                AgentEvaluationCase.tenant_id == tenant_id,
                AgentEvaluationCase.owner_id == owner_id,
            )
            if suite:
                statement = statement.where(
                    AgentEvaluationCase.suite == suite
                )
            if status:
                statement = statement.where(
                    AgentEvaluationCase.status == status
                )
            records = (
                session.execute(
                    statement.order_by(
                        AgentEvaluationCase.updated_at.desc()
                    ).limit(max(1, min(limit, 500)))
                )
                .scalars()
                .all()
            )
            return [_case_dict(record) for record in records]

    def evaluate_agent_run_against_case(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        case_id: str,
        candidate_run_id: str,
    ) -> dict[str, Any]:
        with self.get_session() as session:
            case = (
                session.execute(
                    select(AgentEvaluationCase).where(
                        AgentEvaluationCase.id == case_id,
                        AgentEvaluationCase.tenant_id == tenant_id,
                        AgentEvaluationCase.owner_id == owner_id,
                    )
                )
                .scalars()
                .first()
            )
            if case is None:
                raise KeyError("evaluation_case_not_found")
            expectations = _load_json(case.expectations_json, {})
        snapshot = self.get_agent_run_quality_snapshot(
            candidate_run_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            include_evidence_payloads=False,
        )
        if snapshot is None:
            raise KeyError("agent_run_not_found")
        result = score_agent_run_snapshot(snapshot, expectations)
        with self.session_scope() as session:
            record = AgentEvaluationResult(
                id=uuid.uuid4().hex,
                case_id=case_id,
                candidate_run_id=candidate_run_id,
                evaluator_version=EVALUATOR_VERSION,
                status=result["status"],
                total_score=float(result["total_score"]),
                scores_json=_json(result["dimensions"]),
                violations_json=_json(result["violations"]),
                snapshot_json=_json(
                    {
                        "run": snapshot.get("run"),
                        "trace": snapshot.get("trace"),
                        "quality_projection": snapshot.get(
                            "quality_projection"
                        ),
                        "steps": snapshot.get("steps"),
                    }
                ),
            )
            session.add(record)
            session.flush()
            return {
                **_result_dict(record),
                "minimum_score": result["minimum_score"],
                "passed": result["passed"],
            }

    def list_agent_evaluation_results(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        case_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.get_session() as session:
            statement = (
                select(AgentEvaluationResult)
                .join(
                    AgentEvaluationCase,
                    AgentEvaluationCase.id
                    == AgentEvaluationResult.case_id,
                )
                .where(
                    AgentEvaluationCase.tenant_id == tenant_id,
                    AgentEvaluationCase.owner_id == owner_id,
                )
            )
            if case_id:
                statement = statement.where(
                    AgentEvaluationResult.case_id == case_id
                )
            records = (
                session.execute(
                    statement.order_by(
                        AgentEvaluationResult.created_at.desc()
                    ).limit(max(1, min(limit, 500)))
                )
                .scalars()
                .all()
            )
            return [_result_dict(record) for record in records]

    def upsert_agent_run_feedback(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        run_id: str,
        rating: int,
        category: str | None,
        comment: str | None,
    ) -> dict[str, Any]:
        if rating not in {-1, 1}:
            raise ValueError("rating must be -1 or 1")
        now = datetime.now()
        with self.session_scope() as session:
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
                raise KeyError("agent_run_not_found")
            record = (
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
            if record is None:
                record = AgentRunFeedback(
                    id=uuid.uuid4().hex,
                    run_id=run_id,
                    conversation_id=run.conversation_id,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    created_at=now,
                )
                session.add(record)
            record.rating = rating
            record.category = category
            record.comment = comment
            record.updated_at = now
            session.flush()
            return _feedback_dict(record) or {}

    def agent_quality_summary(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        days: int = 30,
        limit: int = 1000,
    ) -> dict[str, Any]:
        cutoff = datetime.now() - timedelta(
            days=max(1, min(days, 365))
        )
        safe_limit = max(1, min(limit, 5000))
        with self.get_session() as session:
            runs = (
                session.execute(
                    select(AgentRun)
                    .where(
                        AgentRun.tenant_id == tenant_id,
                        AgentRun.owner_id == owner_id,
                        AgentRun.created_at >= cutoff,
                        AgentRun.status.in_(
                            (
                                "completed",
                                "partial",
                                "failed",
                                "cancelled",
                                "blocked",
                            )
                        ),
                    )
                    .order_by(AgentRun.created_at.desc())
                    .limit(safe_limit)
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
            steps = (
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
            evaluation_rows = (
                session.execute(
                    select(AgentEvaluationResult)
                    .join(
                        AgentEvaluationCase,
                        AgentEvaluationCase.id
                        == AgentEvaluationResult.case_id,
                    )
                    .where(
                        AgentEvaluationCase.tenant_id == tenant_id,
                        AgentEvaluationCase.owner_id == owner_id,
                        AgentEvaluationResult.created_at >= cutoff,
                    )
                )
                .scalars()
                .all()
            )

        trace_by_run = {trace.run_id: trace for trace in traces}
        steps_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for step in steps:
            steps_by_run[step.run_id].append(
                {
                    "task_id": step.task_id,
                    "step_id": step.step_id,
                    "tool_name": step.tool_name,
                    "status": step.status,
                    "error_code": step.error_code,
                }
            )
        feedback_by_run = {
            feedback.run_id: feedback for feedback in feedback_rows
        }
        scores: list[dict[str, Any]] = []
        for run in runs:
            trace = trace_by_run.get(run.id)
            snapshot = {
                "run": {
                    "status": run.status,
                    "final_text": run.final_text or "",
                    "tool_call_count": int(
                        run.tool_call_count or 0
                    ),
                    "provider_call_count": int(
                        run.provider_call_count or 0
                    ),
                    "estimated_token_count": int(
                        run.estimated_token_count or 0
                    ),
                    "estimated_cost_micros": int(
                        run.estimated_cost_micros or 0
                    ),
                },
                "quality_projection": _load_json(
                    trace.quality_projection_json if trace else None,
                    {},
                ),
                "steps": steps_by_run.get(run.id, []),
                "feedback": _feedback_dict(
                    feedback_by_run.get(run.id)
                ),
            }
            scores.append(score_agent_run_snapshot(snapshot))

        dimension_totals: Counter[str] = Counter()
        for score in scores:
            for name, value in score["dimensions"].items():
                dimension_totals[name] += float(value["score"])
        violation_counts = Counter(
            str(violation.get("code") or "unknown")
            for score in scores
            for violation in score["violations"]
        )
        positive = sum(row.rating == 1 for row in feedback_rows)
        passed_evaluations = sum(
            row.status == "passed" for row in evaluation_rows
        )
        return {
            "window_days": max(1, min(days, 365)),
            "terminal_runs": len(runs),
            "quality": {
                "scored_runs": len(scores),
                "passed_runs": sum(
                    bool(score["passed"]) for score in scores
                ),
                "pass_rate": (
                    round(
                        sum(
                            bool(score["passed"])
                            for score in scores
                        )
                        / len(scores),
                        6,
                    )
                    if scores
                    else None
                ),
                "average_score": (
                    round(
                        sum(
                            float(score["total_score"])
                            for score in scores
                        )
                        / len(scores),
                        6,
                    )
                    if scores
                    else None
                ),
                "dimension_scores": {
                    name: round(total / len(scores), 6)
                    for name, total in sorted(
                        dimension_totals.items()
                    )
                }
                if scores
                else {},
                "violations": dict(violation_counts),
            },
            "feedback": {
                "total": len(feedback_rows),
                "positive": positive,
                "negative": len(feedback_rows) - positive,
                "positive_rate": (
                    round(positive / len(feedback_rows), 6)
                    if feedback_rows
                    else None
                ),
            },
            "release_gate": {
                "evaluations": len(evaluation_rows),
                "passed": passed_evaluations,
                "failed": len(evaluation_rows)
                - passed_evaluations,
                "pass_rate": (
                    round(
                        passed_evaluations / len(evaluation_rows),
                        6,
                    )
                    if evaluation_rows
                    else None
                ),
            },
        }


__all__ = ["_AgentQualityMethods2"]
