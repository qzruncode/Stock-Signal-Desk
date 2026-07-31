# -*- coding: utf-8 -*-
"""Compilation bridge from frozen V2 intents to existing fixed workflows."""

from __future__ import annotations

from dataclasses import dataclass, replace
import inspect
import re
from typing import Any, Awaitable, Callable, Mapping

from src.agent.orchestrator_v2.contracts import (
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
)
from src.agent.orchestrator_v2.planner import PlannedIntentGraphV2
from src.agent.orchestrator_v2.registry import capability_for
from src.agent.result_contracts import (
    DomainBoardQuerySpec,
    InvestmentThesisContext,
)
from src.agent.task_planner import (
    SemanticResourceBindingUnavailableError,
    TaskPlanValidationError,
    bind_task_plan_resources,
    resolve_plan_entities,
    validate_candidate_plan,
)
from src.agent.task_workflows import (
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
)
from src.tools.registry import ToolRegistry


@dataclass(frozen=True)
class CompiledTaskV2:
    task: ResolvedTask
    capability: Capability
    capability_version: str
    intent_schema_version: str
    execution_policy: ExecutionPolicy
    resource_fingerprint: str
    freshness_policy: FreshnessPolicy = FreshnessPolicy()
    input_artifact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledIntentGraphV2:
    run_id: str
    plan: TaskPlan
    tasks: tuple[CompiledTaskV2, ...]
    assumptions: tuple[Any, ...]

    @property
    def resolved_tasks(self) -> list[ResolvedTask]:
        return [item.task for item in self.tasks]

    @property
    def policy_by_task_id(self) -> Mapping[str, ExecutionPolicy]:
        return {item.task.task_id: item.execution_policy for item in self.tasks}


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


def _project_artifact_domain_subset(
    domains: list[dict[str, Any]],
    requested: list[Any],
    *,
    selection_mode: str,
    task_id: str,
) -> list[dict[str, Any]]:
    """Project a typed collection without ever widening a named subset."""
    if selection_mode == "all_bound":
        return domains
    if selection_mode != "named_subset":
        raise OrchestratorV2Error(
            AgentErrorCode.PLANNER_SCHEMA_INVALID,
            f"{task_id} 缺少合法的领域集合选择模式。",
            task_id=task_id,
        )

    projected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    missing: list[str] = []
    for raw in requested:
        selector = dict(raw) if isinstance(raw, Mapping) else {"label": str(raw or "").strip()}
        selector_tokens = _domain_item_identity_tokens(selector)
        selector_label = str(
            selector.get("label")
            or selector.get("board_name")
            or selector.get("board_code")
            or selector.get("board_id")
            or ""
        ).strip()
        matches = [domain for domain in domains if selector_tokens & _domain_item_identity_tokens(domain)]
        if not matches:
            missing.append(selector_label or "<empty>")
            continue
        if len(matches) > 1:
            raise OrchestratorV2Error(
                AgentErrorCode.CLARIFICATION_REQUIRED,
                (f"{task_id} 中“{selector_label}”对应多个结构化板块，" "请明确具体板块。"),
                task_id=task_id,
            )
        match = matches[0]
        identity = (
            str(match.get("board_code") or match.get("board_id") or ""),
            str(match.get("label") or match.get("board_name") or ""),
        )
        if identity not in seen:
            seen.add(identity)
            projected.append(match)

    if missing:
        raise OrchestratorV2Error(
            AgentErrorCode.RESOURCE_UNAVAILABLE,
            (f"{task_id} 无法在已绑定领域集合中精确定位：" + "、".join(missing) + "；程序没有扩大为整个上游集合。"),
            task_id=task_id,
        )
    return projected


