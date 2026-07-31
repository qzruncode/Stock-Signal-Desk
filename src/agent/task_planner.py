# -*- coding: utf-8 -*-
"""Program-owned resource binding and verified security resolution.

Natural-language planning lives only in ``orchestrator_v2.planner``.  This
module contains no task-planning schema, retry loop or plan cache.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Iterable, Mapping

from pydantic import ValidationError

from src.agent.result_contracts import (
    CollectionFinancialFilterSpec,
    DomainBoardQuerySpec,
    InvestmentThesisContext,
    ThemeEvidenceContext,
)
from src.agent.task_workflows import (
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
)
from src.llm.anthropic_gateway import build_litellm_kwargs
from src.services.stock_screening.screen_spec import QuantitativeScreenSpec
from src.tools.symbols import resolve_securities_csv


logger = logging.getLogger(__name__)


class SemanticResourceBindingUnavailableError(RuntimeError):
    """The live catalog or its semantic binding could not be completed."""


class TaskPlanValidationError(ValueError):
    """A frozen typed graph cannot be bound to executable resources."""


_DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "label": {"type": "string", "minLength": 1, "maxLength": 64},
        "board_queries": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "mapping_type": {
            "type": "string",
            "enum": ["catalog_binding", "unresolved"],
        },
        "rationale": {"type": "string", "maxLength": 240},
        "unresolved_parts": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
        },
    },
    "required": [
        "label",
        "board_queries",
        "mapping_type",
        "rationale",
        "unresolved_parts",
    ],
}

_RESOURCE_BINDING_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_semantic_resource_bindings",
        "description": "Bind semantic task parameters to values from a supplied live resource catalog.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "bindings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "task_id": {"type": "string"},
                            "domains": {
                                "type": "array",
                                "items": _DOMAIN_SCHEMA,
                                "minItems": 1,
                                "maxItems": 12,
                            },
                        },
                        "required": ["task_id", "domains"],
                    },
                },
            },
            "required": ["bindings"],
        },
    },
}


_RESOURCE_BINDING_SYSTEM_PROMPT = """\
你是 Semantic Resource Binder。标准任务已经确定，你只能把其中的语义产业领域绑定到本次提供的实时
板块目录，不能增加、删除或改写任务，也不能选择数据 Tool。

用户写的领域名可能口语化、不完整或并非正式板块名。你需要理解其产业语义，只能从 catalog 逐字选择
项目中真实存在、最能承接该查询的板块名称，并标为 catalog_binding。允许选择严格同义板块、对应的
标准产品板块，或能够形成合理候选池的最近上位板块；按匹配度排序，最多四个。不得生成 catalog 之外
的别名，不得选择仅因文字相似但产业含义无关的板块，也不得把板块成员关系说成主营、订单或收入证明。
只有在目录中没有任何合理可执行的相关板块时才用 unresolved，且 board_queries 为空。保留原始 label，
在 rationale 中明确是精确对应还是近似候选映射及其边界；复合领域未覆盖部分放入 unresolved_parts。
每个输入 task_id 和每个输入领域都必须且只能返回一次。
"""


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


__all__ = [
    "SemanticResourceBindingUnavailableError",
    "TaskPlanValidationError",
    "current_user_request",
    "bind_task_plan_resources",
    "resolve_plan_entities",
    "validate_candidate_plan",
]
