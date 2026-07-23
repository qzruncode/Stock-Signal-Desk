# -*- coding: utf-8 -*-
"""Semantic task decomposition without request-phrase routing.

Natural language enters exactly one planning path.  The model proposes typed
standard tasks; program code validates their contracts, binds live semantic
resources and resolves securities.  No user wording selects a workflow in
Python and no prior answer is parsed for task-specific headings or tables.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable, Mapping

from pydantic import ValidationError

from src.agent.conversation_context import ConversationContext
from src.agent.result_contracts import (
    CollectionFinancialFilterSpec,
    DomainBoardQuerySpec,
    InvestmentThesisContext,
)
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
    parameter_requirement_issues,
    workflow_for,
)
from src.llm.anthropic_gateway import build_litellm_kwargs
from src.services.stock_screening.screen_spec import (
    QuantitativeScreenSpec,
    quantitative_screen_spec_schema,
)
from src.tools.symbols import resolve_securities_csv


logger = logging.getLogger(__name__)

PLANNER_PRIMARY_TIMEOUT_SECONDS = 180.0
PLANNER_RECOVERY_TIMEOUT_SECONDS = 120.0
RESOURCE_BINDING_TIMEOUT_SECONDS = 45.0
PLANNER_CACHE_TTL = timedelta(days=7)
PLANNER_CACHE_VERSION = "v15"


class TaskPlannerUnavailableError(RuntimeError):
    """The semantic orchestration provider was unavailable after recovery."""

    def __init__(self, message: str, *, reason: str = "provider") -> None:
        super().__init__(message)
        self.reason = reason


class TaskPlanValidationError(ValueError):
    """The semantic planner returned a structurally unsafe candidate plan."""


def _planner_unavailable_reason(exc: BaseException) -> str:
    """Classify transport failures without depending on one provider SDK."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(item, TimeoutError) for item in chain):
        return "timeout"
    diagnostic = " ".join(
        f"{type(item).__name__} {item}".lower() for item in chain
    )
    if any(token in diagnostic for token in (
        "connect", "connection", "network", "dns", "name resolution",
        "cannot reach", "unreachable", "socket", "reset by peer",
    )):
        return "connection"
    return "provider"


_TASK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "task_id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,31}$"},
        "kind": {"type": "string", "enum": [kind.value for kind in StandardTaskKind]},
        "objective": {"type": "string"},
        "entity_scope": {"type": "string", "enum": [scope.value for scope in EntityScope]},
        "entities": {"type": "array", "items": {"type": "string"}},
        "parameters": {"type": "object", "additionalProperties": True},
        "depends_on": {"type": "array", "items": {"type": "string"}},
        "confirmation": {
            "type": "string",
            "enum": [state.value for state in ConfirmationState],
        },
    },
    "required": [
        "task_id",
        "kind",
        "objective",
        "entity_scope",
        "entities",
        "parameters",
        "depends_on",
        "confirmation",
    ],
}

_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_standard_task_plan",
        "description": "Submit a semantic decomposition into standard tasks. Do not select data tools.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tasks": {"type": "array", "items": _TASK_SCHEMA, "maxItems": 12},
                "needs_clarification": {"type": "boolean"},
                "clarification_question": {"type": ["string", "null"]},
            },
            "required": ["tasks", "needs_clarification", "clarification_question"],
        },
    },
}

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
            "enum": ["exact_board", "proxy_board", "unresolved"],
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