def task_plan_from_v2(
    graph: PlannedIntentGraphV2,
    *,
    artifacts: Mapping[str, AgentArtifactV2] | None = None,
) -> TaskPlan:
    """Project typed semantic nodes into the immutable workflow compiler."""
    tasks: list[StandardTask] = []
    nodes_by_id = {item.outline.node_id: item for item in graph.nodes}
    for node in graph.nodes:
        entities: list[str] = _artifact_securities(node, artifacts or {})
        artifact_domains, artifact_evidence_context = _artifact_domains(
            node,
            artifacts or {},
        )
        spec = capability_for(node.outline.capability)
        workflow = workflow_for(StandardTaskKind(node.outline.capability.value))
        parameters = dict(node.execution_parameters)
        domain_parameter = next(
            (
                parameter
                for parameter, resource in workflow.input_resource_parameters.items()
                if resource == TaskResource.DOMAIN_COLLECTION
            ),
            None,
        )
        if artifact_domains and domain_parameter is not None:
            selected_artifact_domains = artifact_domains
            if node.outline.capability == Capability.THEME_STOCK_DISCOVERY:
                selected_artifact_domains = _project_artifact_domain_subset(
                    artifact_domains,
                    list(parameters.get("domains") or ()),
                    selection_mode=str(getattr(node.intent, "selection_mode", "")),
                    task_id=node.outline.node_id,
                )
            typed_artifact_domains = _validated_domain_bindings(selected_artifact_domains)
            if typed_artifact_domains:
                parameters[domain_parameter] = [item.model_dump(exclude_none=True) for item in typed_artifact_domains]
        if node.outline.capability == Capability.THEME_BUSINESS_EVIDENCE:
            if artifact_evidence_context is not None:
                parameters["evidence_context"] = artifact_evidence_context
            domain_labels: list[str] = [item["label"] for item in artifact_domains]
            for ref in node.outline.input_refs:
                if ref.source != "node" or ref.node_id is None:
                    continue
                producer = nodes_by_id.get(ref.node_id)
                if producer is None:
                    continue
                for label in _node_domain_labels(producer, nodes_by_id):
                    if label and label not in domain_labels:
                        domain_labels.append(label)
            if domain_labels:
                parameters.setdefault(
                    "domains",
                    [{"label": label} for label in domain_labels],
                )
                parameters.setdefault(
                    "evidence_context",
                    {
                        "target_topics": domain_labels,
                        "domain_theses": [
                            {"label": label, "rationale": node.outline.objective} for label in domain_labels
                        ],
                    },
                )
        if node.outline.capability == Capability.INVESTMENT_DECISION:
            decision_domains = list(artifact_domains)
            known_labels = {
                str(item.get("label") or "").strip() for item in decision_domains if isinstance(item, Mapping)
            }
            for item in _node_domains(node, nodes_by_id):
                label = str(item.get("label") or "").strip()
                if label and label not in known_labels:
                    known_labels.add(label)
                    decision_domains.append(item)
            validated_domains = _validated_domain_bindings(decision_domains)
            if validated_domains:
                domain_labels = [domain.label for domain in validated_domains]
                context = InvestmentThesisContext(
                    summary="、".join(domain_labels),
                    domains=validated_domains,
                )
                parameters["thesis"] = "、".join(domain_labels)
                parameters["thesis_context"] = context.model_dump()
        confirmation_required = workflow.requires_confirmation(parameters)
        confirmation = (
            ConfirmationState.EXPLICIT
            if confirmation_required and getattr(node.intent, "user_confirmed", False)
            else ConfirmationState.MISSING if confirmation_required else ConfirmationState.NOT_REQUIRED
        )
        result_selection = _workflow_result_selection(node.outline.result_selection)
        if result_selection is not None and not spec.supports_result_selection:
            raise OrchestratorV2Error(
                AgentErrorCode.PLANNER_SCHEMA_INVALID,
                (f"{node.outline.capability.value}: result_selection is not " "supported by its capability contract"),
                task_id=node.outline.node_id,
            )
        if workflow.supports_result_selection and result_selection is None:
            result_selection = ResultSelectionSpec(
                mode=ResultSelectionMode.TOP_K,
                max_items=16,
            )
        tasks.append(
            StandardTask(
                task_id=node.outline.node_id,
                kind=StandardTaskKind(node.outline.capability.value),
                objective=node.outline.objective,
                entity_scope=(
                    EntityScope.CURRENT_MESSAGE if entities or spec.allow_direct_entities else EntityScope.NONE
                ),
                entities=entities,
                parameters=parameters,
                depends_on=list(
                    dict.fromkeys(
                        ref.node_id
                        for ref in node.outline.input_refs
                        if ref.source == "node" and ref.node_id is not None
                    )
                ),
                result_selection=result_selection,
                output_requirements=[],
                confirmation=confirmation,
                confidence=1.0,
            )
        )
    plan = TaskPlan(
        tasks=tasks,
        needs_clarification=False,
        clarification_question=None,
        source="unified_typed_control_plane",
    )
    try:
        validate_candidate_plan(plan, resources_bound=False)
    except TaskPlanValidationError as exc:
        raise OrchestratorV2Error(
            AgentErrorCode.PLANNER_SCHEMA_INVALID,
            f"typed task plan failed capability contract validation: {exc}",
        ) from exc
    return plan


def _assert_structured_domains_available(plan: TaskPlan) -> None:
    for task in plan.tasks:
        if task.kind != StandardTaskKind.THEME_STOCK_DISCOVERY:
            continue
        domains = task.parameters.get("domains")
        validated = [DomainBoardQuerySpec.model_validate(item) for item in domains or []]
        unavailable = [
            domain.label for domain in validated if domain.mapping_type != "catalog_binding" or not domain.board_queries
        ]
        if unavailable:
            raise OrchestratorV2Error(
                AgentErrorCode.RESOURCE_UNAVAILABLE,
                ("内部结构化板块目录无法绑定以下领域：" + "、".join(unavailable) + "；V2 未改用新闻或公网搜索。"),
                task_id=task.task_id,
            )


