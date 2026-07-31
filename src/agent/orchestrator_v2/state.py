# -*- coding: utf-8 -*-
"""Versioned conversation summaries and artifact-only legacy-state migration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from pydantic import Field, field_validator

from src.agent.conversation_context import ConversationContext
from src.agent.orchestrator_v2.contracts import (
    AgentArtifactV2,
    CoverageV2,
    EvidenceV2,
    ResourceType,
    StrictModel,
    stable_fingerprint,
)


CONVERSATION_CONTEXT_V2_VERSION = "3"
ARTIFACT_SCHEMA_VERSION = "agent-artifact-3.0"
MAX_V2_TURNS = 8


class ArtifactReferenceV2(StrictModel):
    artifact_id: str
    resource_type: ResourceType
    fingerprint: str
    producer_node_id: str


class TaskSummaryV2(StrictModel):
    node_id: str
    capability: str
    objective: str = Field(max_length=500)
    status: str
    artifact_refs: tuple[str, ...] = ()
    coverage: CoverageV2
    action_fingerprint: str | None = None
    confirmation_pending: bool = False


class TurnSummaryV2(StrictModel):
    run_id: str
    request_message_id: str | None = Field(default=None, max_length=128)
    request_summary: str = Field(max_length=2_000)
    tasks: tuple[TaskSummaryV2, ...] = Field(default_factory=tuple, max_length=12)
    terminal_artifacts: tuple[ArtifactReferenceV2, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationContextV2(StrictModel):
    version: str = CONVERSATION_CONTEXT_V2_VERSION
    turns: tuple[TurnSummaryV2, ...] = Field(default_factory=tuple, max_length=MAX_V2_TURNS)

    @field_validator("turns", mode="before")
    @classmethod
    def _keep_recent(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return tuple(value[-MAX_V2_TURNS:])
        return value

    @classmethod
    def from_value(
        cls,
        value: Any,
    ) -> "ConversationContextV2":
        if not isinstance(value, Mapping) or str(value.get("version") or "") != CONVERSATION_CONTEXT_V2_VERSION:
            return cls()
        try:
            return cls.model_validate(value)
        except Exception:
            return cls()

    def append(self, turn: TurnSummaryV2) -> "ConversationContextV2":
        return self.model_copy(
            update={
                "turns": (*self.turns, turn)[-MAX_V2_TURNS:],
            }
        )

    def planner_payload(
        self,
        *,
        current_request: str | None = None,
    ) -> dict[str, Any]:
        """Expose only relevant terminal summaries, never retry noise or payloads."""
        current_key = _request_identity(current_request)
        latest_by_request: dict[str, tuple[int, TurnSummaryV2]] = {}
        for index, turn in enumerate(self.turns):
            key = _request_identity(turn.request_summary)
            if current_key and key == current_key:
                # Repeating a request is a fresh run over its original upstream
                # resources. Previous attempts of the same request must not
                # become competing candidate inputs for the planner.
                continue
            latest_by_request[key or f"__turn_{index}"] = (index, turn)
        relevant_turns = tuple(turn for _index, turn in sorted(latest_by_request.values()))
        return self.model_copy(
            update={"turns": relevant_turns},
        ).model_dump(mode="json")

    def latest_artifact_refs(
        self,
        resource_type: ResourceType | None = None,
    ) -> tuple[ArtifactReferenceV2, ...]:
        if not self.turns:
            return ()
        values = self.turns[-1].terminal_artifacts
        if resource_type is None:
            return values
        return tuple(item for item in values if item.resource_type == resource_type)

    def pending_action_fingerprints(self) -> frozenset[str]:
        if not self.turns:
            return frozenset()
        return frozenset(
            task.action_fingerprint
            for task in self.turns[-1].tasks
            if task.confirmation_pending and task.action_fingerprint
        )

    def retain_for_messages(
        self,
        messages: Iterable[Mapping[str, Any]],
    ) -> "ConversationContextV2":
        """Prune deleted turns using user-message identity, never answer prose."""
        user_messages: list[tuple[str, str]] = []
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            if str(message.get("role") or "") != "user":
                continue
            content = message.get("content")
            text = content.strip() if isinstance(content, str) else ""
            if not text:
                continue
            user_messages.append(
                (
                    str(message.get("id") or "").strip(),
                    text[:2_000],
                )
            )
        ids = {message_id for message_id, _ in user_messages if message_id}
        retained: list[TurnSummaryV2] = []
        search_start = 0
        for turn in self.turns:
            if turn.request_message_id:
                if turn.request_message_id in ids:
                    retained.append(turn)
                continue
            for index in range(search_start, len(user_messages)):
                if user_messages[index][1] != turn.request_summary:
                    continue
                retained.append(
                    turn.model_copy(
                        update={
                            "request_message_id": user_messages[index][0] or None,
                        }
                    )
                )
                search_start = index + 1
                break
        return self.model_copy(update={"turns": tuple(retained)})


def migrate_legacy_context(
    value: Any,
    *,
    conversation_id: str,
) -> tuple[ConversationContextV2, tuple[AgentArtifactV2, ...]]:
    """One-way read adapter; never parses Markdown or stores legacy result bodies."""
    legacy = value if isinstance(value, ConversationContext) else ConversationContext.from_value(value)
    if not legacy.turns:
        return ConversationContextV2(), ()

    artifacts: list[AgentArtifactV2] = []
    turns: list[TurnSummaryV2] = []
    for turn_index, turn in enumerate(legacy.turns):
        run_id = (
            "migration_"
            + stable_fingerprint(
                {
                    "conversation_id": conversation_id,
                    "turn_index": turn_index,
                    "request": turn.request,
                }
            )[:24]
        )
        terminal_refs: list[ArtifactReferenceV2] = []
        refs_by_producer: dict[str, list[ArtifactReferenceV2]] = {}

        def append_artifact(
            *,
            producer: str,
            resource_type: ResourceType,
            payload: Any,
            item_count: int,
        ) -> None:
            fingerprint = stable_fingerprint(
                {
                    "resource_type": resource_type.value,
                    "payload": payload,
                }
            )
            artifact_id = (
                "artifact_"
                + stable_fingerprint(
                    {
                        "conversation_id": conversation_id,
                        "fingerprint": fingerprint,
                    }
                )[:32]
            )
            coverage = CoverageV2(
                requested=item_count,
                covered=item_count,
                missing=(),
                complete=True,
            )
            artifact = AgentArtifactV2(
                artifact_id=artifact_id,
                schema_version=ARTIFACT_SCHEMA_VERSION,
                run_id=run_id,
                conversation_id=conversation_id,
                producer_node_id=producer,
                resource_type=resource_type,
                coverage=coverage,
                sources=(
                    EvidenceV2(
                        source="structured_context_migration",
                        summary="从旧版已验证结构化状态迁移；未解析回答文本。",
                    ),
                ),
                produced_at=datetime.now(timezone.utc),
                fingerprint=fingerprint,
                lineage=(),
                payload=payload,
            )
            reference = ArtifactReferenceV2(
                artifact_id=artifact_id,
                resource_type=resource_type,
                fingerprint=fingerprint,
                producer_node_id=producer,
            )
            artifacts.append(artifact)
            terminal_refs.append(reference)
            refs_by_producer.setdefault(producer, []).append(reference)

        entities = [item.model_dump() for item in turn.entities]
        if entities:
            producer = turn.tasks[-1].task_id if turn.tasks else f"legacy_turn_{turn_index}"
            append_artifact(
                producer=producer,
                resource_type=ResourceType.SECURITY_COLLECTION,
                payload={"securities": entities},
                item_count=len(entities),
            )

        for task in turn.tasks:
            if task.status != "completed":
                continue
            for semantic in task.semantic_artifacts:
                if not isinstance(semantic, Mapping) or semantic.get("type") not in {
                    "domain_collection_v2",
                    "ranked_domains",
                }:
                    continue
                domains: list[dict[str, Any]] = []
                seen: set[str] = set()
                if semantic.get("type") == "domain_collection_v2":
                    for value in semantic.get("boards") or []:
                        if not isinstance(value, Mapping):
                            continue
                        label = str(value.get("board_name") or "").strip()
                        if not label or label in seen:
                            continue
                        seen.add(label)
                        domains.append(
                            {
                                "label": label,
                                "board_queries": [label],
                                "mapping_type": "catalog_binding",
                                "rationale": str(value.get("rationale") or ""),
                                "unresolved_parts": [],
                                "tier": value.get("tier"),
                            }
                        )
                else:
                    for group in semantic.get("groups") or []:
                        if not isinstance(group, Mapping):
                            continue
                        for value in group.get("domains") or []:
                            if not isinstance(value, Mapping):
                                continue
                            label = str(value.get("label") or "").strip()
                            if not label or label in seen:
                                continue
                            seen.add(label)
                            item = dict(value)
                            item["label"] = label
                            item.setdefault("tier", group.get("tier"))
                            domains.append(item)
                if domains:
                    append_artifact(
                        producer=task.task_id,
                        resource_type=ResourceType.DOMAIN_COLLECTION,
                        payload={
                            "domains": domains,
                            (
                                "domain_collection_v2"
                                if semantic.get("type") == "domain_collection_v2"
                                else "ranked_domains"
                            ): dict(semantic),
                        },
                        item_count=len(domains),
                    )

        task_summaries = tuple(
            TaskSummaryV2(
                node_id=task.task_id,
                capability=task.kind,
                objective=task.objective,
                status=task.status,
                artifact_refs=tuple(ref.artifact_id for ref in refs_by_producer.get(task.task_id, ())),
                coverage=(
                    next(
                        artifact.coverage
                        for artifact in reversed(artifacts)
                        if artifact.producer_node_id == task.task_id
                    )
                    if task.task_id in refs_by_producer
                    else CoverageV2(
                        requested=0,
                        covered=0,
                        missing=(),
                        complete=True,
                    )
                ),
            )
            for task in turn.tasks
        )
        turns.append(
            TurnSummaryV2(
                run_id=run_id,
                request_message_id=turn.request_message_id,
                request_summary=turn.request,
                tasks=task_summaries,
                terminal_artifacts=tuple(terminal_refs),
            )
        )
    return (
        ConversationContextV2(turns=tuple(turns)),
        tuple(artifacts),
    )


def _request_identity(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "ArtifactReferenceV2",
    "CONVERSATION_CONTEXT_V2_VERSION",
    "ConversationContextV2",
    "TaskSummaryV2",
    "TurnSummaryV2",
    "migrate_legacy_context",
]
