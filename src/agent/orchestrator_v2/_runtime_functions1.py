"""Function group 1 extracted from src/agent/orchestrator_v2/runtime.py."""

from __future__ import annotations

from src.agent.orchestrator_v2.runtime import (
    dataclass,
    replace,
    inspect,
    re,
    Any,
    Awaitable,
    Callable,
    Mapping,
    AgentArtifactV2,
    AssumptionRecord,
    CompiledCallV2,
    AgentErrorCode,
    AgentStage,
    AgentStageEventV2,
    ExecutionPolicy,
    FreshnessPolicy,
    Capability,
    PlanningTraceV2,
    OrchestratorV2Error,
    ResourceType,
    StageObserver,
    StageStatus,
    stable_fingerprint,
    PlannedIntentGraphV2,
    capability_for,
    DomainBoardQuerySpec,
    InvestmentThesisContext,
    SemanticResourceBindingUnavailableError,
    TaskPlanValidationError,
    bind_task_plan_resources,
    resolve_plan_entities,
    validate_candidate_plan,
    ConfirmationState,
    EntityScope,
    ResolvedTask,
    ResultSelectionMode,
    ResultSelectionSpec,
    StandardTask,
    StandardTaskKind,
    TaskResource,
    TaskPlan,
    WorkflowCall,
    workflow_for,
    ToolRegistry,
    CompiledTaskV2,
    CompiledIntentGraphV2,
    __all__,
 )

__all__ = ['serialize_compiled_intent_graph_v2', 'restore_compiled_intent_graph_v2', '_emit', '_workflow_result_selection', '_artifact_securities', '_artifact_resources', '_artifact_domains', '_node_domain_labels', '_node_domains', '_validated_domain_bindings', '_domain_identity_tokens', '_domain_item_identity_tokens']

def serialize_compiled_intent_graph_v2(
    graph: CompiledIntentGraphV2,
    *,
    planning_trace: PlanningTraceV2,
    request_fingerprint: str,
) -> dict[str, Any]:
    """Create a versioned, JSON-safe recovery checkpoint after compilation."""
    return {
        "checkpoint_version": "compiled-v2",
        "stage": "compiled",
        "request_fingerprint": request_fingerprint,
        "planning_trace": planning_trace.model_dump(mode="json"),
        "compiled_graph": {
            "run_id": graph.run_id,
            "plan": graph.plan.model_dump(mode="json"),
            "assumptions": [
                (item.model_dump(mode="json") if hasattr(item, "model_dump") else item) for item in graph.assumptions
            ],
            "tasks": [
                {
                    "task_id": item.task.task_id,
                    "symbols": list(item.task.symbols),
                    "entity_names": [list(value) for value in item.task.entity_names],
                    "capability": item.capability.value,
                    "capability_version": item.capability_version,
                    "intent_schema_version": item.intent_schema_version,
                    "execution_policy": item.execution_policy.model_dump(mode="json"),
                    "freshness_policy": item.freshness_policy.model_dump(mode="json"),
                    "resource_fingerprint": item.resource_fingerprint,
                    "input_artifact_ids": list(item.input_artifact_ids),
                }
                for item in graph.tasks
            ],
        },
    }