async def compile_intent_graph_v2(
    graph: PlannedIntentGraphV2,
    llm_cfg: Mapping[str, Any],
    *,
    completion: Callable[..., Awaitable[Any]],
    current_entities: list[dict[str, str]] | None = None,
    conversation_entities: list[dict[str, str]] | None = None,
    artifacts: Mapping[str, AgentArtifactV2] | None = None,
    stage_observer: StageObserver | None = None,
    registry: ToolRegistry | None = None,
) -> CompiledIntentGraphV2:
    """Bind live resources and compile program-owned execution specifications."""
    await _emit(
        stage_observer,
        AgentStageEventV2(
            run_id=graph.run_id,
            stage=AgentStage.RESOURCE_BINDING,
            status=StageStatus.STARTED,
            summary="正在绑定内部结构化资源",
        ),
    )
    try:
        plan = task_plan_from_v2(graph, artifacts=artifacts)
    except OrchestratorV2Error as exc:
        await _emit(
            stage_observer,
            AgentStageEventV2(
                run_id=graph.run_id,
                stage=AgentStage.RESOURCE_BINDING,
                status=StageStatus.FAILED,
                task_id=exc.task_id,
                error_code=exc.code,
                summary=str(exc),
            ),
        )
        raise
    try:
        bound = await bind_task_plan_resources(
            plan,
            llm_cfg,
            completion=completion,
        )
        _assert_structured_domains_available(bound)
    except OrchestratorV2Error:
        await _emit(
            stage_observer,
            AgentStageEventV2(
                run_id=graph.run_id,
                stage=AgentStage.RESOURCE_BINDING,
                status=StageStatus.FAILED,
                error_code=AgentErrorCode.RESOURCE_UNAVAILABLE,
                summary="结构化资源不可用",
            ),
        )
        raise
    except SemanticResourceBindingUnavailableError as exc:
        await _emit(
            stage_observer,
            AgentStageEventV2(
                run_id=graph.run_id,
                stage=AgentStage.RESOURCE_BINDING,
                status=StageStatus.FAILED,
                error_code=AgentErrorCode.RESOURCE_UNAVAILABLE,
                summary=str(exc),
            ),
        )
        raise OrchestratorV2Error(
            AgentErrorCode.RESOURCE_UNAVAILABLE,
            str(exc),
        ) from exc
    except (TaskPlanValidationError, ValueError) as exc:
        await _emit(
            stage_observer,
            AgentStageEventV2(
                run_id=graph.run_id,
                stage=AgentStage.RESOURCE_BINDING,
                status=StageStatus.FAILED,
                error_code=AgentErrorCode.PLANNER_SCHEMA_INVALID,
                summary=str(exc),
            ),
        )
        raise OrchestratorV2Error(
            AgentErrorCode.PLANNER_SCHEMA_INVALID,
            f"bound typed graph failed validation: {exc}",
        ) from exc
    await _emit(
        stage_observer,
        AgentStageEventV2(
            run_id=graph.run_id,
            stage=AgentStage.RESOURCE_BINDING,
            status=StageStatus.SUCCEEDED,
            summary="结构化资源绑定完成",
        ),
    )

    await _emit(
        stage_observer,
        AgentStageEventV2(
            run_id=graph.run_id,
            stage=AgentStage.COMPILATION,
            status=StageStatus.STARTED,
            summary="正在生成不可变 Workflow 规格",
        ),
    )
    try:
        resolved = resolve_plan_entities(
            bound,
            current_entities=current_entities or [],
            previous_answer_entities=[],
            conversation_entities=conversation_entities or [],
        )
    except TaskPlanValidationError as exc:
        raise OrchestratorV2Error(
            AgentErrorCode.RESOURCE_UNAVAILABLE,
            str(exc),
        ) from exc

    outline_by_id = {node.outline.node_id: node for node in graph.nodes}
    compiled: list[CompiledTaskV2] = []
    typed_registry = registry or ToolRegistry()
    await _emit(
        stage_observer,
        AgentStageEventV2(
            run_id=graph.run_id,
            stage=AgentStage.POLICY,
            status=StageStatus.STARTED,
            summary="正在核对能力预算、权限和强类型工具边界",
        ),
    )
    for task in resolved:
        node = outline_by_id[task.task_id]
        spec = capability_for(node.outline.capability)
        if (
            task.kind == StandardTaskKind.BATCH_ANALYSIS
            and len(task.symbols) > 10
            and task.candidate.confirmation == ConfirmationState.NOT_REQUIRED
        ):
            task = replace(
                task,
                candidate=task.candidate.model_copy(
                    update={
                        "confirmation": (
                            ConfirmationState.EXPLICIT
                            if getattr(node.intent, "user_confirmed", False)
                            else ConfirmationState.MISSING
                        ),
                    }
                ),
            )
        workflow_spec = workflow_for(task.kind)
        untyped_tools = [
            tool_name
            for tool_name in workflow_spec.tool_whitelist
            if (
                (tool := typed_registry.get_tool(tool_name)) is None
                or tool.args_model is None
                or tool.result_model is None
            )
        ]
        if untyped_tools:
            await _emit(
                stage_observer,
                AgentStageEventV2(
                    run_id=graph.run_id,
                    stage=AgentStage.POLICY,
                    status=StageStatus.FAILED,
                    task_id=task.task_id,
                    error_code=AgentErrorCode.POLICY_BLOCKED,
                    summary="能力尚未完成强类型工具迁移",
                ),
            )
            raise OrchestratorV2Error(
                AgentErrorCode.POLICY_BLOCKED,
                (f"{task.task_id} has tools without typed adapters: " f"{sorted(untyped_tools)}"),
                task_id=task.task_id,
            )
        confirmation_required = task.candidate.confirmation != ConfirmationState.NOT_REQUIRED or (
            task.kind == StandardTaskKind.BATCH_ANALYSIS and len(task.symbols) > 10
        )
        execution_policy = spec.execution_policy.model_copy(
            update={
                "confirmation_required": confirmation_required,
            }
        )
        compiled.append(
            CompiledTaskV2(
                task=task,
                capability=node.outline.capability,
                capability_version=spec.version,
                intent_schema_version=spec.schema_version,
                execution_policy=execution_policy,
                resource_fingerprint=stable_fingerprint(
                    {
                        "input_refs": [ref.model_dump(mode="json") for ref in node.outline.input_refs],
                        "symbols": list(task.symbols),
                        "parameters": dict(task.parameters),
                    }
                ),
                freshness_policy=spec.freshness_policy,
                input_artifact_ids=tuple(
                    ref.artifact_id
                    for ref in node.outline.input_refs
                    if ref.source == "artifact" and ref.artifact_id is not None
                ),
            )
        )
    await _emit(
        stage_observer,
        AgentStageEventV2(
            run_id=graph.run_id,
            stage=AgentStage.COMPILATION,
            status=StageStatus.SUCCEEDED,
            summary=f"已编译 {len(compiled)} 个 Workflow 节点",
        ),
    )
    # Tool adapters, effects and call budgets were checked while constructing
    # each immutable CompiledTaskV2 above; no user/model field can override
    # those policies.
    await _emit(
        stage_observer,
        AgentStageEventV2(
            run_id=graph.run_id,
            stage=AgentStage.POLICY,
            status=StageStatus.SUCCEEDED,
            summary="Policy 预检通过",
        ),
    )
    return CompiledIntentGraphV2(
        run_id=graph.run_id,
        plan=bound,
        tasks=tuple(compiled),
        assumptions=tuple(assumption for node in graph.nodes for assumption in node.assumptions),
    )


