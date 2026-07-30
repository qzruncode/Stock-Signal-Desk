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
            if TaskResource.SECURITY_COLLECTION
            in workflow_for(result.task.kind).output_resources
            and result.success
        }
        consumed_dependencies = {
            dependency_id
            for result in self.tasks
            if TaskResource.SECURITY_COLLECTION
            in workflow_for(result.task.kind).input_resources
            for dependency_id in result.task.candidate.depends_on
        }
        return [
            result
            for task_id, result in collection_results.items()
            if task_id not in consumed_dependencies
        ]

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
                task.result_selection.model_dump(mode="json")
                if task.result_selection is not None
                else None
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
    ) -> None:
        self.registry = registry
        self.approved_actions = frozenset(approved_actions)
        self.execution_policies = dict(execution_policies or {})

    def preflight_task(self, task: ResolvedTask) -> list[ValidatedCall]:
        spec = workflow_for(task.kind)
        if not spec.enabled:
            machine = " → ".join(spec.state_machine) if spec.state_machine else "未配置"
            raise WorkflowUnavailable(f"{spec.title}当前不可执行；固定状态机：{machine}")
        if (
            spec.max_input_entities is not None
            and len(task.symbols) > spec.max_input_entities
        ):
            raise PolicyViolation(
                f"{task.task_id} received {len(task.symbols)} entities, exceeding "
                f"its declared input capacity {spec.max_input_entities}"
            )

        action = str(task.parameters.get("action") or "")
        fingerprint = action_fingerprint(task)
        if spec.requires_confirmation(task.parameters) and (
            task.candidate.confirmation != ConfirmationState.EXPLICIT
            or fingerprint not in self.approved_actions
        ):
            raise ConfirmationRequired(
                task.task_id,
                action or task.kind.value,
            )
        if (
            task.kind.value == "batch_analysis"
            and len(task.symbols) > 10
            and (
                task.candidate.confirmation != ConfirmationState.EXPLICIT
                or fingerprint not in self.approved_actions
            )
        ):
            raise ConfirmationRequired(task.task_id, "run_more_than_10_symbols")

        calls = compile_task(task)
        policy = self.execution_policies.get(task.task_id)
        call_budget = (
            int(policy.max_calls)
            if policy is not None
            else spec.max_tool_calls
        )
        if policy is not None and str(policy.effect.value) != spec.effect.value:
            raise PolicyViolation(
                f"{task.task_id} execution policy effect does not match workflow"
            )
        if policy is not None and policy.confirmation_required and (
            fingerprint not in self.approved_actions
        ):
            raise ConfirmationRequired(task.task_id, task.kind.value)
        if len(calls) > call_budget:
            raise PolicyViolation(
                f"{task.task_id} exceeds workflow call budget {call_budget}"
            )
        known_steps = {call.step_id for call in calls}
        if len(known_steps) != len(calls):
            raise PolicyViolation(f"{task.task_id} has duplicate workflow step ids")

        validated: list[ValidatedCall] = []
        for call in calls:
            if call.tool_name not in spec.tool_whitelist:
                raise PolicyViolation(
                    f"{call.tool_name} does not belong to {task.kind.value}"
                )
            predecessors = set(call.depends_on_steps) | set(call.after_steps)
            missing_predecessors = predecessors - known_steps
            if missing_predecessors:
                raise PolicyViolation(
                    f"{call.step_id} has unknown predecessors {sorted(missing_predecessors)}"
                )
            binding_sources = {
                source_step
                for _parameter, source_step in call.result_bindings
            }
            if not binding_sources <= set(call.depends_on_steps):
                raise PolicyViolation(
                    f"{call.step_id} result bindings must reference successful "
                    "depends_on steps"
                )
            binding_parameters = [
                parameter for parameter, _source_step in call.result_bindings
            ]
            if len(binding_parameters) != len(set(binding_parameters)):
                raise PolicyViolation(
                    f"{call.step_id} has duplicate result binding parameters"
                )
            if set(binding_parameters) & set(call.arguments):
                raise PolicyViolation(
                    f"{call.step_id} result bindings overwrite static arguments"
                )
            guard = call.execution_guard
            if guard is not None:
                if guard.source_step not in known_steps:
                    raise PolicyViolation(
                        f"{call.step_id} execution guard references unknown "
                        f"step {guard.source_step}"
                    )
                if guard.source_step not in call.depends_on_steps:
                    raise PolicyViolation(
                        f"{call.step_id} execution guard must reference a "
                        "successful depends_on step"
                    )
                if not guard.result_path or not guard.allowed_values:
                    raise PolicyViolation(
                        f"{call.step_id} execution guard has an empty contract"
                    )
                tool = self.registry.get_tool(call.tool_name)
                if tool is None or tool.guard_blocked_result is None:
                    raise PolicyViolation(
                        f"{call.tool_name} cannot project a typed result when "
                        "its execution guard blocks"
                    )
            arguments = self.registry.validate_arguments(call.tool_name, call.arguments)
            validated.append(ValidatedCall(call=call, arguments=arguments))
        self._assert_step_dag(validated)
        return validated

    @staticmethod
    def _assert_step_dag(calls: Iterable[ValidatedCall]) -> None:
        graph = {
            item.call.step_id: (
                set(item.call.depends_on_steps) | set(item.call.after_steps)
            )
            for item in calls
        }
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


