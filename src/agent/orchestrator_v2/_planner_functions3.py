"""Function group 3 extracted from src/agent/orchestrator_v2/planner.py."""

from __future__ import annotations

from src.agent.orchestrator_v2.planner import (
    asyncio,
    dataclass,
    date,
    inspect,
    json,
    logging,
    os,
    re,
    time,
    MappingProxyType,
    Any,
    Awaitable,
    Callable,
    Literal,
    Mapping,
    uuid,
    BaseModel,
    ValidationError,
    AgentErrorCode,
    AgentStage,
    AgentStageEventV2,
    AssumptionRecord,
    GoalContractV2,
    InputReferenceV2,
    IntentOutlineNodeV2,
    IntentOutlineV2,
    Capability,
    OrchestratorV2Error,
    PlanningTraceV2,
    PlannerVerificationV2,
    QuestionType,
    RepairIssueV2,
    RepairRecordV2,
    ResourceType,
    StageObserver,
    StageStatus,
    capability_catalog,
    capability_for,
    normalize_capability_intent,
    current_user_request,
    build_litellm_kwargs,
    V2_SCHEMA_VERSION,
    MODEL_PROGRESS_HEARTBEAT_SECONDS,
    logger,
    RawProviderPayloadError,
    MissingProviderPayloadError,
    ExactContractValidationError,
    _AvailableArtifact,
    PlannedIntentNodeV2,
    PlannedIntentGraphV2,
    _OUTLINE_SYSTEM_PROMPT,
    _INTENT_SYSTEM_PROMPT,
    _VERIFIER_SYSTEM_PROMPT,
    __all__,
 )

__all__ = ['_normalize_outline_resource_refs', '_bind_outline_resources', '_planner_verifier_mode']