def compile_workflow_call_v2(
    task: CompiledTaskV2,
    call: WorkflowCall,
    arguments: Mapping[str, Any],
    *,
    registry: ToolRegistry,
) -> CompiledCallV2[Any]:
    """Validate one dynamically resource-bound call into its typed tool args."""
    tool = registry.get_tool(call.tool_name)
    if tool is None or tool.args_model is None or tool.result_model is None:
        raise OrchestratorV2Error(
            AgentErrorCode.POLICY_BLOCKED,
            (f"{call.tool_name} has no typed args/result adapter and " "cannot enter unified execution"),
            task_id=task.task.task_id,
        )
    typed_arguments = tool.args_model.model_validate(arguments)
    idempotency_key = stable_fingerprint(
        {
            "capability": task.capability.value,
            "capability_version": task.capability_version,
            "intent_schema_version": task.intent_schema_version,
            "resource_fingerprint": task.resource_fingerprint,
            "step_id": call.step_id,
            "arguments": typed_arguments.model_dump(mode="json"),
        }
    )
    return CompiledCallV2(
        task_id=call.task_id,
        step_id=call.step_id,
        tool_name=call.tool_name,
        arguments=typed_arguments,
        depends_on_steps=call.depends_on_steps,
        after_steps=call.after_steps,
        result_bindings=call.result_bindings,
        idempotency_key=idempotency_key,
    )


__all__ = [
    "CompiledIntentGraphV2",
    "CompiledTaskV2",
    "compile_intent_graph_v2",
    "compile_workflow_call_v2",
    "restore_compiled_intent_graph_v2",
    "serialize_compiled_intent_graph_v2",
    "task_plan_from_v2",
]
