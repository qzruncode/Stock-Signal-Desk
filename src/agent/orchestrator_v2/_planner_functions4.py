"""Function group 4 extracted from src/agent/orchestrator_v2/planner.py."""

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

__all__ = ['plan_intent_graph_v2']

async def plan_intent_graph_v2(
    messages: list[dict[str, Any]],
    llm_cfg: Mapping[str, Any],
    *,
    completion: Callable[..., Awaitable[Any]],
    semantic_context: Mapping[str, Any] | None = None,
    stage_observer: StageObserver | None = None,
    run_id: str | None = None,
    today: date | None = None,
    current_entities: list[dict[str, str]] | None = None,
    fixed_goal: GoalContractV2 | None = None,
    allowed_capabilities: tuple[Capability, ...] | None = None,
    plan_revision: int = 0,
) -> PlannedIntentGraphV2:
    """Produce a frozen V2 graph without an application-owned deadline."""
    active_run_id = run_id or uuid.uuid4().hex
    request = current_user_request(messages)
    if not request:
        raise ValueError("conversation has no user text")
    now = today or date.today()
    repairs: list[RepairRecordV2] = []
    durations: dict[str, int] = {}
    started = time.monotonic()
    raw_outline: Any = None
    raw_intents: dict[str, Any] = {}
    verification: PlannerVerificationV2 | None = None
    available_artifacts = _available_artifacts(semantic_context)
    recovery_allowlist = frozenset(allowed_capabilities or ()) if fixed_goal is not None else frozenset()
    if fixed_goal is not None and not recovery_allowlist:
        raise ValueError("recovery planning requires allowed_capabilities")

    async def execute() -> PlannedIntentGraphV2:
        nonlocal raw_outline, verification
        stage_started = time.monotonic()
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.OUTLINE,
            status=StageStatus.STARTED,
            summary="正在识别能力与资源关系",
        )
        outline_semantic_context = {
            "current_request": request,
            "conversation_context": dict(semantic_context or {}),
            "capability_catalog": capability_catalog(),
            "runtime_date": now.isoformat(),
            **(
                {
                    "recovery_mode": True,
                    "frozen_goal": fixed_goal.model_dump(mode="json"),
                    "allowed_capabilities": sorted(item.value for item in recovery_allowlist),
                    "plan_revision": plan_revision,
                    "recovery_rule": (
                        "Keep frozen_goal exactly unchanged and select "
                        "only allowed_capabilities that improve its "
                        "missing evidence coverage. Use node ids prefixed "
                        f"repair_{plan_revision}_"
                    ),
                }
                if fixed_goal is not None
                else {}
            ),
        }
        outline_validator = (
            (
                lambda value: _validate_recovery_outline_contracts(
                    value,
                    fixed_goal=fixed_goal,
                    allowed_capabilities=recovery_allowlist,
                    node_id_prefix=f"repair_{plan_revision}_",
                )
            )
            if fixed_goal is not None
            else _validate_outline_capability_contracts
        )
        outline_normalizer = lambda payload: _normalize_outline_resource_refs(
            payload,
            available_artifacts=available_artifacts,
        )
        outline_value, raw_outline, repair = await call_model_exact_v2(
            llm_cfg=llm_cfg,
            completion=completion,
            function_name="submit_intent_outline_v2",
            description="Submit only the capability graph and resource references.",
            model=IntentOutlineV2,
            system_prompt=_OUTLINE_SYSTEM_PROMPT,
            semantic_context=outline_semantic_context,
            node_id=None,
            value_validator=outline_validator,
            payload_normalizer=outline_normalizer,
            progress_observer=lambda elapsed: _emit(
                stage_observer,
                run_id=active_run_id,
                stage=AgentStage.OUTLINE,
                status=StageStatus.STARTED,
                summary=("模型正在识别能力与资源关系，" f"已持续分析 {elapsed} 秒"),
            ),
        )
        if repair is not None:
            repairs.append(repair)
        outline = IntentOutlineV2.model_validate(outline_value)
        if outline.needs_clarification:
            raise OrchestratorV2Error(
                AgentErrorCode.CLARIFICATION_REQUIRED,
                outline.clarification_question or "需要补充任务目标。",
            )
        outline, collapsed_count = _collapse_subsumed_capabilities(outline)
        outline = _bind_outline_resources(
            outline,
            available_artifacts={artifact.artifact_id: artifact.resource_type for artifact in available_artifacts},
            has_direct_entities=bool(current_entities),
        )
        verifier_mode = "off" if fixed_goal is not None else _planner_verifier_mode(outline.goal.question_type)
        if verifier_mode != "off":

            async def verify_candidate(
                candidate: IntentOutlineV2,
            ) -> PlannerVerificationV2:
                verification_value, _raw_verification, _repair = await call_model_exact_v2(
                    llm_cfg=llm_cfg,
                    completion=completion,
                    function_name="verify_intent_outline_v2",
                    description=(
                        "Independently verify semantic coverage, "
                        "minimality, and resource edges of the frozen "
                        "intent graph."
                    ),
                    model=PlannerVerificationV2,
                    system_prompt=_VERIFIER_SYSTEM_PROMPT,
                    semantic_context={
                        "current_request": request,
                        "frozen_outline": candidate.model_dump(mode="json"),
                        "capability_catalog": capability_catalog(),
                        "conversation_context": dict(semantic_context or {}),
                    },
                    node_id=None,
                    max_tokens=1_500,
                )
                return PlannerVerificationV2.model_validate(verification_value)

            verification = await verify_candidate(outline)
            minimum_confidence = _runtime_float(
                "AGENT_PLANNER_VERIFIER_MIN_CONFIDENCE",
                0.8,
                minimum=0.0,
            )

            def verifier_rejected(
                value: PlannerVerificationV2,
            ) -> bool:
                return (
                    not value.accepted
                    and value.confidence >= minimum_confidence
                    and bool(value.missing_capabilities or value.extraneous_node_ids or value.resource_issues)
                )

            rejected = verifier_rejected(verification)
            if verification.confidence < minimum_confidence or (
                not verification.accepted
                and not (
                    verification.missing_capabilities
                    or verification.extraneous_node_ids
                    or verification.resource_issues
                )
            ):
                logger.warning(
                    "[AgentPlanner] verifier abstained run=%s: %s",
                    active_run_id,
                    verification.model_dump(mode="json"),
                )
            if rejected and verifier_mode == "enforce":
                await _emit(
                    stage_observer,
                    run_id=active_run_id,
                    stage=AgentStage.OUTLINE,
                    status=StageStatus.STARTED,
                    summary="独立验收发现语义缺口，正在进行一次受控重规划",
                )
                replanned_value, raw_outline, replan_repair = await call_model_exact_v2(
                    llm_cfg=llm_cfg,
                    completion=completion,
                    function_name="submit_intent_outline_v2",
                    description=(
                        "Repair the Goal Contract and capability graph " "using the independent verifier feedback."
                    ),
                    model=IntentOutlineV2,
                    system_prompt=(
                        _OUTLINE_SYSTEM_PROMPT + "\n独立验收反馈只能用于修正当前目标与能力覆盖，" "不得扩大用户请求。"
                    ),
                    semantic_context={
                        **outline_semantic_context,
                        "independent_verifier_feedback": (verification.model_dump(mode="json")),
                        "replan_attempt": 1,
                    },
                    node_id=None,
                    value_validator=outline_validator,
                    payload_normalizer=outline_normalizer,
                )
                if replan_repair is not None:
                    repairs.append(replan_repair)
                outline = IntentOutlineV2.model_validate(replanned_value)
                if outline.needs_clarification:
                    raise OrchestratorV2Error(
                        AgentErrorCode.CLARIFICATION_REQUIRED,
                        outline.clarification_question or "需要补充任务目标。",
                    )
                outline, replan_collapsed = _collapse_subsumed_capabilities(outline)
                collapsed_count += replan_collapsed
                outline = _bind_outline_resources(
                    outline,
                    available_artifacts={
                        artifact.artifact_id: artifact.resource_type for artifact in available_artifacts
                    },
                    has_direct_entities=bool(current_entities),
                )
                verification = await verify_candidate(outline)
                rejected = verifier_rejected(verification)
                if rejected:
                    raise OrchestratorV2Error(
                        AgentErrorCode.PLANNER_SCHEMA_INVALID,
                        ("independent semantic verifier rejected the " "capability graph after one bounded replan"),
                        metadata={
                            "verification": verification.model_dump(mode="json"),
                            "minimum_confidence": minimum_confidence,
                        },
                    )
            elif rejected:
                logger.warning(
                    "[AgentPlanner] shadow verifier rejected run=%s: %s",
                    active_run_id,
                    verification.model_dump(mode="json"),
                )
        durations[AgentStage.OUTLINE.value] = int((time.monotonic() - stage_started) * 1000)
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.OUTLINE,
            status=StageStatus.SUCCEEDED,
            summary=(
                f"已冻结 {len(outline.nodes)} 个能力节点"
                + (f"，并入复合 Workflow {collapsed_count} 个重复节点" if collapsed_count else "")
            ),
        )

        parameter_started = time.monotonic()
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.PARAMETERIZATION,
            status=StageStatus.STARTED,
            summary="正在按节点填充精确业务 Schema",
        )

        parameter_semaphore = asyncio.Semaphore(
            _runtime_int(
                "AGENT_PLANNER_PARAMETER_CONCURRENCY",
                4,
                minimum=1,
                maximum=12,
            )
        )

        async def parameterize(
            node: IntentOutlineNodeV2,
        ) -> tuple[IntentOutlineNodeV2, BaseModel, Any, RepairRecordV2 | None]:
            spec = capability_for(node.capability)
            if not spec.intent_model.model_fields:
                empty = spec.intent_model.model_validate({})
                return node, empty, {}, None
            async with parameter_semaphore:
                value, raw, node_repair = await call_model_exact_v2(
                    llm_cfg=llm_cfg,
                    completion=completion,
                    function_name=f"submit_{node.capability.value}_intent_v2",
                    description=f"Submit semantic intent for {spec.title}.",
                    model=spec.intent_model,
                    system_prompt=_INTENT_SYSTEM_PROMPT,
                    semantic_context={
                        "current_request": request,
                        "frozen_node": node.model_dump(mode="json"),
                        "upstream_resources": [ref.model_dump(mode="json") for ref in node.input_refs],
                        "conversation_context": dict(semantic_context or {}),
                        "runtime_date": now.isoformat(),
                    },
                    node_id=node.node_id,
                    progress_observer=lambda elapsed: _emit(
                        stage_observer,
                        run_id=active_run_id,
                        stage=AgentStage.PARAMETERIZATION,
                        status=StageStatus.STARTED,
                        summary=(f"模型正在填写“{spec.title}”业务 Schema，" f"已持续分析 {elapsed} 秒"),
                    ),
                )
            return node, value, raw, node_repair

        parameterized = await asyncio.gather(*(parameterize(node) for node in outline.nodes))
        for node, _, raw, repair in parameterized:
            raw_intents[node.node_id] = raw
            if repair is not None:
                repairs.append(repair)
        durations[AgentStage.PARAMETERIZATION.value] = int((time.monotonic() - parameter_started) * 1000)
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.PARAMETERIZATION,
            status=StageStatus.SUCCEEDED,
            summary="所有能力参数已通过本地同源模型校验",
        )

        normalize_started = time.monotonic()
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.NORMALIZATION,
            status=StageStatus.STARTED,
            summary="正在应用程序默认值并记录假设",
        )
        planned_nodes: list[PlannedIntentNodeV2] = []
        all_assumptions: list[AssumptionRecord] = []
        normalized_intents: dict[str, Any] = {}
        for node, intent, _, _ in parameterized:
            normalized = normalize_capability_intent(
                node_id=node.node_id,
                objective=node.objective,
                capability=node.capability,
                intent=intent,
                input_refs=node.input_refs,
                result_selection=node.result_selection,
                current_year=now.year,
            )
            all_assumptions.extend(normalized.assumptions)
            normalized_intents[node.node_id] = {
                "semantic_intent": normalized.intent.model_dump(mode="json"),
                "execution_parameters": dict(normalized.execution_parameters),
            }
            planned_nodes.append(
                PlannedIntentNodeV2(
                    outline=node,
                    intent=normalized.intent,
                    execution_parameters=MappingProxyType(dict(normalized.execution_parameters)),
                    assumptions=normalized.assumptions,
                )
            )
        durations[AgentStage.NORMALIZATION.value] = int((time.monotonic() - normalize_started) * 1000)
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.NORMALIZATION,
            status=StageStatus.SUCCEEDED,
            summary=f"记录了 {len(all_assumptions)} 项程序假设",
        )
        durations["total"] = int((time.monotonic() - started) * 1000)
        trace = PlanningTraceV2(
            run_id=active_run_id,
            schema_version=V2_SCHEMA_VERSION,
            raw_outline=raw_outline,
            normalized_outline=outline.model_dump(mode="json"),
            raw_intents=raw_intents,
            normalized_intents=normalized_intents,
            assumptions=tuple(all_assumptions),
            repairs=tuple(repairs),
            verification=(verification.model_dump(mode="json") if verification is not None else None),
            goal_state={
                "goal": outline.goal.model_dump(mode="json"),
                "status": "planned",
            },
            plan_revision=plan_revision,
            stage_durations_ms=durations,
        )
        return PlannedIntentGraphV2(
            run_id=active_run_id,
            outline=outline,
            nodes=tuple(planned_nodes),
            trace=trace,
        )

    try:
        graph = await execute()
    except OrchestratorV2Error as exc:
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=(AgentStage.OUTLINE if raw_outline is None else AgentStage.PARAMETERIZATION),
            status=(
                StageStatus.BLOCKED
                if exc.code
                in {
                    AgentErrorCode.CLARIFICATION_REQUIRED,
                    AgentErrorCode.RESOURCE_UNAVAILABLE,
                }
                else StageStatus.FAILED
            ),
            task_id=exc.task_id,
            error_code=exc.code,
            summary=(
                "任务图未通过内部强类型契约校验；" "本轮没有调用任何数据工具"
                if exc.code == AgentErrorCode.PLANNER_SCHEMA_INVALID
                else str(exc)
            ),
        )
        raise
    return graph
