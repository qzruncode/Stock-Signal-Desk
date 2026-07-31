"""Function group 3 extracted from src/agent/orchestrator_v2/runtime.py."""

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

__all__ = ['compile_intent_graph_v2', 'compile_workflow_call_v2']

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
