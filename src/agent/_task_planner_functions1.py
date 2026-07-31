"""Function group 1 extracted from src/agent/task_planner.py."""

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

__all__ = ['_message_text', 'current_user_request', '_json_object', '_payload_from_response', '_planner_generation_overrides', '_semantic_domain_labels', 'validate_candidate_plan', '_exact_catalog_resource_bindings', '_merge_resource_bindings', '_has_complete_catalog_bindings']

def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "") for part in content if isinstance(part, Mapping) and part.get("type") == "text"
        ).strip()
    return ""

def current_user_request(
    messages: list[dict[str, Any]],
    *,
    recovery: bool = False,
) -> str:
    """Return only the latest user goal; historical goals never become input goals."""
    text = ""
    for message in reversed(messages):
        if isinstance(message, Mapping) and message.get("role") == "user":
            text = _message_text(message)
            if text:
                break
    if not text:
        return ""
    limit = 6_000 if recovery else 12_000
    if len(text) <= limit:
        return text
    return text[: limit - 1_100] + "\n...[本轮请求中段省略]...\n" + text[-1_000:]

def _json_object(value: str) -> dict[str, Any]:
    stripped = value.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline >= 0:
            stripped = stripped[first_newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("model response is not an object")
    return parsed

def _payload_from_response(response: Any, function_name: str) -> dict[str, Any]:
    def field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)

    for choice in field(response, "choices") or []:
        message = field(choice, "message")
        if message is None:
            continue
        for tool_call in field(message, "tool_calls") or []:
            function = field(tool_call, "function")
            if field(function, "name") != function_name:
                continue
            arguments = field(function, "arguments")
            if isinstance(arguments, dict):
                return arguments
            if arguments:
                return _json_object(str(arguments))
        content = field(message, "content")
        if isinstance(content, str) and content.strip():
            return _json_object(content)
    raise ValueError(f"model returned no {function_name} payload")

def _planner_generation_overrides() -> dict[str, Any]:
    """Leave provider-visible reasoning enabled for the live analysis stream."""
    return {}

def _semantic_domain_labels(task: StandardTask) -> list[str]:
    values = task.parameters.get("domains")
    if not isinstance(values, list):
        return []
    labels: list[str] = []
    for value in values:
        label = value if isinstance(value, str) else (value.get("label") if isinstance(value, Mapping) else None)
        text = str(label or "").strip()
        if text and text not in labels:
            labels.append(text)
    return labels

