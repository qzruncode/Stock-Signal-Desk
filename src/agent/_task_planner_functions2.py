"""Function group 2 extracted from src/agent/task_planner.py."""

from __future__ import annotations

from src.agent.task_planner import (
    asyncio,
    json,
    logging,
    Any,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    ValidationError,
    CollectionFinancialFilterSpec,
    DomainBoardQuerySpec,
    InvestmentThesisContext,
    ThemeEvidenceContext,
    ConfirmationState,
    EntityScope,
    ResultSelectionMode,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
    TaskResource,
    parameter_requirement_issues,
    workflow_for,
    build_litellm_kwargs,
    QuantitativeScreenSpec,
    resolve_securities_csv,
    logger,
    SemanticResourceBindingUnavailableError,
    TaskPlanValidationError,
    _DOMAIN_SCHEMA,
    _RESOURCE_BINDING_TOOL,
    _RESOURCE_BINDING_SYSTEM_PROMPT,
    __all__,
 )

__all__ = ['_bind_concept_board_catalog', 'bind_task_plan_resources', '_dedupe_entities', '_resolve_task_entities', 'resolve_plan_entities']

async def _bind_concept_board_catalog(
    plan: TaskPlan,
    llm_cfg: Mapping[str, Any],
    completion: Callable[..., Awaitable[Any]],
    *,
    trusted_domain_inputs: (
        Mapping[
            str,
            list[dict[str, Any]],
        ]
        | None
    ) = None,
) -> tuple[TaskPlan, set[str]]:
    target_tasks = [
        task
        for task in plan.tasks
        if "concept_board_catalog" in workflow_for(task.kind).resource_bindings
        # Industry research consumes the complete catalog in its fixed
        # workflow and selects board IDs in the result processor. Pre-mapping
        # its semantic topic here would be a duplicate model binding pass.
        and task.kind != StandardTaskKind.INDUSTRY_RESEARCH and _semantic_domain_labels(task)
    ]
    target_tasks = [task for task in target_tasks if not _has_complete_catalog_bindings(task)]
    if not target_tasks:
        return plan, set()

    try:
        from src.services.domain_board_catalog import get_domain_board_catalog

        catalog = await asyncio.to_thread(get_domain_board_catalog)
    except Exception as exc:
        logger.warning("[TaskPlanner] concept board catalog unavailable: %s", exc)
        raise SemanticResourceBindingUnavailableError(
            f"live concept-board catalog unavailable: {type(exc).__name__}"
        ) from exc

    board_names = [str(value).strip() for value in catalog.get("board_names") or [] if str(value).strip()]
    board_name_set = set(board_names)
    if not board_names:
        errors = "；".join(str(value) for value in catalog.get("errors") or [])
        raise SemanticResourceBindingUnavailableError(errors or "live concept-board catalog is empty")

    exact_bindings, unresolved_labels = _exact_catalog_resource_bindings(
        target_tasks,
        board_names,
        trusted_domain_inputs,
    )
    semantic_tasks = [task for task in target_tasks if unresolved_labels[task.task_id]]
    if not semantic_tasks:
        return (
            _merge_resource_bindings(
                plan,
                exact=exact_bindings,
                semantic={},
                unresolved_reason="",
            ),
            board_name_set,
        )

    context = {
        "tasks": [
            {
                "task_id": task.task_id,
                "objective": task.objective,
                "semantic_domains": unresolved_labels[task.task_id],
            }
            for task in semantic_tasks
        ],
        "catalog": {
            "board_names": board_names,
            "count": len(board_names),
            "source": catalog.get("source"),
            "data_time": catalog.get("data_time"),
        },
    }
    validation_error = ""
    last_error: Exception | None = None
    for attempt in range(1):
        binding_context = dict(context)
        if validation_error:
            binding_context["previous_validation_error"] = validation_error
            binding_context["instruction"] = "修正绑定结构；不得改变输入任务或语义领域。"
        kwargs = build_litellm_kwargs(
            dict(llm_cfg),
            stream=False,
            messages=[
                {"role": "system", "content": _RESOURCE_BINDING_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(binding_context, ensure_ascii=False)},
            ],
            tools=[_RESOURCE_BINDING_TOOL],
            tool_choice={
                "type": "function",
                "function": {"name": "submit_semantic_resource_bindings"},
            },
            temperature=0,
            max_tokens=2_400,
            **_planner_generation_overrides(),
        )
        try:
            response = await completion(**kwargs)
            payload = _payload_from_response(response, "submit_semantic_resource_bindings")
            raw_bindings = payload.get("bindings")
            if not isinstance(raw_bindings, list):
                raise ValueError("bindings must be an array")
            by_task: dict[str, dict[str, dict[str, Any]]] = {}
            for raw in raw_bindings:
                if not isinstance(raw, Mapping):
                    raise ValueError("each binding must be an object")
                task_id = str(raw.get("task_id") or "")
                if task_id in by_task:
                    raise ValueError(f"duplicate binding for {task_id}")
                domains = raw.get("domains")
                if not isinstance(domains, list):
                    raise ValueError(f"{task_id} domains must be an array")
                validated = [DomainBoardQuerySpec.model_validate(item) for item in domains]
                unknown = [
                    board for domain in validated for board in domain.board_queries if board not in board_name_set
                ]
                if unknown:
                    raise ValueError(f"{task_id} selected catalog-absent boards {unknown}")
                expected_labels = unresolved_labels.get(task_id)
                if expected_labels is None:
                    raise ValueError(f"unexpected binding task id {task_id}")
                returned_labels = [domain.label for domain in validated]
                if returned_labels != expected_labels:
                    raise ValueError(f"{task_id} must preserve domain labels and order: {expected_labels}")
                by_task[task_id] = {domain.label: domain.model_dump() for domain in validated}
            expected_ids = {task.task_id for task in semantic_tasks}
            if set(by_task) != expected_ids:
                raise ValueError(f"binding task ids must equal {sorted(expected_ids)}, got {sorted(by_task)}")
            return (
                _merge_resource_bindings(
                    plan,
                    exact=exact_bindings,
                    semantic=by_task,
                    unresolved_reason="",
                ),
                board_name_set,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            validation_error = str(exc)
            logger.warning(
                "[TaskPlanner] resource binding attempt=%d failed=%s",
                attempt + 1,
                type(exc).__name__,
            )
    logger.warning(
        "[TaskPlanner] semantic resource binding unavailable after bounded attempt: %s",
        validation_error[:300],
    )
    raise SemanticResourceBindingUnavailableError(
        ("live semantic resource binding did not complete: " f"{type(last_error).__name__}")
        if last_error is not None
        else "live semantic resource binding did not complete"
    ) from last_error

async def bind_task_plan_resources(
    plan: TaskPlan,
    llm_cfg: Mapping[str, Any],
    *,
    completion: Callable[..., Awaitable[Any]],
) -> TaskPlan:
    """Bind live program resources for an already frozen typed graph."""
    if plan.needs_clarification:
        return plan
    validate_candidate_plan(plan, resources_bound=False)
    bound_plan, board_names = await _bind_concept_board_catalog(
        plan,
        llm_cfg,
        completion,
    )
    validate_candidate_plan(
        bound_plan,
        concept_board_names=board_names or None,
        resources_bound=True,
    )
    return bound_plan

def _dedupe_entities(values: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        symbol = str(value.get("symbol") or "").strip()
        name = str(value.get("name") or symbol).strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append({"symbol": symbol, "name": name})
    return result

def _resolve_task_entities(values: Iterable[str]) -> tuple[list[dict[str, str]], list[str]]:
    resolved, unresolved = resolve_securities_csv("，".join(str(value) for value in values))
    return (
        [{"symbol": item["symbol"], "name": item["name"]} for item in resolved],
        unresolved,
    )

def resolve_plan_entities(
    plan: TaskPlan,
    *,
    current_entities: list[dict[str, str]],
    previous_answer_entities: list[dict[str, str]],
    conversation_entities: list[dict[str, str]] | None = None,
) -> list[ResolvedTask]:
    """Resolve model-selected scopes only through verified local securities."""
    current = _dedupe_entities(current_entities)
    previous = _dedupe_entities(previous_answer_entities)
    conversation = _dedupe_entities(
        [
            *(conversation_entities or []),
            *previous,
            *current,
        ]
    )
    tasks_by_id = {task.task_id: task for task in plan.tasks}
    resolved_tasks: list[ResolvedTask] = []
    for task in plan.tasks:
        explicit, unresolved = _resolve_task_entities(task.entities)
        spec = workflow_for(task.kind)
        if unresolved and spec.requires_entities:
            raise TaskPlanValidationError(f"{task.task_id} contains unresolved securities: {unresolved}")

        if task.entity_scope == EntityScope.CURRENT_MESSAGE:
            selected = explicit or current
        elif task.entity_scope == EntityScope.PREVIOUS_ANSWER:
            if current:
                allowed = {item["symbol"] for item in previous}
                selected = [item for item in current if item["symbol"] in allowed]
                if len(selected) != len(current):
                    raise TaskPlanValidationError(f"{task.task_id} names entities outside previous-turn scope")
            else:
                selected = previous
        elif task.entity_scope == EntityScope.CONVERSATION:
            if current:
                allowed = {item["symbol"] for item in conversation}
                selected = [item for item in current if item["symbol"] in allowed]
                if len(selected) != len(current):
                    raise TaskPlanValidationError(f"{task.task_id} names entities outside conversation scope")
            else:
                selected = conversation
        else:
            selected = explicit

        symbols = tuple(item["symbol"] for item in _dedupe_entities(selected))
        dependency_outputs = frozenset(
            resource
            for dependency_id in task.depends_on
            for resource in workflow_for(tasks_by_id[dependency_id].kind).output_resources
        )
        if spec.requires_entities and not symbols and TaskResource.SECURITY_COLLECTION not in dependency_outputs:
            raise TaskPlanValidationError(f"{task.task_id} requires a locally verified A-share entity")
        resolved_tasks.append(
            ResolvedTask(
                candidate=task,
                symbols=symbols,
                entity_names=tuple((item["symbol"], item["name"]) for item in _dedupe_entities(selected)),
            )
        )
    return resolved_tasks