_PLANNER_SYSTEM_PROMPT = """\
你是股票 AI 助手的 Task Decomposer。你只理解本轮用户目标并产生标准子任务，不能选择数据 Tool、
不能设计执行步骤，也不能直接回答用户。

规则：
1. current_request 是唯一的新执行目标。conversation_context 和 previous_assistant_response 只用于解析
   指代、沿用范围或修改上一轮条件；不得自动重做已经完成的旧目标。
2. 严格从 standard_task_contracts 选择 kind。组合问题拆成最小且完整的任务；只在后续任务确实需要
   前一任务输出时声明 depends_on，不创建重复或用户未要求的任务。
3. entities 只放用户在 current_request 中亲自点名的证券。当 entity_scope 为
   previous_answer 或 conversation 时，entities 必须留空；完整已验证集合由程序绑定，
   不得把上下文样本复制成缩小名单。结构化上下文是边界，不得凭记忆增加证券。
4. parameters 必须满足所选任务目录中的 required_parameters、allowed_parameters、枚举和条件要求。
   带 semantic_resources 的任务只提取用户表达的语义参数；实时资源的精确绑定由程序在规划后完成。
5. 需要实时、市场、财务、资讯或外部事实时选择对应数据任务；只有不需要这些数据的请求才使用
   general_response。股票研究与交易执行是不同任务，不得互相替代。
   只要用户的核心问题是在问某只或某组股票现在能否买、是否适合介入、是否值得买入或应否等待，
   必须选择 investment_decision；不得用 stock_deep_research、valuation_analysis 或
   technical_analysis 替代完整买入分析。用户只要求研究事实且没有要求买入判断时，才选择深度研究。
6. confirmation 只描述用户是否已明确确认具体高影响动作。无法确定时使用 missing；普通查询使用
   not_required。trade_execution 始终进入独立安全状态机。
7. 信息不足以生成完整且合法的任务参数时，设置 needs_clarification 并提出一个最小必要问题；不得
   猜测条件、补默认业务目标或丢弃用户约束。
8. 你同时完成任务类型选择、参数提取和依赖声明，只通过 submit_standard_task_plan 返回一次完整
   结构化结果；任何字段都不得包含 Tool 名。
"""

_RESOURCE_BINDING_SYSTEM_PROMPT = """\
你是 Semantic Resource Binder。标准任务已经确定，你只能把其中的语义产业领域绑定到本次提供的实时
板块目录，不能增加、删除或改写任务，也不能选择数据 Tool。

对每个领域：同名板块用 exact_board；没有同名板块时，只能选择产业功能与应用场景明确相邻的最窄
板块并标为 proxy_board；没有可靠绑定就用 unresolved 且 board_queries 为空。board_queries 必须逐字
来自 catalog，最多四个。保留原始 label，并用 rationale 说明边界；复合领域无法覆盖的部分放入
unresolved_parts。每个输入 task_id 和每个输入领域都必须且只能返回一次。
"""


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, Mapping) and part.get("type") == "text"
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


def previous_assistant_outline(
    messages: list[dict[str, Any]],
    *,
    max_chars: int = 12_000,
) -> str:
    """Return a format-agnostic sample of the immediately preceding answer."""
    previous = ""
    for message in reversed(messages[:-1]):
        if isinstance(message, Mapping) and message.get("role") == "assistant":
            previous = _message_text(message)
            if previous:
                break
    if len(previous) <= max_chars:
        return previous
    window_count = 4
    window_size = max_chars // window_count
    last_start = max(0, len(previous) - window_size)
    starts = [round(index * last_start / (window_count - 1)) for index in range(window_count)]
    return "\n...[连续回答片段]...\n".join(
        previous[start:start + window_size] for start in starts
    )[:max_chars]