class WorkflowExecutor:
    """Execute task and step DAGs with exact-call reuse and policy gates."""

    def __init__(
        self,
        registry: ToolRegistry,
        runner: CallRunner,
        *,
        max_plan_tool_calls: int = 6200,
        approved_actions: Iterable[str] = (),
        processor_runner: ResultProcessorRunner | None = None,
        outcome_observer: OutcomeObserver | None = None,
        execution_policies: Mapping[str, Any] | None = None,
    ) -> None:
        self.registry = registry
        self.runner = runner
        self.validator = WorkflowPolicyValidator(
            registry,
            approved_actions=approved_actions,
            execution_policies=execution_policies,
        )
        self.max_plan_tool_calls = max_plan_tool_calls
        self.processor_runner = processor_runner
        self.outcome_observer = outcome_observer
        self.execution_policies = dict(execution_policies or {})
        self._cache_lock = asyncio.Lock()
        self._call_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._execution_count = 0

    async def execute(self, tasks: list[ResolvedTask]) -> PlanExecutionResult:
        """Run independent read tasks concurrently and dependencies in order."""
        task_by_id = {task.task_id: task for task in tasks}
        pending = set(task_by_id)
        results: dict[str, TaskExecutionResult] = {}

        while pending:
            blocked = [
                task_id
                for task_id in pending
                if any(
                    dependency in results and not results[dependency].success
                    for dependency in task_by_id[task_id].candidate.depends_on
                )
            ]
            for task_id in blocked:
                pending.remove(task_id)
                results[task_id] = TaskExecutionResult(
                    task=task_by_id[task_id],
                    status="skipped",
                    errors=["前置标准任务未成功，程序已阻止后续执行。"],
                )

            ready = [
                self._bind_dependency_outputs(task_by_id[task_id], results)
                for task_id in pending
                if set(task_by_id[task_id].candidate.depends_on) <= set(results)
            ]
            if not ready:
                if pending:
                    raise PolicyViolation("task dependency graph made no progress")
                break

            reads = [task for task in ready if workflow_for(task.kind).effect == EffectClass.READ]
            effects = [task for task in ready if workflow_for(task.kind).effect != EffectClass.READ]

            if reads:
                read_results = await asyncio.gather(
                    *(self._execute_task(task) for task in reads)
                )
                for result in read_results:
                    results[result.task.task_id] = result
                    pending.remove(result.task.task_id)

            # Side effects are intentionally serialized even when their task
            # nodes are independent.  This keeps confirmation and mutation
            # boundaries visible and avoids racing stateful operations.
            for task in effects:
                result = await self._execute_task(task)
                results[result.task.task_id] = result
                pending.remove(result.task.task_id)

        ordered = [results[task.task_id] for task in tasks]
        return PlanExecutionResult(tasks=ordered)

    def _bind_dependency_outputs(
        self,
        task: ResolvedTask,
        results: Mapping[str, TaskExecutionResult],
    ) -> ResolvedTask:
        spec = workflow_for(task.kind)
        bound = task
        parameters = dict(task.parameters)
        parameters_changed = False
        for parameter, resource in spec.input_resource_parameters.items():
            value = _resource_from_dependencies(
                task.candidate.depends_on,
                results,
                resource,
            )
            if value is None:
                continue
            # An explicit typed resource edge is authoritative. Semantic
            # parameters and incidental current-message entities describe the
            # request, but must never replace or narrow the producer's frozen
            # collection.
            parameters[parameter] = value
            parameters_changed = True
        if parameters_changed:
            bound = replace(
                bound,
                candidate=bound.candidate.model_copy(
                    update={"execution_parameters": parameters}
                ),
            )

        if TaskResource.SECURITY_COLLECTION not in spec.input_resources:
            return bound
        security_resource = _resource_from_dependencies(
            bound.candidate.depends_on,
            results,
            TaskResource.SECURITY_COLLECTION,
        )
        if security_resource is None:
            return bound
        entities = _security_entities_from_resource(security_resource)
        return replace(
            bound,
            symbols=tuple(entity.symbol for entity in entities),
            entity_names=tuple((entity.symbol, entity.name) for entity in entities),
        )

    async def _execute_task(self, task: ResolvedTask) -> TaskExecutionResult:
        try:
            validated = self.validator.preflight_task(task)
        except ConfirmationRequired as exc:
            return TaskExecutionResult(
                task=task,
                status="blocked",
                errors=[str(exc)],
                blocked_reason="confirmation_required",
            )
        except (PolicyViolation, WorkflowCompileError, ValueError) as exc:
            return TaskExecutionResult(
                task=task,
                status="blocked",
                errors=[str(exc)],
                blocked_reason="policy_violation",
            )
        spec = workflow_for(task.kind)
        if not validated and not spec.result_processor:
            return TaskExecutionResult(
                task=task,
                status="completed",
                output_entities=project_task_collection(task, ()),
            )

        pending = {item.call.step_id: item for item in validated}
        completed: dict[str, CallOutcome] = {}
        outcomes: list[CallOutcome] = []
        errors: list[str] = []
        dependency_blocked_steps = 0
        while pending:
            skipped = [
                step_id
                for step_id, item in pending.items()
                if any(
                    predecessor in completed and not completed[predecessor].success
                    for predecessor in item.call.depends_on_steps
                )
            ]
            for step_id in skipped:
                item = pending.pop(step_id)
                outcome = CallOutcome(
                    task_id=task.task_id,
                    step_id=step_id,
                    tool_name=item.call.tool_name,
                    arguments=item.arguments,
                    result={
                        "success": False,
                        "errors": ["前置步骤失败，程序已阻止调用。"],
                        "partial": False,
                    },
                    executed=False,
                    reused=False,
                )
                completed[step_id] = outcome
                dependency_blocked_steps += 1

            ready = [
                item
                for item in pending.values()
                if (
                    set(item.call.depends_on_steps) | set(item.call.after_steps)
                ) <= set(completed)
            ]
            if not ready:
                if pending:
                    errors.append("固定 Workflow 的步骤依赖无法继续。")
                break
            execution_policy = self.execution_policies.get(task.task_id)
            step_limit = (
                int(execution_policy.max_parallelism)
                if execution_policy is not None
                else spec.max_parallel_steps
            )
            semaphore = asyncio.Semaphore(step_limit)

            async def execute_ready(item: ValidatedCall) -> CallOutcome:
                async with semaphore:
                    try:
                        runtime_arguments = dict(item.arguments)
                        for parameter, source_step in item.call.result_bindings:
                            source = completed.get(source_step)
                            if source is None or not source.success:
                                raise PolicyViolation(
                                    f"{item.call.step_id} cannot bind failed "
                                    f"step {source_step}"
                                )
                            runtime_arguments[parameter] = (
                                self.registry.project_bound_argument(
                                    item.call.tool_name,
                                    parameter,
                                    source.result,
                                )
                            )
                        runtime_item = ValidatedCall(
                            call=item.call,
                            arguments=self.registry.validate_arguments(
                                item.call.tool_name,
                                runtime_arguments,
                            ),
                        )
                    except Exception as exc:
                        return CallOutcome(
                            task_id=task.task_id,
                            step_id=item.call.step_id,
                            tool_name=item.call.tool_name,
                            arguments=dict(item.arguments),
                            result={
                                "success": False,
                                "errors": [
                                    "前置步骤结果绑定失败："
                                    f"{type(exc).__name__}: {exc}"
                                ],
                                "partial": False,
                            },
                            executed=False,
                            reused=False,
                        )
                    guard = item.call.execution_guard
                    if guard is not None:
                        try:
                            source = completed.get(guard.source_step)
                            if source is None or not source.success:
                                raise PolicyViolation(
                                    f"{item.call.step_id} cannot evaluate guard "
                                    f"from failed step {guard.source_step}"
                                )
                            guard_value = _result_path_value(
                                source.result,
                                guard.result_path,
                            )
                            guard_allows_execution = any(
                                guard_value == allowed
                                for allowed in guard.allowed_values
                            )
                            if not guard_allows_execution:
                                projected_result = await asyncio.to_thread(
                                    self.registry.project_guard_blocked_result,
                                    item.call.tool_name,
                                    runtime_item.arguments,
                                )
                                return CallOutcome(
                                    task_id=task.task_id,
                                    step_id=item.call.step_id,
                                    tool_name=item.call.tool_name,
                                    arguments=runtime_item.arguments,
                                    result=projected_result,
                                    executed=False,
                                    reused=False,
                                )
                        except Exception as exc:
                            return CallOutcome(
                                task_id=task.task_id,
                                step_id=item.call.step_id,
                                tool_name=item.call.tool_name,
                                arguments=runtime_item.arguments,
                                result={
                                    "success": False,
                                    "errors": [
                                        "执行守卫未能形成确定性终态："
                                        f"{type(exc).__name__}: {exc}"
                                    ],
                                    "partial": False,
                                },
                                executed=False,
                                reused=False,
                            )
                    return await self._execute_or_reuse(task, runtime_item)

            active = [
                asyncio.create_task(execute_ready(item))
                for item in ready
            ]
            try:
                for future in asyncio.as_completed(active):
                    outcome = await future
                    pending.pop(outcome.step_id, None)
                    completed[outcome.step_id] = outcome
                    outcomes.append(outcome)
                    if self.outcome_observer is not None:
                        try:
                            await self.outcome_observer(
                                task,
                                outcome,
                                len(completed),
                                len(validated),
                            )
                        except Exception as exc:
                            logger.warning(
                                "Workflow outcome observer failed for task=%s step=%s: %s",
                                task.task_id,
                                outcome.step_id,
                                exc,
                            )
            finally:
                unfinished = [future for future in active if not future.done()]
                for future in unfinished:
                    future.cancel()
                if unfinished:
                    await asyncio.gather(*unfinished, return_exceptions=True)

        if dependency_blocked_steps:
            errors.append(
                f"{dependency_blocked_steps} 个后续步骤因前置步骤失败未执行。"
            )

        outcome_order = {
            item.call.step_id: index
            for index, item in enumerate(validated)
        }
        outcomes.sort(key=lambda item: outcome_order.get(item.step_id, len(outcome_order)))

        successful_outcomes = [item for item in outcomes if item.success]
        failed_outcomes = [item for item in outcomes if not item.success]
        partial_sources_accepted = (
            spec.allow_partial_tool_failures
            and bool(successful_outcomes)
            and bool(failed_outcomes)
        )
        status = (
            "completed"
            if (
                not errors
                and (
                    not failed_outcomes
                    or partial_sources_accepted
                )
            )
            else "failed"
        )
        derived_results: list[dict[str, Any]] = []
        resource_outputs: dict[str, Any] = {}
        if status == "completed" and spec.result_processor:
            if self.processor_runner is None:
                status = "failed"
                errors.append(
                    f"标准任务缺少已声明的结果处理器：{spec.result_processor}"
                )
            else:
                try:
                    processor_result = await self.processor_runner(
                        spec.result_processor,
                        task,
                        [outcome.evidence() for outcome in outcomes],
                    )
                    packet = {
                        "task_id": task.task_id,
                        "step_id": f"result_{spec.result_processor}",
                        "processor": spec.result_processor,
                        "result": processor_result,
                    }
                    derived_results.append(packet)
                    if processor_result.get("success") is False:
                        status = "failed"
                        errors.extend(
                            str(value)
                            for value in processor_result.get("errors") or [
                                f"{spec.result_processor} failed"
                            ]
                        )
                    raw_resources = processor_result.get("resource_outputs")
                    if isinstance(raw_resources, Mapping):
                        resource_outputs.update({
                            str(key): value
                            for key, value in raw_resources.items()
                        })
                except Exception as exc:
                    status = "failed"
                    errors.append(
                        f"标准任务结果处理失败：{type(exc).__name__}: {exc}"
                    )

        result_context = [
            *(outcome.evidence() for outcome in outcomes),
            *derived_results,
        ]
        authoritative_security = resource_outputs.get(
            TaskResource.SECURITY_COLLECTION.value
        )
        output_entities = (
            (
                _security_entities_from_resource(authoritative_security)
                if isinstance(authoritative_security, list)
                else project_task_collection(task, result_context)
            )
            if status == "completed"
            else ()
        )
        max_output_entities = spec.max_output_entities
        if (
            status == "completed"
            and max_output_entities is not None
            and len(output_entities) > max_output_entities
        ):
            status = "failed"
            errors.append(
                f"标准任务产出 {len(output_entities)} 个证券对象，超过资源契约容量 "
                f"{max_output_entities}；未截断集合。"
            )
            output_entities = ()
        if status == "completed":
            for parameter, resource in spec.parameter_output_resources.items():
                value = task.parameters.get(parameter)
                if value not in (None, [], {}):
                    resource_outputs[resource.value] = value
            if TaskResource.SECURITY_COLLECTION in spec.output_resources:
                resource_outputs[TaskResource.SECURITY_COLLECTION.value] = [
                    {"symbol": entity.symbol, "name": entity.name}
                    for entity in output_entities
                ]
            missing_resources = [
                resource.value
                for resource in spec.output_resources
                if resource.value not in resource_outputs
            ]
            if missing_resources:
                status = "failed"
                errors.append(
                    "标准任务没有发布声明的资源："
                    + "、".join(missing_resources)
                )
                output_entities = ()
                resource_outputs = {}
        return TaskExecutionResult(
            task=task,
            status=status,
            calls=outcomes,
            derived_results=derived_results,
            errors=errors,
            output_entities=output_entities,
            resource_outputs=resource_outputs,
        )

    async def _execute_or_reuse(
        self,
        task: ResolvedTask,
        item: ValidatedCall,
    ) -> CallOutcome:
        signature = self._signature(item.call.tool_name, item.arguments)
        owner = False
        async with self._cache_lock:
            future = self._call_futures.get(signature)
            if future is None:
                if self._execution_count >= self.max_plan_tool_calls:
                    return CallOutcome(
                        task_id=task.task_id,
                        step_id=item.call.step_id,
                        tool_name=item.call.tool_name,
                        arguments=item.arguments,
                        result={
                            "success": False,
                            "errors": ["整轮标准任务调用预算已用完。"],
                            "partial": False,
                        },
                        executed=False,
                        reused=False,
                    )
                future = asyncio.get_running_loop().create_future()
                self._call_futures[signature] = future
                self._execution_count += 1
                owner = True

        if owner:
            try:
                result = await self.runner(item.call, item.arguments)
                if not isinstance(result, dict):
                    result = {
                        "success": False,
                        "errors": ["工具返回值不是对象。"],
                        "partial": False,
                    }
            except Exception as exc:
                result = {
                    "success": False,
                    "errors": [f"工具执行失败：{exc}"],
                    "partial": False,
                }
            if not future.done():
                future.set_result(result)
        result = await future
        return CallOutcome(
            task_id=task.task_id,
            step_id=item.call.step_id,
            tool_name=item.call.tool_name,
            arguments=item.arguments,
            result=result,
            executed=owner,
            reused=not owner,
        )

    @staticmethod
    def _signature(tool_name: str, arguments: Mapping[str, Any]) -> str:
        payload = json.dumps(
            {"tool": tool_name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _result_path_value(
    result: Mapping[str, Any],
    path: tuple[str, ...],
) -> Any:
    value: Any = result
    for field in path:
        if not isinstance(value, Mapping) or field not in value:
            raise PolicyViolation(
                "execution guard result path is missing: "
                + "/".join(path)
            )
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
