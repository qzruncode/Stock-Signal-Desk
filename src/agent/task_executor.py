# -*- coding: utf-8 -*-
"""Policy-validated executor for immutable standard-task workflows."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Iterable, Mapping

from src.agent.task_workflows import (
    ConfirmationState,
    EffectClass,
    ResolvedTask,
    SecurityEntity,
    TaskResource,
    WorkflowCall,
    WorkflowCompileError,
    compile_task,
    project_task_collection,
    workflow_for,
)
from src.tools.registry import ToolRegistry


logger = logging.getLogger(__name__)


class PolicyViolation(RuntimeError):
    pass


class ConfirmationRequired(PolicyViolation):
    def __init__(self, task_id: str, action: str) -> None:
        self.task_id = task_id
        self.action = action
        super().__init__(f"{task_id} requires explicit confirmation for action={action}")


class WorkflowUnavailable(PolicyViolation):
    pass


@dataclass(frozen=True)
class ValidatedCall:
    call: WorkflowCall
    arguments: dict[str, Any]


@dataclass
class CallOutcome:
    task_id: str
    step_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    executed: bool
    reused: bool

    @property
    def success(self) -> bool:
        return self.result.get("success") is not False

    def evidence(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "step_id": self.step_id,
            "tool": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
            "executed": self.executed,
            "reused": self.reused,
        }


@dataclass
class TaskExecutionResult:
    task: ResolvedTask
    status: str
    calls: list[CallOutcome] = field(default_factory=list)
    derived_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    blocked_reason: str | None = None
    output_entities: tuple[SecurityEntity, ...] = ()
    resource_outputs: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == "completed"

    @property
    def evidence(self) -> list[dict[str, Any]]:
        return [
            *(call.evidence() for call in self.calls),
            *self.derived_results,
        ]


@dataclass
class PlanExecutionResult:
    tasks: list[TaskExecutionResult]

    @property
    def evidence(self) -> list[dict[str, Any]]:
        return [packet for task in self.tasks for packet in task.evidence]

    @property
    def success(self) -> bool:
        return all(task.success for task in self.tasks)

    def _terminal_collection_results(self) -> list[TaskExecutionResult]:
        collection_results = {
            result.task.task_id: result
            for result in self.tasks
            if TaskResource.SECURITY_COLLECTION in workflow_for(result.task.kind).output_resources and result.success
        }
        consumed_dependencies = {
            dependency_id
            for result in self.tasks
            if TaskResource.SECURITY_COLLECTION in workflow_for(result.task.kind).input_resources
            for dependency_id in result.task.candidate.depends_on
        }
        return [result for task_id, result in collection_results.items() if task_id not in consumed_dependencies]

    @property
    def has_final_collection(self) -> bool:
        return bool(self._terminal_collection_results())

    @property
    def final_entities(self) -> tuple[SecurityEntity, ...]:
        """Return collection outputs from terminal resource-producing tasks."""
        values: list[SecurityEntity] = []
        seen: set[str] = set()
        for result in self._terminal_collection_results():
            for entity in result.output_entities:
                if entity.symbol in seen:
                    continue
                seen.add(entity.symbol)
                values.append(entity)
        return tuple(values)


CallRunner = Callable[[WorkflowCall, dict[str, Any]], Awaitable[dict[str, Any]]]
ResultProcessorRunner = Callable[
    [str, ResolvedTask, list[dict[str, Any]]],
    Awaitable[dict[str, Any]],
]
OutcomeObserver = Callable[
    [ResolvedTask, CallOutcome, int, int],
    Awaitable[None],
]


def action_fingerprint(task: ResolvedTask) -> str:
    """Stable identity for a reviewed task, excluding model-generated prose."""
    payload = json.dumps(
        {
            "kind": task.kind.value,
            "parameters": task.parameters,
            "result_selection": (
                task.result_selection.model_dump(mode="json") if task.result_selection is not None else None
            ),
            "symbols": list(task.symbols),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class WorkflowPolicyValidator:
    """Program rules that every compiled call must pass before execution."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        approved_actions: Iterable[str] = (),
        execution_policies: Mapping[str, Any] | None = None,
        capability_authorizations: Mapping[str, bool] | None = None,
    ) -> None:
        self.registry = registry
        self.approved_actions = frozenset(approved_actions)
        self.execution_policies = dict(execution_policies or {})
        self.capability_authorizations = dict(
            capability_authorizations or {}
        )

    def preflight_task(self, task: ResolvedTask) -> list[ValidatedCall]:
        spec = workflow_for(task.kind)
        if self.capability_authorizations.get(task.task_id) is False:
            raise PolicyViolation(
                f"{task.kind.value} is not authorized for the current owner"
            )
        if not spec.enabled:
            machine = " → ".join(spec.state_machine) if spec.state_machine else "未配置"
            raise WorkflowUnavailable(f"{spec.title}当前不可执行；固定状态机：{machine}")
        if spec.max_input_entities is not None and len(task.symbols) > spec.max_input_entities:
            raise PolicyViolation(
                f"{task.task_id} received {len(task.symbols)} entities, exceeding "
                f"its declared input capacity {spec.max_input_entities}"
            )

        action = str(task.parameters.get("action") or "")
        fingerprint = action_fingerprint(task)
        if spec.requires_confirmation(task.parameters) and (
            task.candidate.confirmation != ConfirmationState.EXPLICIT or fingerprint not in self.approved_actions
        ):
            raise ConfirmationRequired(
                task.task_id,
                action or task.kind.value,
            )
        if (
            task.kind.value == "batch_analysis"
            and len(task.symbols) > 10
            and (task.candidate.confirmation != ConfirmationState.EXPLICIT or fingerprint not in self.approved_actions)
        ):
            raise ConfirmationRequired(task.task_id, "run_more_than_10_symbols")

        calls = compile_task(task)
        policy = self.execution_policies.get(task.task_id)
        call_budget = int(policy.max_calls) if policy is not None else spec.max_tool_calls
        if policy is not None and str(policy.effect.value) != spec.effect.value:
            raise PolicyViolation(f"{task.task_id} execution policy effect does not match workflow")
        if policy is not None and policy.confirmation_required and (fingerprint not in self.approved_actions):
            raise ConfirmationRequired(task.task_id, task.kind.value)
        if len(calls) > call_budget:
            raise PolicyViolation(f"{task.task_id} exceeds workflow call budget {call_budget}")
        known_steps = {call.step_id for call in calls}
        if len(known_steps) != len(calls):
            raise PolicyViolation(f"{task.task_id} has duplicate workflow step ids")

        validated: list[ValidatedCall] = []
        for call in calls:
            if call.tool_name not in spec.tool_whitelist:
                raise PolicyViolation(f"{call.tool_name} does not belong to {task.kind.value}")
            predecessors = set(call.depends_on_steps) | set(call.after_steps)
            missing_predecessors = predecessors - known_steps
            if missing_predecessors:
                raise PolicyViolation(f"{call.step_id} has unknown predecessors {sorted(missing_predecessors)}")
            binding_sources = {source_step for _parameter, source_step in call.result_bindings}
            if not binding_sources <= set(call.depends_on_steps):
                raise PolicyViolation(f"{call.step_id} result bindings must reference successful " "depends_on steps")
            binding_parameters = [parameter for parameter, _source_step in call.result_bindings]
            if len(binding_parameters) != len(set(binding_parameters)):
                raise PolicyViolation(f"{call.step_id} has duplicate result binding parameters")
            if set(binding_parameters) & set(call.arguments):
                raise PolicyViolation(f"{call.step_id} result bindings overwrite static arguments")
            guard = call.execution_guard
            if guard is not None:
                if guard.source_step not in known_steps:
                    raise PolicyViolation(
                        f"{call.step_id} execution guard references unknown " f"step {guard.source_step}"
                    )
                if guard.source_step not in call.depends_on_steps:
                    raise PolicyViolation(
                        f"{call.step_id} execution guard must reference a " "successful depends_on step"
                    )
                if not guard.result_path or not guard.allowed_values:
                    raise PolicyViolation(f"{call.step_id} execution guard has an empty contract")
                tool = self.registry.get_tool(call.tool_name)
                if tool is None or tool.guard_blocked_result is None:
                    raise PolicyViolation(
                        f"{call.tool_name} cannot project a typed result when " "its execution guard blocks"
                    )
            arguments = self.registry.validate_arguments(call.tool_name, call.arguments)
            validated.append(ValidatedCall(call=call, arguments=arguments))
        self._assert_step_dag(validated)
        return validated

    @staticmethod
    def _assert_step_dag(calls: Iterable[ValidatedCall]) -> None:
        graph = {item.call.step_id: (set(item.call.depends_on_steps) | set(item.call.after_steps)) for item in calls}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise PolicyViolation("workflow step graph contains a cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for predecessor in graph[step_id]:
                visit(predecessor)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in graph:
            visit(step_id)


from ._task_executor_methods1 import _WorkflowExecutorMethods1
from ._task_executor_methods2 import _WorkflowExecutorMethods2
class WorkflowExecutor(_WorkflowExecutorMethods1, _WorkflowExecutorMethods2):
        """Execute task and step DAGs with exact-call reuse and policy gates."""


def _bind_mixin_member(_member):
    import functools
    import types

    if isinstance(_member, staticmethod):
        return staticmethod(_bind_mixin_member(_member.__func__))
    if isinstance(_member, classmethod):
        return classmethod(_bind_mixin_member(_member.__func__))
    if isinstance(_member, property):
        return property(
            _bind_mixin_member(_member.fget) if _member.fget else None,
            _bind_mixin_member(_member.fset) if _member.fset else None,
            _bind_mixin_member(_member.fdel) if _member.fdel else None,
            _member.__doc__,
        )
    if not isinstance(_member, types.FunctionType):
        return _member
    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _mixin in (_WorkflowExecutorMethods1, _WorkflowExecutorMethods2):
    for _name, _member in _mixin.__dict__.items():
        if _name not in {"__dict__", "__weakref__"}:
            setattr(WorkflowExecutor, _name, _bind_mixin_member(_member))


def _result_path_value(
    result: Mapping[str, Any],
    path: tuple[str, ...],
) -> Any:
    value: Any = result
    for field in path:
        if not isinstance(value, Mapping) or field not in value:
            raise PolicyViolation("execution guard result path is missing: " + "/".join(path))
        value = value[field]
    return value


def _resource_from_dependencies(
    dependency_ids: Iterable[str],
    results: Mapping[str, TaskExecutionResult],
    resource: TaskResource,
) -> Any:
    values: list[Any] = []
    resource_found = False
    for dependency_id in dependency_ids:
        dependency = results.get(dependency_id)
        if dependency is None or not dependency.success:
            continue
        if resource.value not in dependency.resource_outputs:
            continue
        resource_found = True
        value = dependency.resource_outputs[resource.value]
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    if not resource_found:
        return None
    unique: list[Any] = []
    seen: set[str] = set()
    for value in values:
        identity = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(value)
    return unique


def _security_entities_from_resource(value: list[Any]) -> tuple[SecurityEntity, ...]:
    entities: list[SecurityEntity] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip()
        name = str(item.get("name") or symbol).strip()
        if len(symbol) != 6 or not symbol.isdigit() or symbol in seen:
            continue
        seen.add(symbol)
        entities.append(SecurityEntity(symbol=symbol, name=name))
    return tuple(entities)


__all__ = [
    "CallOutcome",
    "ConfirmationRequired",
    "PlanExecutionResult",
    "PolicyViolation",
    "TaskExecutionResult",
    "ResultProcessorRunner",
    "ValidatedCall",
    "WorkflowExecutor",
    "WorkflowPolicyValidator",
    "WorkflowUnavailable",
    "action_fingerprint",
]