def _normalize_outline_resource_refs(
    payload: dict[str, Any],
    *,
    available_artifacts: tuple[_AvailableArtifact, ...],
) -> dict[str, Any]:
    """Resolve model-visible resource relations to program-owned artifact IDs.

    A provider sometimes copies ``producer_node_id`` from a previous turn and
    labels it as a node in the current graph. Historical nodes are not graph
    dependencies. Resolve that semantic producer reference only when it names
    one unique compatible artifact (or when there is only one compatible
    artifact at all). Never guess among multiple resources.
    """
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, (list, tuple)):
        return payload
    available_by_id = {artifact.artifact_id: artifact for artifact in available_artifacts}

    # Providers occasionally insert an identity bridge between a historical
    # resource and its real consumer: a node receives resource R even though
    # its capability cannot consume R, produces only R, and the next node
    # consumes R from that bridge. The bridge cannot execute as described and
    # used to survive after its invalid input edge was dropped, which caused a
    # fresh workflow and a second semantic binding pass. Collapse this
    # structurally invalid identity bridge back to the immutable artifact.
    bridge_artifacts: dict[str, tuple[str, ResourceType]] = {}
    raw_nodes_by_id = {
        str(node.get("node_id") or "").strip(): node
        for node in raw_nodes
        if isinstance(node, Mapping) and str(node.get("node_id") or "").strip()
    }
    for node_id, raw_node in raw_nodes_by_id.items():
        try:
            spec = capability_for(raw_node.get("capability"))
        except (KeyError, TypeError, ValueError):
            continue
        refs = raw_node.get("input_refs")
        if not isinstance(refs, (list, tuple)) or len(refs) != 1:
            continue
        raw_ref = refs[0]
        if not isinstance(raw_ref, Mapping):
            continue
        try:
            resource_type = ResourceType(raw_ref.get("resource_type"))
        except (TypeError, ValueError):
            continue
        artifact_id = str(raw_ref.get("artifact_id") or "").strip()
        artifact = available_by_id.get(artifact_id)
        if (
            str(raw_ref.get("source") or "").strip() != "artifact"
            or artifact is None
            or artifact.resource_type != resource_type
            or resource_type in spec.input_resources
            or spec.output_resources != frozenset({resource_type})
        ):
            continue
        consumer_refs = [
            ref
            for candidate_id, candidate in raw_nodes_by_id.items()
            if candidate_id != node_id
            for ref in candidate.get("input_refs") or ()
            if isinstance(ref, Mapping)
            and str(ref.get("source") or "node").strip() == "node"
            and str(ref.get("node_id") or "").strip() == node_id
        ]
        if not consumer_refs:
            continue
        if any(ref.get("resource_type") != resource_type.value for ref in consumer_refs):
            continue
        bridge_artifacts[node_id] = (artifact_id, resource_type)

    if bridge_artifacts:
        collapsed_nodes: list[Any] = []
        for raw_node in raw_nodes:
            if not isinstance(raw_node, Mapping):
                collapsed_nodes.append(raw_node)
                continue
            node_id = str(raw_node.get("node_id") or "").strip()
            if node_id in bridge_artifacts:
                continue
            node = dict(raw_node)
            refs: list[Any] = []
            for raw_ref in node.get("input_refs") or ():
                if not isinstance(raw_ref, Mapping):
                    refs.append(raw_ref)
                    continue
                bridge = bridge_artifacts.get(str(raw_ref.get("node_id") or "").strip())
                if (
                    str(raw_ref.get("source") or "node").strip() == "node"
                    and bridge is not None
                    and raw_ref.get("resource_type") == bridge[1].value
                ):
                    refs.append(
                        {
                            **raw_ref,
                            "source": "artifact",
                            "node_id": None,
                            "artifact_id": bridge[0],
                            "resource_type": bridge[1].value,
                        }
                    )
                else:
                    refs.append(raw_ref)
            node["input_refs"] = refs
            collapsed_nodes.append(node)
        raw_nodes = collapsed_nodes

    current_node_ids = {
        str(node.get("node_id") or "").strip()
        for node in raw_nodes
        if isinstance(node, Mapping) and str(node.get("node_id") or "").strip()
    }
    normalized_nodes: list[Any] = []
    issues: list[RepairIssueV2] = []
    for node_index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, Mapping):
            normalized_nodes.append(raw_node)
            continue
        node = dict(raw_node)
        raw_refs = node.get("input_refs")
        if not isinstance(raw_refs, (list, tuple)):
            normalized_nodes.append(node)
            continue
        try:
            capability_spec = capability_for(node.get("capability"))
        except (KeyError, TypeError, ValueError):
            capability_spec = None
        normalized_refs: list[Any] = []
        for ref_index, raw_ref in enumerate(raw_refs):
            if not isinstance(raw_ref, Mapping):
                normalized_refs.append(raw_ref)
                continue
            ref = dict(raw_ref)
            try:
                resource_type = ResourceType(ref.get("resource_type"))
            except (TypeError, ValueError):
                normalized_refs.append(ref)
                continue
            # Resource edges are program-owned. A model can describe a
            # semantically related but type-impossible extra edge (for
            # example evidence_collection on a direct-entity investment
            # decision). Such an edge can never be consumed by the selected
            # capability, so remove it deterministically instead of letting an
            # irrelevant model field abort the whole run.
            if capability_spec is not None and resource_type not in capability_spec.input_resources:
                continue
            source = str(ref.get("source") or "node").strip()
            node_id = str(ref.get("node_id") or "").strip()
            if source != "node" or not node_id or node_id in current_node_ids:
                normalized_refs.append(ref)
                continue
            compatible = tuple(artifact for artifact in available_artifacts if artifact.resource_type == resource_type)
            producer_matches = tuple(artifact for artifact in compatible if artifact.producer_node_id == node_id)
            candidates = producer_matches if producer_matches else compatible
            if len(candidates) == 1:
                artifact = candidates[0]
                normalized_refs.append(
                    {
                        **ref,
                        "source": "artifact",
                        "node_id": None,
                        "artifact_id": artifact.artifact_id,
                        "resource_type": resource_type.value,
                    }
                )
                continue
            if candidates:
                raise OrchestratorV2Error(
                    AgentErrorCode.CLARIFICATION_REQUIRED,
                    (f"找到多个可用的 {resource_type.value} 历史结果，" "请说明要使用哪一轮或哪一次筛选结果。"),
                    task_id=str(node.get("node_id") or "") or None,
                )
            issues.append(
                RepairIssueV2(
                    pointer=f"/nodes/{node_index}/input_refs/{ref_index}",
                    code="unknown_node_reference",
                    expected=("a current-graph node_id or one uniquely resolvable " "historical artifact"),
                    message=(
                        f"{node_id!r} is not a node in this graph and "
                        f"resolved to {len(candidates)} compatible artifacts"
                    ),
                )
            )
            normalized_refs.append(ref)
        node["input_refs"] = normalized_refs
        normalized_nodes.append(node)
    if issues:
        raise ExactContractValidationError(tuple(issues))
    return {
        **payload,
        "nodes": normalized_nodes,
    }