def restore_compiled_intent_graph_v2(
    checkpoint: Mapping[str, Any],
    *,
    expected_run_id: str,
    request_fingerprint: str,
) -> tuple[CompiledIntentGraphV2, PlanningTraceV2] | None:
    """Restore only a checkpoint matching this request and current contracts."""
    if (
        checkpoint.get("checkpoint_version") != "compiled-v2"
        or checkpoint.get("stage") != "compiled"
        or checkpoint.get("request_fingerprint") != request_fingerprint
    ):
        return None
    raw_graph = checkpoint.get("compiled_graph")
    raw_trace = checkpoint.get("planning_trace")
    if not isinstance(raw_graph, Mapping) or not isinstance(raw_trace, Mapping):
        return None
    if str(raw_graph.get("run_id") or "") != expected_run_id:
        return None
    try:
        plan = TaskPlan.model_validate(raw_graph.get("plan"))
        candidates = {task.task_id: task for task in plan.tasks}
        compiled_tasks: list[CompiledTaskV2] = []
        for raw in raw_graph.get("tasks") or ():
            if not isinstance(raw, Mapping):
                return None
            task_id = str(raw.get("task_id") or "")
            candidate = candidates.get(task_id)
            if candidate is None:
                return None
            capability = Capability(str(raw.get("capability") or ""))
            spec = capability_for(capability)
            if (
                candidate.kind.value != capability.value
                or raw.get("capability_version") != spec.version
                or raw.get("intent_schema_version") != spec.schema_version
            ):
                return None
            symbols = tuple(str(value) for value in raw.get("symbols") or ())
            execution_policy = ExecutionPolicy.model_validate(raw.get("execution_policy"))
            freshness_policy = FreshnessPolicy.model_validate(raw.get("freshness_policy"))
            confirmation_required = candidate.confirmation != ConfirmationState.NOT_REQUIRED or (
                candidate.kind == StandardTaskKind.BATCH_ANALYSIS and len(symbols) > 10
            )
            if (
                execution_policy
                != spec.execution_policy.model_copy(
                    update={
                        "confirmation_required": confirmation_required,
                    }
                )
                or freshness_policy != spec.freshness_policy
            ):
                return None
            resource_fingerprint = str(raw.get("resource_fingerprint") or "")
            if not resource_fingerprint:
                return None
            compiled_tasks.append(
                CompiledTaskV2(
                    task=ResolvedTask(
                        candidate=candidate,
                        symbols=symbols,
                        entity_names=tuple(
                            (str(value[0]), str(value[1]))
                            for value in raw.get("entity_names") or ()
                            if isinstance(value, (list, tuple)) and len(value) == 2
                        ),
                    ),
                    capability=capability,
                    capability_version=spec.version,
                    intent_schema_version=spec.schema_version,
                    execution_policy=execution_policy,
                    freshness_policy=freshness_policy,
                    resource_fingerprint=resource_fingerprint,
                    input_artifact_ids=tuple(str(value) for value in raw.get("input_artifact_ids") or ()),
                )
            )
        if len(compiled_tasks) != len(candidates) or set(candidates) != {item.task.task_id for item in compiled_tasks}:
            return None
        assumptions = tuple(AssumptionRecord.model_validate(item) for item in raw_graph.get("assumptions") or ())
        trace = PlanningTraceV2.model_validate(raw_trace)
    except (TypeError, ValueError):
        return None
    if trace.run_id != expected_run_id:
        return None
    return (
        CompiledIntentGraphV2(
            run_id=expected_run_id,
            plan=plan,
            tasks=tuple(compiled_tasks),
            assumptions=assumptions,
        ),
        trace,
    )

async def _emit(
    observer: StageObserver | None,
    event: AgentStageEventV2,
) -> None:
    if observer is None:
        return
    result = observer(event)
    if inspect.isawaitable(result):
        await result

def _workflow_result_selection(
    value: Any,
) -> ResultSelectionSpec | None:
    if value is None:
        return None
    mode = ResultSelectionMode(value.mode.value)
    max_items = value.max_items
    if mode == ResultSelectionMode.BEST_ONE:
        max_items = 1
    return ResultSelectionSpec(mode=mode, max_items=max_items)

def _artifact_securities(
    node: Any,
    artifacts: Mapping[str, AgentArtifactV2],
) -> list[str]:
    values: list[str] = []
    for ref in node.outline.input_refs:
        if ref.source != "artifact" or ref.artifact_id is None:
            continue
        artifact = artifacts.get(ref.artifact_id)
        if (
            artifact is None
            or artifact.resource_type != ResourceType.SECURITY_COLLECTION
            or not isinstance(artifact.payload, Mapping)
        ):
            continue
        raw = artifact.payload.get("securities")
        if not isinstance(raw, list):
            continue
        for item in raw:
            symbol = (str(item.get("symbol") or "") if isinstance(item, Mapping) else str(item or "")).strip()
            if symbol and symbol not in values:
                values.append(symbol)
    return values