def _cache_key(
    messages: list[dict[str, Any]],
    llm_cfg: Mapping[str, Any],
    current_entities: list[dict[str, str]],
    previous_answer_entities: list[dict[str, str]],
    conversation_context: ConversationContext,
) -> str | None:
    if not llm_cfg.get("api_base"):
        return None
    payload = {
        "version": PLANNER_CACHE_VERSION,
        "model": str(llm_cfg.get("model") or ""),
        "current_request": current_user_request(messages, recovery=True),
        "previous_assistant_response": (
            "" if conversation_context.turns else previous_assistant_outline(messages)
        ),
        "current_entities": current_entities,
        "previous_answer_entities": previous_answer_entities,
        "conversation_context": conversation_context.planner_payload(),
        "runtime_context": _planner_runtime_context(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"standard_task_plan:{PLANNER_CACHE_VERSION}:{digest}"


def _load_cached_plan(cache_key: str | None) -> TaskPlan | None:
    if not cache_key:
        return None
    try:
        from src.storage.manager import DatabaseManager

        cached = DatabaseManager.get_instance().get_tool_cache(cache_key)
        if not cached:
            return None
        updated_at = cached.get("updated_at")
        if not isinstance(updated_at, datetime) or datetime.now() - updated_at > PLANNER_CACHE_TTL:
            return None
        payload = json.loads(bytes(cached["payload"]).decode("utf-8"))
        plan = TaskPlan.model_validate(payload)
        validate_candidate_plan(plan, resources_bound=False)
        return plan.model_copy(update={"source": "semantic_cache"})
    except Exception:
        logger.warning("[TaskPlanner] ignored invalid persistent plan cache", exc_info=True)
        return None


def _save_cached_plan(cache_key: str | None, plan: TaskPlan) -> None:
    if not cache_key:
        return
    try:
        from src.storage.manager import DatabaseManager

        payload = plan.model_copy(update={"source": "semantic"}).model_dump_json().encode("utf-8")
        DatabaseManager.get_instance().save_tool_cache(cache_key, payload)
    except Exception:
        logger.warning("[TaskPlanner] failed to persist plan cache", exc_info=True)


def _json_object(value: str) -> dict[str, Any]:
    stripped = value.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline >= 0:
            stripped = stripped[first_newline + 1:]
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


def _plan_tool_for_kinds(kinds: list[StandardTaskKind]) -> dict[str, Any]:
    tool = copy.deepcopy(_PLAN_TOOL)
    tool["function"]["parameters"]["properties"]["tasks"]["items"][
        "properties"
    ]["kind"]["enum"] = [kind.value for kind in kinds]
    return tool


def _selected_parameter_schemas(
    kinds: list[StandardTaskKind],
) -> dict[str, Any]:
    schemas: dict[str, Any] = {}
    if StandardTaskKind.COLLECTION_FINANCIAL_FILTER in kinds:
        schemas[StandardTaskKind.COLLECTION_FINANCIAL_FILTER.value] = (
            CollectionFinancialFilterSpec.model_json_schema()
        )
    if StandardTaskKind.STOCK_SCREENING in kinds:
        schemas[StandardTaskKind.STOCK_SCREENING.value] = (
            quantitative_screen_spec_schema(nullable=False)
        )
    if StandardTaskKind.INVESTMENT_DECISION in kinds:
        schemas[StandardTaskKind.INVESTMENT_DECISION.value] = {
            "thesis_context": InvestmentThesisContext.model_json_schema(),
        }
    return schemas


def _planner_entity_context(values: list[dict[str, str]]) -> dict[str, Any]:
    """Expose scope metadata only; identities are bound by the program."""
    return {
        "count": len(values),
        "complete_scope_managed_by_program": True,
    }


def _planner_runtime_context() -> dict[str, Any]:
    today = datetime.now().astimezone().date()
    return {
        "current_date": today.isoformat(),
        "current_year": today.year,
        "previous_fiscal_year": today.year - 1,
    }


def _selected_planner_contracts(
    kinds: list[StandardTaskKind],
    parameter_schemas: Mapping[str, Any],
) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for kind in kinds:
        contract = workflow_for(kind).planner_contract()
        if kind.value not in parameter_schemas:
            contracts.append(contract)
            continue
        contracts.append({
            "kind": contract["kind"],
            "description": contract["description"],
            "requires_entities": contract["requires_entities"],
            "effect": contract["effect"],
            "enabled": contract["enabled"],
            "parameter_notes": contract["parameter_notes"],
            "conditional_requirements": contract["conditional_requirements"],
            "semantic_resources": contract["semantic_resources"],
        })
    return contracts


def _planner_generation_overrides() -> dict[str, Any]:
    """Keep orchestration calls short; they do not need extended reasoning."""
    return {
        "extra_body": {
            "thinking": {"type": "disabled"},
            "reasoning_effort": "none",
        }
    }


def _semantic_domain_labels(task: StandardTask) -> list[str]:
    values = task.parameters.get("domains")
    if not isinstance(values, list):
        return []
    labels: list[str] = []
    for value in values:
        label = value if isinstance(value, str) else (
            value.get("label") if isinstance(value, Mapping) else None
        )
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
    for task in plan.tasks:
        spec = workflow_for(task.kind)
        unknown = set(task.parameters) - spec.allowed_parameters
        missing = spec.required_parameters - set(task.parameters)
        if unknown:
            issues.append(f"{task.task_id}: unsupported parameters {sorted(unknown)}")
        if missing:
            issues.append(f"{task.task_id}: missing parameters {sorted(missing)}")
        for parameter, allowed_values in spec.parameter_enums.items():
            if parameter in task.parameters and task.parameters[parameter] not in allowed_values:
                issues.append(
                    f"{task.task_id}: {parameter} must be one of {sorted(allowed_values)}"
                )
        if "subjects" in task.parameters:
            subjects = task.parameters["subjects"]
            if (
                not isinstance(subjects, list)
                or not subjects
                or len(subjects) > 12
                or any(not isinstance(subject, str) or not subject.strip() for subject in subjects)
            ):
                issues.append(
                    f"{task.task_id}: subjects must be a non-empty list of at most 12 strings"
                )
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
                    f"{task.task_id}: invalid screen_spec: "
                    + "; ".join(error["msg"] for error in exc.errors())
                )
        elif task.kind == StandardTaskKind.INVESTMENT_DECISION:
            thesis_context = task.parameters.get("thesis_context")
            if thesis_context is not None:
                try:
                    InvestmentThesisContext.model_validate(thesis_context)
                except ValidationError as exc:
                    issues.append(
                        f"{task.task_id}: invalid thesis_context: "
                        + "; ".join(error["msg"] for error in exc.errors())
                    )

        if "concept_board_catalog" in spec.resource_bindings:
            labels = _semantic_domain_labels(task)
            if not labels:
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
                    unknown_boards = [
                        board for board in domain.board_queries
                        if board not in concept_board_names
                    ]
                    if unknown_boards:
                        issues.append(
                            f"{task.task_id}: domains[{index}] selects values absent from "
                            f"the live concept-board catalog: {unknown_boards}"
                        )
    if issues:
        raise TaskPlanValidationError("; ".join(issues))


def _unresolved_resource_plan(plan: TaskPlan, reason: str) -> TaskPlan:
    tasks: list[StandardTask] = []
    for task in plan.tasks:
        if "concept_board_catalog" not in workflow_for(task.kind).resource_bindings:
            tasks.append(task)
            continue
        domains = [
            {
                "label": label,
                "board_queries": [],
                "mapping_type": "unresolved",
                "rationale": reason[:240],
                "unresolved_parts": [label],
            }
            for label in _semantic_domain_labels(task)
        ]
        tasks.append(task.model_copy(update={
            "parameters": {**task.parameters, "domains": domains},
        }))
    return plan.model_copy(update={"tasks": tasks})


async def _bind_concept_board_catalog(
    plan: TaskPlan,
    llm_cfg: Mapping[str, Any],
    completion: Callable[..., Awaitable[Any]],
) -> tuple[TaskPlan, set[str]]:
    target_tasks = [
        task for task in plan.tasks
        if "concept_board_catalog" in workflow_for(task.kind).resource_bindings
    ]
    if not target_tasks:
        return plan, set()

    try:
        from src.services.domain_board_catalog import get_domain_board_catalog

        catalog = await asyncio.to_thread(get_domain_board_catalog)
    except Exception as exc:
        logger.warning("[TaskPlanner] concept board catalog unavailable: %s", exc)
        return _unresolved_resource_plan(plan, f"实时板块目录不可用：{type(exc).__name__}"), set()

    board_names = [
        str(value).strip() for value in catalog.get("board_names") or []
        if str(value).strip()
    ]
    board_name_set = set(board_names)
    if not board_names:
        errors = "；".join(str(value) for value in catalog.get("errors") or [])
        return _unresolved_resource_plan(plan, errors or "实时板块目录为空"), set()

    context = {
        "tasks": [
            {
                "task_id": task.task_id,
                "objective": task.objective,
                "semantic_domains": _semantic_domain_labels(task),
                "context_theme": task.parameters.get("context_theme"),
            }
            for task in target_tasks
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
    for attempt in range(2):
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
            async with asyncio.timeout(RESOURCE_BINDING_TIMEOUT_SECONDS):
                response = await completion(**kwargs)
            payload = _payload_from_response(response, "submit_semantic_resource_bindings")
            raw_bindings = payload.get("bindings")
            if not isinstance(raw_bindings, list):
                raise ValueError("bindings must be an array")
            by_task: dict[str, list[dict[str, Any]]] = {}
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
                    board
                    for domain in validated
                    for board in domain.board_queries
                    if board not in board_name_set
                ]
                if unknown:
                    raise ValueError(f"{task_id} selected catalog-absent boards {unknown}")
                expected_labels = _semantic_domain_labels(
                    next(task for task in target_tasks if task.task_id == task_id)
                )
                returned_labels = [domain.label for domain in validated]
                if returned_labels != expected_labels:
                    raise ValueError(
                        f"{task_id} must preserve domain labels and order: {expected_labels}"
                    )
                by_task[task_id] = [domain.model_dump() for domain in validated]
            expected_ids = {task.task_id for task in target_tasks}
            if set(by_task) != expected_ids:
                raise ValueError(
                    f"binding task ids must equal {sorted(expected_ids)}, got {sorted(by_task)}"
                )
            bound_tasks = [
                task.model_copy(update={
                    "parameters": {**task.parameters, "domains": by_task[task.task_id]},
                }) if task.task_id in by_task else task
                for task in plan.tasks
            ]
            return plan.model_copy(update={"tasks": bound_tasks}), board_name_set
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
    if last_error is not None and not isinstance(
        last_error, (ValidationError, ValueError, json.JSONDecodeError)
    ):
        raise TaskPlannerUnavailableError(
            "semantic resource binder unavailable after recovery",
            reason=_planner_unavailable_reason(last_error),
        ) from last_error
    raise TaskPlanValidationError(
        f"semantic resource binding failed after retry: {validation_error}"
    )


async def _plan_semantically(
    messages: list[dict[str, Any]],
    llm_cfg: Mapping[str, Any],
    completion: Callable[..., Awaitable[Any]],
    *,
    current_entities: list[dict[str, str]],
    previous_answer_entities: list[dict[str, str]],
    conversation_context: ConversationContext,
) -> TaskPlan:
    available_kinds = list(StandardTaskKind)
    parameter_schemas = _selected_parameter_schemas(available_kinds)
    task_contracts = _selected_planner_contracts(
        available_kinds, parameter_schemas,
    )
    plan_tool = _plan_tool_for_kinds(available_kinds)
    validation_error = ""
    for attempt in range(2):
        context = {
            "current_request": current_user_request(messages, recovery=attempt == 1),
            "previous_assistant_response": (
                "" if conversation_context.turns else previous_assistant_outline(
                    messages, max_chars=2_000,
                )
            ),
            "conversation_context": conversation_context.task_selection_payload(),
            "verified_entities_in_current_message": _planner_entity_context(
                current_entities
            ),
            "verified_entities_in_previous_turn": _planner_entity_context(
                previous_answer_entities
            ),
            "runtime_context": _planner_runtime_context(),
            "standard_task_contracts": task_contracts,
            "parameter_schemas": parameter_schemas,
        }
        if validation_error:
            context["previous_validation_error"] = validation_error
            context["instruction"] = (
                "修正上一候选计划的结构问题，只返回完整标准任务计划；"
                "不要改写用户目标，也不要选择或提及数据工具。"
            )
        kwargs = build_litellm_kwargs(
            dict(llm_cfg),
            stream=False,
            messages=[
                {"role": "system", "content": _PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            tools=[plan_tool],
            tool_choice={
                "type": "function",
                "function": {"name": "submit_standard_task_plan"},
            },
            temperature=0,
            max_tokens=8_000,
            **_planner_generation_overrides(),
        )
        try:
            timeout = (
                PLANNER_PRIMARY_TIMEOUT_SECONDS
                if attempt == 0
                else PLANNER_RECOVERY_TIMEOUT_SECONDS
            )
            async with asyncio.timeout(timeout):
                response = await completion(**kwargs)
            plan = TaskPlan.model_validate(
                _payload_from_response(response, "submit_standard_task_plan")
            )
            validate_candidate_plan(plan, resources_bound=False)
            return plan
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            validation_error = str(exc)
            logger.warning(
                "[TaskPlanner] plan attempt=%d failed=%s recovery=%s detail=%s",
                attempt + 1,
                type(exc).__name__,
                attempt == 0,
                validation_error[:300],
            )
            if attempt == 1:
                if isinstance(exc, (ValidationError, ValueError, json.JSONDecodeError)):
                    raise TaskPlanValidationError(
                        f"invalid standard task plan after retry: {exc}"
                    ) from exc
                raise TaskPlannerUnavailableError(
                    "semantic task planner unavailable after recovery",
                    reason=_planner_unavailable_reason(exc),
                ) from exc
    raise TaskPlanValidationError("standard task plan was not resolved")


async def resolve_task_plan(
    messages: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    *,
    completion: Callable[..., Awaitable[Any]],
    current_entities: list[dict[str, str]] | None = None,
    previous_answer_entities: list[dict[str, str]] | None = None,
    conversation_context: Mapping[str, Any] | ConversationContext | None = None,
) -> TaskPlan:
    """Plan every request through one semantic path, then bind live resources."""
    if not current_user_request(messages):
        raise ValueError("conversation has no user text")
    current = current_entities or []
    previous = previous_answer_entities or []
    context = (
        conversation_context
        if isinstance(conversation_context, ConversationContext)
        else ConversationContext.from_value(conversation_context)
    )
    cache_key = _cache_key(messages, llm_cfg, current, previous, context)
    semantic_plan = _load_cached_plan(cache_key)
    if semantic_plan is None:
        semantic_plan = await _plan_semantically(
            messages,
            llm_cfg,
            completion,
            current_entities=current,
            previous_answer_entities=previous,
            conversation_context=context,
        )
        _save_cached_plan(cache_key, semantic_plan)
    else:
        logger.info("[TaskPlanner] reused validated persistent semantic plan")

    if semantic_plan.needs_clarification:
        return semantic_plan
    bound_plan, board_names = await _bind_concept_board_catalog(
        semantic_plan,
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
    conversation = _dedupe_entities([
        *(conversation_entities or []),
        *previous,
        *current,
    ])
    resolved_tasks: list[ResolvedTask] = []
    for task in plan.tasks:
        explicit, unresolved = _resolve_task_entities(task.entities)
        spec = workflow_for(task.kind)
        if unresolved and spec.requires_entities:
            raise TaskPlanValidationError(
                f"{task.task_id} contains unresolved securities: {unresolved}"
            )

        if task.entity_scope == EntityScope.CURRENT_MESSAGE:
            selected = explicit or current
        elif task.entity_scope == EntityScope.PREVIOUS_ANSWER:
            if current:
                allowed = {item["symbol"] for item in previous}
                selected = [item for item in current if item["symbol"] in allowed]
                if len(selected) != len(current):
                    raise TaskPlanValidationError(
                        f"{task.task_id} names entities outside previous-turn scope"
                    )
            else:
                selected = previous
        elif task.entity_scope == EntityScope.CONVERSATION:
            if current:
                allowed = {item["symbol"] for item in conversation}
                selected = [item for item in current if item["symbol"] in allowed]
                if len(selected) != len(current):
                    raise TaskPlanValidationError(
                        f"{task.task_id} names entities outside conversation scope"
                    )
            else:
                selected = conversation
        else:
            selected = explicit

        symbols = tuple(item["symbol"] for item in _dedupe_entities(selected))
        if spec.requires_entities and not symbols:
            raise TaskPlanValidationError(
                f"{task.task_id} requires a locally verified A-share entity"
            )
        resolved_tasks.append(ResolvedTask(candidate=task, symbols=symbols))
    return resolved_tasks


__all__ = [
    "TaskPlanValidationError",
    "TaskPlannerUnavailableError",
    "current_user_request",
    "previous_assistant_outline",
    "resolve_plan_entities",
    "resolve_task_plan",
    "validate_candidate_plan",
]
