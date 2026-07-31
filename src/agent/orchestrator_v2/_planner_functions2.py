"""Function group 2 extracted from src/agent/orchestrator_v2/planner.py."""

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

__all__ = ['call_model_exact_v2', '_validate_outline_capability_contracts', '_validate_recovery_outline_contracts', '_collapse_subsumed_capabilities', '_available_artifacts']

async def call_model_exact_v2(
    *,
    llm_cfg: Mapping[str, Any],
    completion: Callable[..., Awaitable[Any]],
    function_name: str,
    description: str,
    model: type[BaseModel],
    system_prompt: str,
    semantic_context: Mapping[str, Any],
    node_id: str | None,
    value_validator: Callable[[BaseModel], None] | None = None,
    payload_normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    progress_observer: Callable[[int], Awaitable[None] | None] | None = None,
    provider_error_code: AgentErrorCode = AgentErrorCode.PLANNER_PROVIDER_FAILED,
    schema_error_code: AgentErrorCode = AgentErrorCode.PLANNER_SCHEMA_INVALID,
    max_tokens: int = 4_000,
    contract_transport: Literal["function", "json_content"] = "function",
) -> tuple[BaseModel, Any, RepairRecordV2 | None]:
    tool = _function_tool(function_name, description, model)
    first_payload: Any = None
    first_error: BaseException | None = None
    for attempt in range(2):
        request_context = dict(semantic_context)
        json_content_transport = contract_transport == "json_content" or (
            attempt == 1
            and isinstance(
                first_error,
                (
                    RawProviderPayloadError,
                    MissingProviderPayloadError,
                ),
            )
        )
        if attempt == 1:
            assert first_error is not None
            issues = _repair_issues(first_error, model)
            request_context["targeted_repair"] = {
                "invalid_payload": first_payload,
                "issues": [item.model_dump(mode="json") for item in issues],
                "instruction": (
                    "只修正列出的字段路径，保持其余字段和用户目标不变；"
                    + (
                        "通过最终 content 返回完整 JSON 对象。"
                        if json_content_transport
                        else "返回同一函数的完整参数对象。"
                    )
                ),
            }
        structured_kwargs: dict[str, Any] = {
            "messages": _exact_contract_messages(
                function_name=function_name,
                system_prompt=system_prompt,
                request_context=request_context,
                model=model,
                json_content_transport=json_content_transport,
            ),
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if not json_content_transport:
            structured_kwargs.update(
                {
                    "tools": [tool],
                    "tool_choice": {
                        "type": "function",
                        "function": {"name": function_name},
                    },
                }
            )
        kwargs = build_litellm_kwargs(
            dict(llm_cfg),
            stream=False,
            **structured_kwargs,
        )
        raw_payload: Any = None
        response: Any = None
        completion_task: asyncio.Task | None = None
        heartbeat: asyncio.Task | None = None
        try:
            # GuardedModelRuntime is the sole transport-retry owner. This
            # contract layer performs one request plus, when needed, one
            # schema-repair request with a different semantic purpose.
            completion_task = asyncio.create_task(completion(**kwargs))
            wait_started = time.monotonic()
            while True:
                heartbeat = asyncio.create_task(
                    asyncio.sleep(MODEL_PROGRESS_HEARTBEAT_SECONDS)
                )
                done, _ = await asyncio.wait(
                    {completion_task, heartbeat},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if completion_task in done:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                    heartbeat = None
                    response = completion_task.result()
                    break
                if progress_observer is not None:
                    progress_result = progress_observer(max(1, int(time.monotonic() - wait_started)))
                    if inspect.isawaitable(progress_result):
                        await progress_result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise OrchestratorV2Error(
                provider_error_code,
                (f"{function_name} provider request failed: " f"{type(exc).__name__}: {exc}"),
                task_id=node_id,
                metadata={
                    "provider_attempts": 1,
                },
            ) from exc
        finally:
            if heartbeat is not None and not heartbeat.done():
                heartbeat.cancel()
                await asyncio.gather(
                    heartbeat,
                    return_exceptions=True,
                )
            if completion_task is not None and not completion_task.done():
                completion_task.cancel()
                await asyncio.gather(
                    completion_task,
                    return_exceptions=True,
                )

        try:
            raw_payload = _payload_from_response(response, function_name)
            normalized_payload = _normalize_json_encoded_contract_fields(
                raw_payload,
                model,
            )
            payload = payload_normalizer(normalized_payload) if payload_normalizer is not None else normalized_payload
            validated = model.model_validate(payload)
            if value_validator is not None:
                value_validator(validated)
            repair = (
                RepairRecordV2(
                    node_id=node_id,
                    function_name=function_name,
                    invalid_payload=first_payload,
                    issues=_repair_issues(first_error, model),
                    succeeded=True,
                )
                if attempt == 1 and first_error is not None
                else None
            )
            return validated, raw_payload, repair
        except asyncio.CancelledError:
            raise
        except OrchestratorV2Error:
            raise
        except Exception as exc:
            if attempt == 0:
                first_payload = exc.payload if isinstance(exc, RawProviderPayloadError) else raw_payload
                first_error = exc
                continue
            raise OrchestratorV2Error(
                schema_error_code,
                (f"{function_name} remained invalid after one targeted repair: " f"{exc}"),
                task_id=node_id,
                metadata={
                    "repair": RepairRecordV2(
                        node_id=node_id,
                        function_name=function_name,
                        invalid_payload=first_payload,
                        issues=_repair_issues(first_error, model),
                        succeeded=False,
                    ).model_dump(mode="json"),
                },
            ) from exc
    raise AssertionError("unreachable")

def _validate_outline_capability_contracts(value: BaseModel) -> None:
    outline = IntentOutlineV2.model_validate(value)
    issues: list[RepairIssueV2] = []
    selected_specs = [capability_for(node.capability) for node in outline.nodes]
    covered_dimensions = frozenset(dimension for spec in selected_specs for dimension in spec.evidence_dimensions)
    required_dimensions = frozenset(
        dimension for claim in outline.goal.claims if claim.mandatory for dimension in claim.required_dimensions
    )
    missing_dimensions = tuple(
        sorted(
            required_dimensions - covered_dimensions,
            key=lambda item: item.value,
        )
    )
    if missing_dimensions and not outline.needs_clarification:
        issues.append(
            RepairIssueV2(
                pointer="/goal/claims",
                code="goal_evidence_coverage_incomplete",
                expected=(
                    "selected capabilities must jointly cover every required "
                    "evidence dimension of every mandatory claim"
                ),
                allowed=tuple(item.value for item in covered_dimensions),
                message=(
                    "capability graph does not cover required evidence dimensions: "
                    + ", ".join(item.value for item in missing_dimensions)
                ),
            )
        )
    if not outline.needs_clarification and not any(
        outline.goal.question_type in spec.supported_question_types for spec in selected_specs
    ):
        issues.append(
            RepairIssueV2(
                pointer="/goal/question_type",
                code="goal_question_type_unsupported",
                expected=(
                    "at least one selected terminal capability must support " f"{outline.goal.question_type.value}"
                ),
                allowed=tuple(
                    sorted({item.value for spec in selected_specs for item in spec.supported_question_types})
                ),
                message=(
                    "selected capabilities cannot produce the requested answer "
                    f"mode: {outline.goal.question_type.value}"
                ),
            )
        )
    for index, node in enumerate(outline.nodes):
        spec = capability_for(node.capability)
        if node.result_selection is not None and not spec.supports_result_selection:
            issues.append(
                RepairIssueV2(
                    pointer=f"/nodes/{index}/result_selection",
                    code="capability_result_selection_forbidden",
                    expected=("null because capability " f"{node.capability.value} does not support result selection"),
                    allowed=(None,),
                    message=(f"{node.capability.value} does not support result_selection"),
                )
            )
    if issues:
        raise ExactContractValidationError(tuple(issues))

def _validate_recovery_outline_contracts(
    value: BaseModel,
    *,
    fixed_goal: GoalContractV2,
    allowed_capabilities: frozenset[Capability],
    node_id_prefix: str,
) -> None:
    """Keep a repair round inside the frozen goal and read-only allowlist."""

    outline = IntentOutlineV2.model_validate(value)
    issues: list[RepairIssueV2] = []
    for index, node in enumerate(outline.nodes):
        spec = capability_for(node.capability)
        if node.result_selection is not None and not spec.supports_result_selection:
            issues.append(
                RepairIssueV2(
                    pointer=f"/nodes/{index}/result_selection",
                    code="capability_result_selection_forbidden",
                    expected=("null because capability " f"{node.capability.value} does not support result selection"),
                    allowed=(None,),
                    message=(f"{node.capability.value} does not support result_selection"),
                )
            )
    if outline.goal != fixed_goal:
        issues.append(
            RepairIssueV2(
                pointer="/goal",
                code="recovery_goal_changed",
                expected="the exact frozen Goal Contract from the initial plan",
                allowed=(fixed_goal.model_dump(mode="json"),),
                message="recovery planning cannot change the user's frozen goal",
            )
        )
    unexpected = tuple(node.capability for node in outline.nodes if node.capability not in allowed_capabilities)
    if unexpected:
        issues.append(
            RepairIssueV2(
                pointer="/nodes",
                code="recovery_capability_out_of_scope",
                expected="only program-proposed read-only recovery capabilities",
                allowed=tuple(sorted(item.value for item in allowed_capabilities)),
                message=(
                    "recovery plan selected capabilities outside the bounded "
                    "allowlist: " + ", ".join(item.value for item in unexpected)
                ),
            )
        )
    invalid_node_ids = tuple(node.node_id for node in outline.nodes if not node.node_id.startswith(node_id_prefix))
    if invalid_node_ids:
        issues.append(
            RepairIssueV2(
                pointer="/nodes",
                code="recovery_node_id_not_namespaced",
                expected=f"every recovery node id starts with {node_id_prefix}",
                allowed=(f"{node_id_prefix}<name>",),
                message=("recovery node ids must be namespaced for durable merging: " + ", ".join(invalid_node_ids)),
            )
        )
    if issues:
        raise ExactContractValidationError(tuple(issues))

def _collapse_subsumed_capabilities(
    outline: IntentOutlineV2,
) -> tuple[IntentOutlineV2, int]:
    """Collapse redundant siblings into their program-owned composite Workflow."""
    selected_composites = {
        node.capability for node in outline.nodes if capability_for(node.capability).subsumes_capabilities
    }
    if not selected_composites:
        return outline, 0
    by_id = {node.node_id: node for node in outline.nodes}
    removable = {
        node.node_id
        for node in outline.nodes
        if any(node.capability in capability_for(composite).subsumes_capabilities for composite in selected_composites)
    }
    for candidate_id in tuple(removable):
        candidate = by_id[candidate_id]
        for consumer in outline.nodes:
            if consumer.node_id in removable:
                continue
            references_candidate = any(
                ref.source == "node" and ref.node_id == candidate_id for ref in consumer.input_refs
            )
            if not references_candidate:
                continue
            consumer_spec = capability_for(consumer.capability)
            consumer_owns_candidate = (
                candidate.capability in consumer_spec.subsumes_capabilities and consumer_spec.allow_direct_entities
            )
            if not consumer_owns_candidate:
                removable.discard(candidate_id)
                break
    if not removable:
        return outline, 0
    nodes = [
        node.model_copy(
            update={
                "input_refs": tuple(
                    ref for ref in node.input_refs if not (ref.source == "node" and ref.node_id in removable)
                ),
            }
        )
        for node in outline.nodes
        if node.node_id not in removable
    ]
    collapsed = IntentOutlineV2.model_validate(
        {
            **outline.model_dump(mode="python"),
            "nodes": tuple(nodes),
        }
    )
    return collapsed, len(removable)

def _available_artifacts(
    semantic_context: Mapping[str, Any] | None,
) -> tuple[_AvailableArtifact, ...]:
    result: dict[str, _AvailableArtifact] = {}
    if not isinstance(semantic_context, Mapping):
        return ()
    for turn in semantic_context.get("turns") or []:
        if not isinstance(turn, Mapping):
            continue
        for artifact in turn.get("terminal_artifacts") or []:
            if not isinstance(artifact, Mapping):
                continue
            artifact_id = str(artifact.get("artifact_id") or "").strip()
            try:
                resource_type = ResourceType(artifact.get("resource_type"))
            except (TypeError, ValueError):
                continue
            if artifact_id:
                result[artifact_id] = _AvailableArtifact(
                    artifact_id=artifact_id,
                    resource_type=resource_type,
                    producer_node_id=str(artifact.get("producer_node_id") or "").strip(),
                )
    return tuple(result.values())
