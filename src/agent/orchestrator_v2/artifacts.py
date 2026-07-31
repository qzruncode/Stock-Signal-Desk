# -*- coding: utf-8 -*-
"""Project completed typed execution into independent artifacts and lean context."""

from __future__ import annotations

from datetime import datetime, timezone
from src.agent.orchestrator_v2.contracts import (
    AgentArtifactV2,
    EvidenceV2,
    ResourceType,
    OutcomeStatus,
    TaskOutcomeV2,
    stable_fingerprint,
)
from src.agent.orchestrator_v2.runtime import CompiledIntentGraphV2
from src.agent.orchestrator_v2.registry import capability_for
from src.agent.orchestrator_v2.state import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactReferenceV2,
    TaskSummaryV2,
    TurnSummaryV2,
)
from src.agent.task_executor import action_fingerprint
from src.agent.task_workflows import ConfirmationState


def build_execution_artifacts_v2(
    compiled: CompiledIntentGraphV2,
    outcomes: tuple[TaskOutcomeV2, ...],
    *,
    conversation_id: str,
    request: str,
    request_message_id: str | None,
) -> tuple[tuple[AgentArtifactV2, ...], TurnSummaryV2]:
    outcome_by_id = {outcome.task_id: outcome for outcome in outcomes}
    artifact_by_node_resource: dict[
        tuple[str, ResourceType],
        AgentArtifactV2,
    ] = {}
    artifacts: list[AgentArtifactV2] = []
    now = datetime.now(timezone.utc)

    for compiled_task in compiled.tasks:
        outcome = outcome_by_id[compiled_task.task.task_id]
        if outcome.status not in {
            OutcomeStatus.SUCCEEDED,
            OutcomeStatus.PARTIAL,
        }:
            continue
        capability_spec = capability_for(compiled_task.capability)
        spec_resources = sorted(
            capability_spec.output_resources,
            key=lambda item: item.value,
        )
        for resource_type in spec_resources:
            projected = capability_spec.projector(outcome, resource_type)
            if projected is None:
                continue
            payload = projected.payload
            fingerprint = stable_fingerprint(
                {
                    "capability": compiled_task.capability.value,
                    "capability_version": compiled_task.capability_version,
                    "schema_version": compiled_task.intent_schema_version,
                    "resource_type": resource_type.value,
                    "resource_fingerprint": compiled_task.resource_fingerprint,
                    "payload": payload,
                }
            )
            artifact = AgentArtifactV2(
                artifact_id="artifact_"
                + stable_fingerprint(
                    {
                        "conversation_id": conversation_id,
                        "fingerprint": fingerprint,
                    }
                )[:32],
                schema_version=ARTIFACT_SCHEMA_VERSION,
                run_id=compiled.run_id,
                conversation_id=conversation_id,
                producer_node_id=compiled_task.task.task_id,
                resource_type=resource_type,
                coverage=projected.coverage,
                sources=(
                    outcome.evidence
                    or (
                        EvidenceV2(
                            source="validated_workflow_execution",
                            observed_at=now,
                            summary=(f"{compiled_task.capability.value} " f"{outcome.status.value}"),
                        ),
                    )
                ),
                produced_at=now,
                fingerprint=fingerprint,
                lineage=compiled_task.input_artifact_ids,
                payload=payload,
            )
            artifacts.append(artifact)
            artifact_by_node_resource[(compiled_task.task.task_id, resource_type)] = artifact

    artifacts_with_lineage: list[AgentArtifactV2] = []
    compiled_by_id = {item.task.task_id: item for item in compiled.tasks}
    for artifact in artifacts:
        task = compiled_by_id[artifact.producer_node_id]
        dependency_artifacts = [
            value.artifact_id
            for dependency_id in task.task.candidate.depends_on
            for (producer_id, _), value in artifact_by_node_resource.items()
            if producer_id == dependency_id
        ]
        lineage = tuple(
            dict.fromkeys(
                [
                    *artifact.lineage,
                    *dependency_artifacts,
                ]
            )
        )
        updated = artifact.model_copy(update={"lineage": lineage})
        artifact_by_node_resource[(updated.producer_node_id, updated.resource_type)] = updated
        artifacts_with_lineage.append(updated)
    artifacts = artifacts_with_lineage

    consumers: set[tuple[str, ResourceType]] = set()
    # A resource is terminal when no later compiled node consumes that exact
    # producer/resource pair. StandardTask dependencies are the executable
    # projection of node input references.
    for task in compiled.plan.tasks:
        for dependency_id in task.depends_on:
            input_resources = capability_for(compiled_by_id[task.task_id].capability).input_resources
            for resource_type in input_resources:
                if (dependency_id, resource_type) in artifact_by_node_resource:
                    consumers.add((dependency_id, resource_type))

    terminal_artifacts = tuple(artifact for key, artifact in artifact_by_node_resource.items() if key not in consumers)
    terminal_refs = tuple(
        ArtifactReferenceV2(
            artifact_id=artifact.artifact_id,
            resource_type=artifact.resource_type,
            fingerprint=artifact.fingerprint,
            producer_node_id=artifact.producer_node_id,
        )
        for artifact in terminal_artifacts
    )
    refs_by_node: dict[str, list[str]] = {}
    for artifact in terminal_artifacts:
        refs_by_node.setdefault(artifact.producer_node_id, []).append(artifact.artifact_id)
    turn = TurnSummaryV2(
        run_id=compiled.run_id,
        request_message_id=request_message_id,
        request_summary=request[:2_000],
        tasks=tuple(
            TaskSummaryV2(
                node_id=task.task.task_id,
                capability=task.capability.value,
                objective=task.task.candidate.objective,
                status=outcome_by_id[task.task.task_id].status.value,
                artifact_refs=tuple(refs_by_node.get(task.task.task_id, [])),
                coverage=outcome_by_id[task.task.task_id].coverage,
                action_fingerprint=(
                    action_fingerprint(task.task)
                    if task.task.candidate.confirmation != ConfirmationState.NOT_REQUIRED
                    else None
                ),
                confirmation_pending=(
                    outcome_by_id[task.task.task_id].status == OutcomeStatus.BLOCKED
                    and task.task.candidate.confirmation == ConfirmationState.MISSING
                ),
            )
            for task in compiled.tasks
        ),
        terminal_artifacts=terminal_refs,
        completed_at=now,
    )
    # Intermediate candidate sets stay inside the current run. Only resources
    # that no later node consumed become cross-turn artifacts.
    return terminal_artifacts, turn


def attach_artifact_refs_v2(
    outcomes: tuple[TaskOutcomeV2, ...],
    artifacts: tuple[AgentArtifactV2, ...],
) -> tuple[TaskOutcomeV2, ...]:
    """Return outcomes whose artifact references match persisted resources."""
    refs_by_task: dict[str, list[str]] = {}
    for artifact in artifacts:
        refs_by_task.setdefault(artifact.producer_node_id, []).append(artifact.artifact_id)
    return tuple(
        outcome.model_copy(
            update={
                "artifact_refs": tuple(refs_by_task.get(outcome.task_id, ())),
            }
        )
        for outcome in outcomes
    )


__all__ = [
    "attach_artifact_refs_v2",
    "build_execution_artifacts_v2",
]