def validate_candidate_plan(
    plan: TaskPlan,
    *,
    concept_board_names: set[str] | None = None,
    resources_bound: bool = True,
) -> None:
    """Validate task contracts without interpreting the user's wording."""
    issues: list[str] = []
    tasks_by_id = {task.task_id: task for task in plan.tasks}
    for task in plan.tasks:
        spec = workflow_for(task.kind)
        dependency_outputs = frozenset(
            resource
            for dependency_id in task.depends_on
            for resource in workflow_for(tasks_by_id[dependency_id].kind).output_resources
        )
        has_declared_entity_source = bool(task.entities) or (task.entity_scope != EntityScope.NONE)
        if spec.supports_result_selection and task.result_selection is None:
            issues.append(f"{task.task_id}: requires a typed result_selection")
        if not spec.supports_result_selection and task.result_selection is not None:
            issues.append(f"{task.task_id}: result_selection is not supported")
        if (
            spec.requires_entities
            and not has_declared_entity_source
            and TaskResource.SECURITY_COLLECTION not in dependency_outputs
        ):
            issues.append(
                f"{task.task_id}: requires an entity scope or a dependency that " "produces security_collection"
            )
        conditional_entity_required = any(
            requirement.requires_entities and requirement.applies(task.parameters)
            for requirement in spec.parameter_requirements
        )
        if (
            conditional_entity_required
            and not has_declared_entity_source
            and TaskResource.SECURITY_COLLECTION not in dependency_outputs
        ):
            issues.append(
                f"{task.task_id}: selected operation requires an entity scope or "
                "a dependency that produces security_collection"
            )
        if (
            not resources_bound
            and task.kind == StandardTaskKind.THEME_BUSINESS_EVIDENCE
            and task.parameters.get("candidate_scope") == "public_fallback"
        ):
            issues.append(
                f"{task.task_id}: public_fallback is program-owned and cannot be " "selected by the semantic planner"
            )
        if "subjects" in task.parameters:
            subjects = task.parameters["subjects"]
            if (
                not isinstance(subjects, list)
                or not subjects
                or len(subjects) > 12
                or any(not isinstance(subject, str) or not subject.strip() for subject in subjects)
            ):
                issues.append(f"{task.task_id}: subjects must be a non-empty list of at most 12 strings")
        action = str(task.parameters.get("action") or "")
        if action in spec.confirmation_actions and task.confirmation == ConfirmationState.NOT_REQUIRED:
            issues.append(f"{task.task_id}: high-impact action must declare confirmation state")
        for issue in parameter_requirement_issues(task, spec):
            issues.append(f"{task.task_id}: {issue}")

        if task.kind == StandardTaskKind.COLLECTION_FINANCIAL_FILTER:
            try:
                CollectionFinancialFilterSpec.model_validate(task.parameters)
            except ValidationError as exc:
                issues.append(
                    f"{task.task_id}: invalid collection financial filter: "
                    + "; ".join(error["msg"] for error in exc.errors())
                )
        elif task.kind == StandardTaskKind.STOCK_SCREENING:
            try:
                QuantitativeScreenSpec.model_validate(task.parameters.get("screen_spec"))
            except ValidationError as exc:
                issues.append(
                    f"{task.task_id}: invalid screen_spec: " + "; ".join(error["msg"] for error in exc.errors())
                )
        elif task.kind == StandardTaskKind.INVESTMENT_DECISION:
            from src.services.buy_criteria.mainline_policy import (
                normalize_mainline_strategy,
            )

            try:
                normalize_mainline_strategy(task.parameters.get("mainline_strategy"))
            except (TypeError, ValueError) as exc:
                issues.append(f"{task.task_id}: invalid mainline_strategy: {exc}")
            thesis_context = task.parameters.get("thesis_context")
            if thesis_context is not None:
                try:
                    InvestmentThesisContext.model_validate(thesis_context)
                except ValidationError as exc:
                    issues.append(
                        f"{task.task_id}: invalid thesis_context: " + "; ".join(error["msg"] for error in exc.errors())
                    )
        elif task.kind == StandardTaskKind.THEME_BUSINESS_EVIDENCE:
            evidence_context = task.parameters.get("evidence_context")
            try:
                ThemeEvidenceContext.model_validate(evidence_context)
            except ValidationError as exc:
                issues.append(
                    f"{task.task_id}: invalid evidence_context: " + "; ".join(error["msg"] for error in exc.errors())
                )

        if "concept_board_catalog" in spec.resource_bindings:
            labels = _semantic_domain_labels(task)
            if not labels:
                domains_from_dependency = spec.input_resource_parameters.get("domains") in dependency_outputs
                if (
                    task.kind
                    in {
                        StandardTaskKind.INDUSTRY_RESEARCH,
                        StandardTaskKind.THEME_STOCK_DISCOVERY,
                    }
                    and not domains_from_dependency
                ):
                    issues.append(f"{task.task_id}: domains must contain semantic labels")
                continue
            if not resources_bound:
                continue
            values = task.parameters.get("domains") or []
            for index, value in enumerate(values):
                try:
                    domain = DomainBoardQuerySpec.model_validate(value)
                except ValidationError as exc:
                    issues.append(
                        f"{task.task_id}: invalid domains[{index}]: "
                        + "; ".join(error["msg"] for error in exc.errors())
                    )
                    continue
                if concept_board_names is not None:
                    unknown_boards = [board for board in domain.board_queries if board not in concept_board_names]
                    if unknown_boards:
                        issues.append(
                            f"{task.task_id}: domains[{index}] selects values absent from "
                            f"the live concept-board catalog: {unknown_boards}"
                        )
    if issues:
        raise TaskPlanValidationError("; ".join(issues))

