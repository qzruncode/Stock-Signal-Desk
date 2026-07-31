"""Method group extracted from agent_governance."""

from __future__ import annotations

import src.storage.mixins.agent_governance as _base

for _name, _value in vars(_base).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


class _AgentGovernanceMethods1:
    def _append_agent_audit_event_in_session(
        self,
        session,
        *,
        tenant_id: str,
        owner_id: str,
        event_type: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        metadata: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> AgentAuditEvent:
        now = datetime.now()
        previous = (
            session.execute(
                select(AgentAuditEvent)
                .where(
                    AgentAuditEvent.tenant_id == tenant_id,
                    AgentAuditEvent.owner_id == owner_id,
                )
                .order_by(
                    AgentAuditEvent.created_at.desc(),
                    AgentAuditEvent.id.desc(),
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        previous_hash = previous.event_hash if previous else None
        event_id = uuid.uuid4().hex
        metadata_json = _json(dict(metadata or {}))
        event_hash = hashlib.sha256(
            _json(
                {
                    "id": event_id,
                    "tenant_id": tenant_id,
                    "owner_id": owner_id,
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                    "event_type": event_type,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "outcome": outcome,
                    "metadata": metadata_json,
                    "previous_hash": previous_hash,
                    "created_at": now.isoformat(),
                }
            ).encode("utf-8")
        ).hexdigest()
        event = AgentAuditEvent(
            id=event_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            run_id=run_id,
            conversation_id=conversation_id,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            metadata_json=metadata_json,
            previous_hash=previous_hash,
            event_hash=event_hash,
            created_at=now,
        )
        session.add(event)
        return event

    def _ensure_capability_release_in_session(
        self,
        session,
        *,
        manifest: Mapping[str, Any],
        owner_id: str,
    ) -> dict[str, Any]:
        fingerprint = str(
            manifest.get("registry_fingerprint") or ""
        )
        if not fingerprint:
            return {
                "authorized": False,
                "release_id": None,
                "release_version": None,
                "active_fingerprint": None,
                "runtime_fingerprint": "",
            }
        active = (
            session.execute(
                select(AgentCapabilityRelease).where(
                    AgentCapabilityRelease.status == "active"
                )
            )
            .scalars()
            .first()
        )
        if active is None:
            now = datetime.now()
            active = AgentCapabilityRelease(
                id=uuid.uuid4().hex,
                release_version=f"baseline-{fingerprint[:12]}",
                registry_fingerprint=fingerprint,
                manifest_json=_json(dict(manifest)),
                status="active",
                evaluation_suite=None,
                minimum_pass_rate=1.0,
                evaluation_summary_json=_json(
                    {
                        "bootstrap": True,
                        "reason": (
                            "首次启用时冻结当前内置能力清单；"
                            "后续变更必须通过版本化发布。"
                        ),
                    }
                ),
                created_by=owner_id,
                created_at=now,
                activated_at=now,
            )
            session.add(active)
            session.flush()
        return {
            "authorized": (
                active.registry_fingerprint == fingerprint
            ),
            "release_id": active.id,
            "release_version": active.release_version,
            "active_fingerprint": active.registry_fingerprint,
            "runtime_fingerprint": fingerprint,
        }

    def create_agent_capability_release(
        self,
        *,
        owner_id: str,
        release_version: str,
        manifest: Mapping[str, Any],
        evaluation_suite: str,
        minimum_pass_rate: float,
    ) -> dict[str, Any]:
        fingerprint = str(
            manifest.get("registry_fingerprint") or ""
        )
        if not fingerprint:
            raise ValueError("registry fingerprint is required")
        threshold = max(0.0, min(1.0, float(minimum_pass_rate)))
        with self.session_scope() as session:
            existing = (
                session.execute(
                    select(AgentCapabilityRelease).where(
                        AgentCapabilityRelease.release_version
                        == release_version
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                raise ValueError("release version already exists")
            record = AgentCapabilityRelease(
                id=uuid.uuid4().hex,
                release_version=release_version,
                registry_fingerprint=fingerprint,
                manifest_json=_json(dict(manifest)),
                status="draft",
                evaluation_suite=evaluation_suite,
                minimum_pass_rate=threshold,
                evaluation_summary_json="{}",
                created_by=owner_id,
            )
            session.add(record)
            session.flush()
            return self._capability_release_dict(record)

    @staticmethod
    def _capability_release_dict(
        record: AgentCapabilityRelease,
    ) -> dict[str, Any]:
        return {
            "id": record.id,
            "release_version": record.release_version,
            "registry_fingerprint": record.registry_fingerprint,
            "manifest": _load_json(record.manifest_json, {}),
            "status": record.status,
            "evaluation_suite": record.evaluation_suite,
            "minimum_pass_rate": float(
                record.minimum_pass_rate or 0.0
            ),
            "evaluation_summary": _load_json(
                record.evaluation_summary_json,
                {},
            ),
            "created_by": record.created_by,
            "created_at": _iso(record.created_at),
            "activated_at": _iso(record.activated_at),
            "retired_at": _iso(record.retired_at),
        }

    def list_agent_capability_releases(
        self,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.get_session() as session:
            records = (
                session.execute(
                    select(AgentCapabilityRelease)
                    .order_by(
                        AgentCapabilityRelease.created_at.desc()
                    )
                    .limit(max(1, min(limit, 500)))
                )
                .scalars()
                .all()
            )
            return [
                self._capability_release_dict(record)
                for record in records
            ]

    def activate_agent_capability_release(
        self,
        *,
        release_id: str,
        tenant_id: str,
        owner_id: str,
        runtime_fingerprint: str,
    ) -> dict[str, Any]:
        now = datetime.now()
        with self.session_scope() as session:
            release = session.get(
                AgentCapabilityRelease,
                release_id,
            )
            if release is None:
                raise KeyError("capability_release_not_found")
            if release.status != "draft":
                raise ValueError("only a draft release can be activated")
            if release.registry_fingerprint != runtime_fingerprint:
                raise ValueError(
                    "draft manifest does not match the running registry"
                )
            suite = str(release.evaluation_suite or "").strip()
            if not suite:
                raise ValueError(
                    "an evaluation suite is required before activation"
                )
            cases = (
                session.execute(
                    select(AgentEvaluationCase).where(
                        AgentEvaluationCase.tenant_id == tenant_id,
                        AgentEvaluationCase.owner_id == owner_id,
                        AgentEvaluationCase.suite == suite,
                        AgentEvaluationCase.status == "active",
                    )
                )
                .scalars()
                .all()
            )
            case_ids = [case.id for case in cases]
            results = (
                session.execute(
                    select(AgentEvaluationResult)
                    .where(
                        AgentEvaluationResult.case_id.in_(case_ids)
                    )
                    .order_by(
                        AgentEvaluationResult.created_at.desc()
                    )
                )
                .scalars()
                .all()
                if case_ids
                else []
            )
            latest_by_case: dict[str, AgentEvaluationResult] = {}
            for result in results:
                latest_by_case.setdefault(result.case_id, result)
            missing_cases = [
                case_id
                for case_id in case_ids
                if case_id not in latest_by_case
            ]
            if not cases or missing_cases:
                raise ValueError(
                    "every active evaluation case needs a result"
                )
            mismatched_cases = []
            for case_id, result in latest_by_case.items():
                snapshot = _load_json(result.snapshot_json, {})
                projection = (
                    snapshot.get("quality_projection")
                    if isinstance(snapshot, Mapping)
                    else {}
                )
                fingerprint = (
                    str(
                        projection.get(
                            "capability_registry_fingerprint"
                        )
                        or ""
                    )
                    if isinstance(projection, Mapping)
                    else ""
                )
                if fingerprint != release.registry_fingerprint:
                    mismatched_cases.append(case_id)
            if mismatched_cases:
                raise ValueError(
                    "every evaluation result must come from the "
                    "candidate capability registry"
                )
            passed = sum(
                result.status == "passed"
                for result in latest_by_case.values()
            )
            pass_rate = passed / len(cases)
            summary = {
                "suite": suite,
                "cases": len(cases),
                "passed": passed,
                "failed": len(cases) - passed,
                "pass_rate": round(pass_rate, 6),
                "minimum_pass_rate": float(
                    release.minimum_pass_rate
                ),
                "registry_fingerprint": (
                    release.registry_fingerprint
                ),
            }
            release.evaluation_summary_json = _json(summary)
            if pass_rate < float(release.minimum_pass_rate):
                raise ValueError(
                    "evaluation pass rate is below the release gate"
                )
            active_releases = (
                session.execute(
                    select(AgentCapabilityRelease).where(
                        AgentCapabilityRelease.status == "active"
                    )
                )
                .scalars()
                .all()
            )
            for active in active_releases:
                active.status = "retired"
                active.retired_at = now
            release.status = "active"
            release.activated_at = now
            self._append_agent_audit_event_in_session(
                session,
                tenant_id=tenant_id,
                owner_id=owner_id,
                event_type="capability_release_activated",
                resource_type="capability_release",
                resource_id=release.id,
                outcome="activated",
                metadata=summary,
            )
            session.flush()
            return self._capability_release_dict(release)



__all__ = ["_AgentGovernanceMethods1"]
