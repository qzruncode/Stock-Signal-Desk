"""Read-only compatibility access for artifacts produced by legacy engines."""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import delete, select

from src.storage.models import AgentArtifact


class AgentArtifactMixin:
    def save_agent_artifacts(self, artifacts: Iterable[Any]) -> int:
        del artifacts
        raise RuntimeError("legacy Agent artifacts are read-only after LangGraph cutover")

    def get_agent_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self.get_session() as session:
            record = session.get(AgentArtifact, artifact_id)
            return _to_legacy_view(record) if record is not None else None

    def get_agent_artifacts(self, artifact_ids: Iterable[str]) -> list[dict[str, Any]]:
        ordered_ids = tuple(
            dict.fromkeys(str(value).strip() for value in artifact_ids if str(value).strip())
        )
        if not ordered_ids:
            return []
        with self.get_session() as session:
            records = (
                session.execute(select(AgentArtifact).where(AgentArtifact.id.in_(ordered_ids)))
                .scalars()
                .all()
            )
            by_id = {record.id: record for record in records}
            return [_to_legacy_view(by_id[item]) for item in ordered_ids if item in by_id]

    def list_agent_artifacts(
        self,
        conversation_id: str,
        *,
        resource_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self.get_session() as session:
            statement = select(AgentArtifact).where(
                AgentArtifact.conversation_id == conversation_id
            )
            if resource_type is not None:
                statement = statement.where(AgentArtifact.resource_type == str(resource_type))
            records = (
                session.execute(
                    statement.order_by(AgentArtifact.produced_at.desc()).limit(
                        max(1, min(limit, 500))
                    )
                )
                .scalars()
                .all()
            )
            return [_to_legacy_view(record) for record in records]

    def delete_agent_artifacts(self, conversation_id: str) -> int:
        with self.session_scope() as session:
            result = session.execute(
                delete(AgentArtifact).where(AgentArtifact.conversation_id == conversation_id)
            )
            return result.rowcount or 0

    def prune_agent_artifacts(
        self,
        conversation_id: str,
        keep_artifact_ids: Iterable[str],
    ) -> int:
        keep = tuple(
            dict.fromkeys(
                str(value).strip() for value in keep_artifact_ids if str(value).strip()
            )
        )
        with self.session_scope() as session:
            statement = delete(AgentArtifact).where(
                AgentArtifact.conversation_id == conversation_id
            )
            if keep:
                statement = statement.where(AgentArtifact.id.not_in(keep))
            result = session.execute(statement)
            return result.rowcount or 0


def _load(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _to_legacy_view(record: AgentArtifact) -> dict[str, Any]:
    return {
        "artifact_id": record.id,
        "schema_version": record.schema_version,
        "run_id": record.run_id,
        "conversation_id": record.conversation_id,
        "producer_node_id": record.producer_node_id,
        "resource_type": record.resource_type,
        "coverage": _load(record.coverage_json, {}),
        "sources": _load(record.sources_json, []),
        "produced_at": record.produced_at,
        "fingerprint": record.fingerprint,
        "lineage": _load(record.lineage_json, []),
        "payload": _load(record.payload_json, {}),
        "read_only": True,
        "legacy_engine": True,
    }


__all__ = ["AgentArtifactMixin"]