def _artifact_resources(
    node: Any,
    artifacts: Mapping[str, AgentArtifactV2],
    resource_type: ResourceType,
) -> list[Any]:
    """Bind exact typed artifact payloads without falling back to generic results."""
    from src.agent.orchestrator_v2.state import ARTIFACT_SCHEMA_VERSION

    values: list[Any] = []
    for ref in node.outline.input_refs:
        if (
            ref.source != "artifact"
            or ref.artifact_id is None
            or ref.resource_type != resource_type
        ):
            continue
        artifact = artifacts.get(ref.artifact_id)
        if (
            artifact is None
            or artifact.schema_version != ARTIFACT_SCHEMA_VERSION
            or artifact.resource_type != resource_type
        ):
            continue
        payload = artifact.payload
        if isinstance(payload, list):
            values.extend(payload)
        elif payload not in (None, {}, ()):
            values.append(payload)
    return values

def _artifact_domains(
    node: Any,
    artifacts: Mapping[str, AgentArtifactV2],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Read typed domain resources, including the input artifact lineage."""
    domains: list[dict[str, Any]] = []
    root_topics: list[str] = []
    seen: set[str] = set()
    visited_artifacts: set[str] = set()

    def visit(artifact_id: str) -> None:
        if artifact_id in visited_artifacts:
            return
        visited_artifacts.add(artifact_id)
        artifact = artifacts.get(artifact_id)
        if artifact is None:
            return
        for lineage_id in artifact.lineage:
            visit(lineage_id)
        if artifact.resource_type != ResourceType.DOMAIN_COLLECTION:
            return
        payload = artifact.payload
        ranked = (
            payload.get("domain_collection_v2")
            if isinstance(payload, Mapping) and isinstance(payload.get("domain_collection_v2"), Mapping)
            else (
                payload.get("ranked_domains")
                if isinstance(payload, Mapping) and isinstance(payload.get("ranked_domains"), Mapping)
                else None
            )
        )
        raw_domains: Any = None
        if isinstance(ranked, Mapping):
            raw_topics = ranked.get("root_topics")
            if isinstance(raw_topics, list):
                root_topics.extend(str(value).strip() for value in raw_topics if str(value).strip())
            topic = str(ranked.get("topic") or "").strip()
            if not topic:
                topic = str(ranked.get("requested_topic") or "").strip()
            if topic:
                root_topics.append(topic)
            ranked_boards = ranked.get("boards")
            if isinstance(ranked_boards, list):
                snapshot_id = str(ranked.get("catalog_snapshot_id") or "").strip()
                raw_domains = []
                for raw_board in ranked_boards:
                    if not isinstance(raw_board, Mapping):
                        continue
                    board_id = str(raw_board.get("board_id") or raw_board.get("board_code") or "").strip()
                    board_name = str(raw_board.get("board_name") or raw_board.get("label") or "").strip()
                    if not board_id or not board_name:
                        continue
                    raw_domains.append(
                        {
                            "label": board_name,
                            "catalog_snapshot_id": snapshot_id or None,
                            "board_id": board_id,
                            "board_name": board_name,
                            "board_queries": [board_name],
                            "mapping_type": "catalog_binding",
                            "rationale": str(raw_board.get("rationale") or "").strip(),
                            "unresolved_parts": [],
                            "role_id": str(raw_board.get("role_id") or "").strip() or None,
                            "role_label": str(raw_board.get("role_label") or "").strip() or None,
                            "tier": raw_board.get("tier"),
                        }
                    )
        if raw_domains is None:
            raw_domains = payload.get("domains") if isinstance(payload, Mapping) else payload
        if not isinstance(raw_domains, list):
            return
        for raw in raw_domains:
            item = dict(raw) if isinstance(raw, Mapping) else {"label": str(raw or "").strip()}
            label = str(item.get("label") or "").strip()
            identity = str(item.get("board_id") or item.get("board_code") or label).strip()
            if not label or not identity or identity in seen:
                continue
            seen.add(identity)
            item["label"] = label
            domains.append(item)

    for ref in node.outline.input_refs:
        if ref.source != "artifact" or ref.artifact_id is None:
            continue
        visit(ref.artifact_id)
    if not domains:
        return [], None
    target_topics = list(dict.fromkeys(root_topics)) or [item["label"] for item in domains]
    return domains, {
        "target_topics": target_topics,
        "domain_theses": [
            {
                "label": item["label"],
                "rationale": str(item.get("rationale") or "").strip(),
                "tier": item.get("tier"),
            }
            for item in domains
        ],
    }

def _node_domain_labels(
    node: Any,
    nodes_by_id: Mapping[str, Any],
    *,
    visiting: frozenset[str] = frozenset(),
) -> list[str]:
    """Follow typed node edges to the nearest semantic domain producer."""
    node_id = node.outline.node_id
    if node_id in visiting:
        return []
    labels: list[str] = []
    for item in node.execution_parameters.get("domains") or []:
        label = (str(item.get("label") or "") if isinstance(item, Mapping) else str(item or "")).strip()
        if label and label not in labels:
            labels.append(label)
    if labels:
        return labels
    next_visiting = visiting | {node_id}
    for ref in node.outline.input_refs:
        if ref.source != "node" or ref.node_id is None:
            continue
        producer = nodes_by_id.get(ref.node_id)
        if producer is None:
            continue
        for label in _node_domain_labels(
            producer,
            nodes_by_id,
            visiting=next_visiting,
        ):
            if label not in labels:
                labels.append(label)
    return labels

def _node_domains(
    node: Any,
    nodes_by_id: Mapping[str, Any],
    *,
    visiting: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Follow same-run typed resources while retaining exact board bindings."""
    node_id = node.outline.node_id
    if node_id in visiting:
        return []
    domains: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in node.execution_parameters.get("domains") or []:
        item = dict(raw) if isinstance(raw, Mapping) else {"label": str(raw or "").strip()}
        label = str(item.get("label") or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        item["label"] = label
        domains.append(item)
    if domains:
        return domains
    next_visiting = visiting | {node_id}
    for ref in node.outline.input_refs:
        if ref.source != "node" or ref.node_id is None:
            continue
        producer = nodes_by_id.get(ref.node_id)
        if producer is None:
            continue
        for item in _node_domains(
            producer,
            nodes_by_id,
            visiting=next_visiting,
        ):
            label = str(item.get("label") or "").strip()
            if label and label not in seen:
                seen.add(label)
                domains.append(item)
    return domains

def _validated_domain_bindings(
    values: list[dict[str, Any]],
) -> list[DomainBoardQuerySpec]:
    """Drop ranking metadata at the typed board-binding boundary."""
    allowed = set(DomainBoardQuerySpec.model_fields)
    validated: list[DomainBoardQuerySpec] = []
    for item in values:
        try:
            validated.append(
                DomainBoardQuerySpec.model_validate({key: value for key, value in item.items() if key in allowed})
            )
        except Exception:
            continue
    return validated

def _domain_identity_tokens(value: Any) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    compact = "".join(text.split()).casefold()
    tokens = {compact}
    board_ids = re.findall(r"bk[a-z0-9_-]+", compact)
    tokens.update(board_ids)
    without_parenthetical_id = re.sub(
        r"[（(]\s*bk[a-z0-9_-]+\s*[）)]",
        "",
        compact,
    ).strip()
    if without_parenthetical_id:
        tokens.add(without_parenthetical_id)
    return tokens

def _domain_item_identity_tokens(item: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in (
        "label",
        "board_name",
        "board_code",
        "board_id",
        "role_id",
        "role_label",
    ):
        tokens.update(_domain_identity_tokens(item.get(key)))
    for value in item.get("board_queries") or ():
        tokens.update(_domain_identity_tokens(value))
    return tokens