def _exact_catalog_resource_bindings(
    target_tasks: Iterable[StandardTask],
    board_names: Iterable[str],
    trusted_domain_inputs: (
        Mapping[
            str,
            list[dict[str, Any]],
        ]
        | None
    ) = None,
) -> tuple[
    dict[str, dict[str, dict[str, Any]]],
    dict[str, list[str]],
]:
    """Bind literal catalog identities and leave semantic equivalence to the model."""
    names_by_identity = {name.strip().casefold(): name.strip() for name in board_names if name.strip()}
    exact: dict[str, dict[str, dict[str, Any]]] = {}
    unresolved: dict[str, list[str]] = {}
    for task in target_tasks:
        exact[task.task_id] = {}
        unresolved[task.task_id] = []
        trusted_by_label: dict[str, dict[str, Any]] = {}
        for value in task.parameters.get("domains") or []:
            try:
                domain = DomainBoardQuerySpec.model_validate(value)
            except ValidationError:
                continue
            if (
                domain.mapping_type == "catalog_binding"
                and domain.board_queries
                and set(domain.board_queries) <= set(names_by_identity.values())
            ):
                trusted_by_label[domain.label] = domain.model_dump()
        for value in (trusted_domain_inputs or {}).get(task.task_id, []):
            try:
                domain = DomainBoardQuerySpec.model_validate(value)
            except ValidationError:
                continue
            if domain.mapping_type == "catalog_binding" and set(domain.board_queries) <= set(
                names_by_identity.values()
            ):
                trusted_by_label[domain.label] = domain.model_dump()
        for label in _semantic_domain_labels(task):
            board_name = names_by_identity.get(label.strip().casefold())
            if board_name is not None:
                exact[task.task_id][label] = {
                    "label": label,
                    "board_queries": [board_name],
                    "mapping_type": "catalog_binding",
                    "rationale": "用户领域名称与实时目录板块名称完全一致。",
                    "unresolved_parts": [],
                }
                continue
            trusted = trusted_by_label.get(label)
            if trusted is not None:
                exact[task.task_id][label] = trusted
                continue
            unresolved[task.task_id].append(label)
    return exact, unresolved

def _merge_resource_bindings(
    plan: TaskPlan,
    *,
    exact: Mapping[str, Mapping[str, dict[str, Any]]],
    semantic: Mapping[str, Mapping[str, dict[str, Any]]],
    unresolved_reason: str,
) -> TaskPlan:
    tasks: list[StandardTask] = []
    for task in plan.tasks:
        if task.task_id not in exact:
            tasks.append(task)
            continue
        domains: list[dict[str, Any]] = []
        for label in _semantic_domain_labels(task):
            resolved = exact[task.task_id].get(label) or semantic.get(task.task_id, {}).get(label)
            domains.append(
                resolved
                or {
                    "label": label,
                    "board_queries": [],
                    "mapping_type": "unresolved",
                    "rationale": unresolved_reason[:240],
                    "unresolved_parts": [label],
                }
            )
        tasks.append(
            task.model_copy(
                update={
                    "execution_parameters": {**task.parameters, "domains": domains},
                }
            )
        )
    return plan.model_copy(update={"tasks": tasks})

def _has_complete_catalog_bindings(task: StandardTask) -> bool:
    values = task.parameters.get("domains")
    if not isinstance(values, list) or not values:
        return False
    try:
        domains = [DomainBoardQuerySpec.model_validate(value) for value in values]
    except ValidationError:
        return False
    return all(domain.mapping_type == "catalog_binding" and bool(domain.board_queries) for domain in domains)
