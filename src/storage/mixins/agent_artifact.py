# -*- coding: utf-8 -*-
"""Persistence operations for independent V2 Agent artifacts."""

from __future__ import annotations

import json
from typing import Iterable

from sqlalchemy import delete, select

from src.agent.orchestrator_v2.contracts import AgentArtifactV2, ResourceType
from src.storage.models import AgentArtifact


class AgentArtifactMixin:
    def save_agent_artifacts(
        self,
        artifacts: Iterable[AgentArtifactV2],
    ) -> int:
        values = list(artifacts)
        if not values:
            return 0
        saved = 0
        with self.session_scope() as session:
            for artifact in values:
                existing = session.execute(
                    select(AgentArtifact).where(
                        AgentArtifact.conversation_id == artifact.conversation_id,
                        AgentArtifact.fingerprint == artifact.fingerprint,
                    )
                ).scalars().first()
                if existing is not None:
                    continue
                session.add(AgentArtifact(
                    id=artifact.artifact_id,
                    conversation_id=artifact.conversation_id,
                    run_id=artifact.run_id,
                    schema_version=artifact.schema_version,
                    producer_node_id=artifact.producer_node_id,
                    resource_type=artifact.resource_type.value,
                    coverage_json=artifact.coverage.model_dump_json(),
                    sources_json=json.dumps(
                        [
                            item.model_dump(mode="json")
                            for item in artifact.sources
                        ],
                        ensure_ascii=False,
                    ),
                    fingerprint=artifact.fingerprint,
                    lineage_json=json.dumps(
                        list(artifact.lineage),
                        ensure_ascii=False,
                    ),
                    payload_json=json.dumps(
                        artifact.payload,
                        ensure_ascii=False,
                        default=str,
                    ),
                    produced_at=artifact.produced_at,
                ))
                saved += 1
        return saved

    def get_agent_artifact(
        self,
        artifact_id: str,
    ) -> AgentArtifactV2 | None:
        with self.get_session() as session:
            record = session.get(AgentArtifact, artifact_id)
            if record is None:
                return None
            return _to_contract(record)

    def get_agent_artifacts(
        self,
        artifact_ids: Iterable[str],
    ) -> list[AgentArtifactV2]:
        """Load a reference set in one query while preserving caller order."""
        ordered_ids = tuple(dict.fromkeys(
            str(value).strip()
            for value in artifact_ids
            if str(value).strip()
        ))
        if not ordered_ids:
            return []
        with self.get_session() as session:
            records = session.execute(
                select(AgentArtifact).where(AgentArtifact.id.in_(ordered_ids))
            ).scalars().all()
            by_id = {record.id: record for record in records}
            return [
                _to_contract(by_id[artifact_id])
                for artifact_id in ordered_ids
                if artifact_id in by_id
            ]

    def list_agent_artifacts(
        self,
        conversation_id: str,
        *,
        resource_type: ResourceType | None = None,
        limit: int = 50,
    ) -> list[AgentArtifactV2]:
        with self.get_session() as session:
            statement = select(AgentArtifact).where(
                AgentArtifact.conversation_id == conversation_id
            )
            if resource_type is not None:
                statement = statement.where(
                    AgentArtifact.resource_type == resource_type.value
                )
            records = session.execute(
                statement
                .order_by(AgentArtifact.produced_at.desc())
                .limit(max(1, min(limit, 500)))
            ).scalars().all()
            return [_to_contract(record) for record in records]

    def delete_agent_artifacts(self, conversation_id: str) -> int:
        with self.session_scope() as session:
            result = session.execute(
                delete(AgentArtifact).where(
                    AgentArtifact.conversation_id == conversation_id
                )
            )
            return result.rowcount or 0

    def prune_agent_artifacts(
        self,
        conversation_id: str,
        keep_artifact_ids: Iterable[str],
    ) -> int:
        keep = tuple(dict.fromkeys(
            str(value).strip()
            for value in keep_artifact_ids
            if str(value).strip()
        ))
        with self.session_scope() as session:
            statement = delete(AgentArtifact).where(
                AgentArtifact.conversation_id == conversation_id
            )
            if keep:
                statement = statement.where(AgentArtifact.id.not_in(keep))
            result = session.execute(statement)
            return result.rowcount or 0


def _to_contract(record: AgentArtifact) -> AgentArtifactV2:
    return AgentArtifactV2.model_validate({
        "artifact_id": record.id,
        "schema_version": record.schema_version,
        "run_id": record.run_id,
        "conversation_id": record.conversation_id,
        "producer_node_id": record.producer_node_id,
        "resource_type": record.resource_type,
        "coverage": json.loads(record.coverage_json),
        "sources": json.loads(record.sources_json or "[]"),
        "produced_at": record.produced_at,
        "fingerprint": record.fingerprint,
        "lineage": json.loads(record.lineage_json or "[]"),
        "payload": json.loads(record.payload_json),
    })


__all__ = ["AgentArtifactMixin"]
