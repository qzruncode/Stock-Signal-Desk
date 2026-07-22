# -*- coding: utf-8 -*-
"""Policy-validated executor for immutable standard-task workflows."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Mapping

from src.agent.task_workflows import (
    ConfirmationState,
    EffectClass,
    ResolvedTask,
    WorkflowCall,
    WorkflowCompileError,
    compile_task,
    workflow_for,
)
from src.tools.registry import ToolRegistry


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
            "reused": self.reused,
        }


@dataclass
class TaskExecutionResult:
    task: ResolvedTask
    status: str
    calls: list[CallOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    blocked_reason: str | None = None

    @property
    def success(self) -> bool:
        return self.status == "completed" and all(call.success for call in self.calls)


@dataclass
class PlanExecutionResult:
    tasks: list[TaskExecutionResult]

    @property
    def evidence(self) -> list[dict[str, Any]]:
        return [call.evidence() for task in self.tasks for call in task.calls]

    @property
    def success(self) -> bool:
        return all(task.success for task in self.tasks)


CallRunner = Callable[[WorkflowCall, dict[str, Any]], Awaitable[dict[str, Any]]]


def action_fingerprint(task: ResolvedTask) -> str:
    """Stable identity for a reviewed task, excluding model-generated prose."""
    payload = json.dumps(
        {
            "kind": task.kind.value,
            "parameters": task.parameters,
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
    ) -> None:
        self.registry = registry
        self.approved_actions = frozenset(approved_actions)

    def preflight_task(self, task: ResolvedTask) -> list[ValidatedCall]:
        spec = workflow_for(task.kind)
        if not spec.enabled:
            machine = " → ".join(spec.state_machine) if spec.state_machine else "未配置"
            raise WorkflowUnavailable(f"{spec.title}当前不可执行；固定状态机：{machine}")

        action = str(task.parameters.get("action") or "")
        fingerprint = action_fingerprint(task)
        if action in spec.confirmation_actions and (
            task.candidate.confirmation != ConfirmationState.EXPLICIT
            or fingerprint not in self.approved_actions
        ):
            raise ConfirmationRequired(task.task_id, action)
        for requirement in spec.parameter_requirements:
            if (
                requirement.applies(task.parameters)
                and requirement.confirmation_required
                and (
                    task.candidate.confirmation != ConfirmationState.EXPLICIT
                    or fingerprint not in self.approved_actions
                )
            ):
                discriminator = ",".join(
                    f"{key}={task.parameters.get(key)}"
                    for key, _values in requirement.when
                ) or task.kind.value
                raise ConfirmationRequired(task.task_id, discriminator)
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
        if len(calls) > spec.max_tool_calls:
            raise PolicyViolation(
                f"{task.task_id} exceeds workflow call budget {spec.max_tool_calls}"
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
            missing_predecessors = set(call.depends_on_steps) - known_steps
            if missing_predecessors:
                raise PolicyViolation(
                    f"{call.step_id} has unknown predecessors {sorted(missing_predecessors)}"
                )
            arguments = self.registry.validate_arguments(call.tool_name, call.arguments)
            validated.append(ValidatedCall(call=call, arguments=arguments))
        self._assert_step_dag(validated)
        return validated

    @staticmethod
    def _assert_step_dag(calls: Iterable[ValidatedCall]) -> None:
        graph = {
            item.call.step_id: set(item.call.depends_on_steps)
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
        max_plan_tool_calls: int = 160,
        approved_actions: Iterable[str] = (),
    ) -> None:
        self.registry = registry
        self.runner = runner
        self.validator = WorkflowPolicyValidator(
            registry,
            approved_actions=approved_actions,
        )
        self.max_plan_tool_calls = max_plan_tool_calls
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
                task_by_id[task_id]
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
        if not validated:
            return TaskExecutionResult(task=task, status="completed")

        pending = {item.call.step_id: item for item in validated}
        completed: dict[str, CallOutcome] = {}
        outcomes: list[CallOutcome] = []
        errors: list[str] = []
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
                outcomes.append(outcome)

            ready = [
                item
                for item in pending.values()
                if set(item.call.depends_on_steps) <= set(completed)
            ]
            if not ready:
                if pending:
                    errors.append("固定 Workflow 的步骤依赖无法继续。")
                break
            step_limit = workflow_for(task.kind).max_parallel_steps
            for start in range(0, len(ready), step_limit):
                batch = await asyncio.gather(
                    *(
                        self._execute_or_reuse(task, item)
                        for item in ready[start:start + step_limit]
                    )
                )
                for outcome in batch:
                    pending.pop(outcome.step_id, None)
                    completed[outcome.step_id] = outcome
                    outcomes.append(outcome)

        status = "completed" if not errors and all(item.success for item in outcomes) else "failed"
        return TaskExecutionResult(task=task, status=status, calls=outcomes, errors=errors)

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


__all__ = [
    "CallOutcome",
    "ConfirmationRequired",
    "PlanExecutionResult",
    "PolicyViolation",
    "TaskExecutionResult",
    "ValidatedCall",
    "WorkflowExecutor",
    "WorkflowPolicyValidator",
    "WorkflowUnavailable",
    "action_fingerprint",
]
