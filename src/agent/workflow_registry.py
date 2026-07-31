"""Immutable workflow registry assembled from typed specification modules."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from src.agent.task_workflows import (
    ParameterRequirement,
    ResolvedTask,
    StandardTaskKind,
    WorkflowCall,
    WorkflowCompileError,
    WorkflowSpec,
    parameter_requirement_issues,
)
from src.agent.workflow_registry_core import (
    build_core_registry,
    make_spec,
    requirement,
)
from src.agent.workflow_registry_extended import build_extended_registry


_WORKFLOW_REGISTRY = {
    **build_core_registry(make_spec),
    **build_extended_registry(make_spec, requirement),
}

# Neither the planner nor request-local code can mutate the production
# Workflow Registry after module initialization.
WORKFLOW_REGISTRY: Mapping[StandardTaskKind, WorkflowSpec] = MappingProxyType(_WORKFLOW_REGISTRY)


def workflow_for(kind: StandardTaskKind) -> WorkflowSpec:
    return WORKFLOW_REGISTRY[kind]


def compile_task(task: ResolvedTask) -> list[WorkflowCall]:
    spec = workflow_for(task.kind)
    if not spec.enabled:
        raise WorkflowCompileError(f"{spec.title}当前不可执行")
    if spec.supports_result_selection and task.result_selection is None:
        raise WorkflowCompileError(f"{task.kind.value} requires a typed result_selection")
    if not spec.supports_result_selection and task.result_selection is not None:
        raise WorkflowCompileError(f"{task.kind.value} does not support result_selection")
    if spec.requires_entities and not task.symbols:
        raise WorkflowCompileError(f"{task.kind.value} requires resolved entities")
    conditional_issues = parameter_requirement_issues(
        task.candidate,
        spec,
        symbols=task.symbols,
        check_confirmation=False,
    )
    if conditional_issues:
        raise WorkflowCompileError(
            f"{task.kind.value} violates its conditional contract: " + "; ".join(conditional_issues)
        )
    calls = spec.compiler(task)
    if len(calls) > spec.max_tool_calls:
        raise WorkflowCompileError(f"{task.kind.value} compiled {len(calls)} calls, exceeding {spec.max_tool_calls}")
    for call in calls:
        if call.tool_name not in spec.tool_whitelist:
            raise WorkflowCompileError(f"{call.tool_name} is outside the {task.kind.value} whitelist")
    return calls


def registered_workflow_tools() -> frozenset[str]:
    return frozenset(tool for spec in WORKFLOW_REGISTRY.values() for tool in spec.tool_whitelist)


__all__ = [
    "WORKFLOW_REGISTRY",
    "compile_task",
    "registered_workflow_tools",
    "workflow_for",
]
