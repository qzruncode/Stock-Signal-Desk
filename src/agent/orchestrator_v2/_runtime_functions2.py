"""Function group 2 extracted from src/agent/orchestrator_v2/runtime.py."""

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

__all__ = ['_project_artifact_domain_subset', 'task_plan_from_v2', '_assert_structured_domains_available']

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