def _bind_outline_resources(
    outline: IntentOutlineV2,
    *,
    available_artifacts: Mapping[str, ResourceType],
    has_direct_entities: bool = False,
) -> IntentOutlineV2:
    """Validate explicit edges and deterministically add unambiguous bindings."""
    nodes = list(outline.nodes)
    by_id = {node.node_id: node for node in nodes}
    updated: list[IntentOutlineNodeV2] = []
    for node in nodes:
        spec = capability_for(node.capability)
        refs = list(node.input_refs)
        for ref in refs:
            if ref.resource_type not in spec.input_resources:
                raise OrchestratorV2Error(
                    AgentErrorCode.PLANNER_SCHEMA_INVALID,
                    (f"{node.node_id} does not accept " f"{ref.resource_type.value}"),
                    task_id=node.node_id,
                )
            if ref.source == "node":
                assert ref.node_id is not None
                producer = by_id[ref.node_id]
                producer_spec = capability_for(producer.capability)
                if ref.resource_type not in producer_spec.output_resources:
                    raise OrchestratorV2Error(
                        AgentErrorCode.PLANNER_SCHEMA_INVALID,
                        (f"{ref.node_id} does not produce " f"{ref.resource_type.value}"),
                        task_id=node.node_id,
                    )
            else:
                assert ref.artifact_id is not None
                actual = available_artifacts.get(ref.artifact_id)
                if actual != ref.resource_type:
                    raise OrchestratorV2Error(
                        AgentErrorCode.RESOURCE_UNAVAILABLE,
                        (f"artifact {ref.artifact_id} is missing or does not " f"contain {ref.resource_type.value}"),
                        task_id=node.node_id,
                    )
        bound_types = {ref.resource_type for ref in refs}
        for required in spec.required_input_resources - bound_types:
            if spec.allow_direct_entities and has_direct_entities:
                continue
            node_producers = [
                candidate
                for candidate in nodes
                if candidate.node_id != node.node_id
                and required in capability_for(candidate.capability).output_resources
            ]
            artifact_producers = [
                artifact_id for artifact_id, resource_type in available_artifacts.items() if resource_type == required
            ]
            if len(node_producers) + len(artifact_producers) == 1:
                if node_producers:
                    refs.append(
                        InputReferenceV2(
                            source="node",
                            node_id=node_producers[0].node_id,
                            resource_type=required,
                        )
                    )
                else:
                    refs.append(
                        InputReferenceV2(
                            source="artifact",
                            artifact_id=artifact_producers[0],
                            resource_type=required,
                        )
                    )
                continue
            if not node_producers and not artifact_producers and spec.allow_direct_entities:
                continue
            if len(node_producers) + len(artifact_producers) > 1:
                raise OrchestratorV2Error(
                    AgentErrorCode.CLARIFICATION_REQUIRED,
                    (f"{node.node_id} 有多个可用的 {required.value} 来源，" "请明确要使用哪一个集合。"),
                    task_id=node.node_id,
                )
            raise OrchestratorV2Error(
                AgentErrorCode.RESOURCE_UNAVAILABLE,
                (f"{node.node_id} 缺少 {required.value}，" "不会改用新闻或公网搜索生成替代集合。"),
                task_id=node.node_id,
            )
        bound_types = {ref.resource_type for ref in refs}
        for group in spec.alternative_input_resource_groups:
            if bound_types & group:
                continue
            node_producers = [
                (candidate, resource_type)
                for candidate in nodes
                if candidate.node_id != node.node_id
                for resource_type in group
                if resource_type
                in capability_for(candidate.capability).output_resources
            ]
            artifact_producers = [
                (artifact_id, resource_type)
                for artifact_id, resource_type in available_artifacts.items()
                if resource_type in group
            ]
            if len(node_producers) + len(artifact_producers) == 1:
                if node_producers:
                    producer, resource_type = node_producers[0]
                    refs.append(
                        InputReferenceV2(
                            source="node",
                            node_id=producer.node_id,
                            resource_type=resource_type,
                        )
                    )
                else:
                    artifact_id, resource_type = artifact_producers[0]
                    refs.append(
                        InputReferenceV2(
                            source="artifact",
                            artifact_id=artifact_id,
                            resource_type=resource_type,
                        )
                    )
                bound_types.add(resource_type)
                continue
            names = "/".join(
                item.value for item in sorted(group, key=lambda value: value.value)
            )
            if len(node_producers) + len(artifact_producers) > 1:
                raise OrchestratorV2Error(
                    AgentErrorCode.CLARIFICATION_REQUIRED,
                    f"{node.node_id} 有多个可用的 {names} 来源，请明确使用哪一个。",
                    task_id=node.node_id,
                )
            raise OrchestratorV2Error(
                AgentErrorCode.RESOURCE_UNAVAILABLE,
                f"{node.node_id} 至少需要一个 {names}，不会猜测或重新搜索条目。",
                task_id=node.node_id,
            )
        updated.append(node.model_copy(update={"input_refs": tuple(refs)}))
    # ``model_copy`` does not rerun graph validators. Re-validate the complete
    # outline after deterministic edges are added so automatic binding cannot
    # turn a valid provider graph into a cyclic execution graph.
    try:
        return IntentOutlineV2.model_validate(
            {
                **outline.model_dump(mode="python"),
                "nodes": tuple(updated),
            }
        )
    except ValidationError as exc:
        raise OrchestratorV2Error(
            AgentErrorCode.PLANNER_SCHEMA_INVALID,
            f"resource-bound intent graph is invalid: {exc}",
        ) from exc

def _planner_verifier_mode(
    question_type: QuestionType | None = None,
) -> str:
    configured = (os.getenv("AGENT_PLANNER_VERIFIER_MODE") or "").strip().lower()
    if configured in {"off", "shadow", "enforce"}:
        return configured
    environment = str(os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    if environment in {"prod", "production"}:
        return "enforce"
    # Forecasts are the highest-risk semantic mode: a superficially related
    # snapshot can easily be mistaken for evidence about the future. Source
    # and terminal-resource contracts are promoted to enforce mode by the
    # caller after the typed Goal Contract has been formed.
    return "enforce" if question_type == QuestionType.FORECAST else "off"
