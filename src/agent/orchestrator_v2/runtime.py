# -*- coding: utf-8 -*-
"""Compilation bridge from frozen V2 intents to existing fixed workflows."""

from __future__ import annotations

from dataclasses import dataclass, replace
import inspect
import re
from typing import Any, Awaitable, Callable, Mapping

from src.agent.orchestrator_v2.contracts import (
    AgentArtifactV2,
    AssumptionRecord,
    CompiledCallV2,
    AgentErrorCode,
    AgentStage,
    AgentStageEventV2,
    ExecutionPolicy,
    FreshnessPolicy,
    Capability,
    PlanningTraceV2,
    OrchestratorV2Error,
    ResourceType,
    StageObserver,
    StageStatus,
    stable_fingerprint,
)
from src.agent.orchestrator_v2.planner import PlannedIntentGraphV2
from src.agent.orchestrator_v2.registry import capability_for
from src.agent.result_contracts import (
    DomainBoardQuerySpec,
    InvestmentThesisContext,
)
from src.agent.task_planner import (
    SemanticResourceBindingUnavailableError,
    TaskPlanValidationError,
    bind_task_plan_resources,
    resolve_plan_entities,
    validate_candidate_plan,
)
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResolvedTask,
    ResultSelectionMode,
    ResultSelectionSpec,
    StandardTask,
    StandardTaskKind,
    TaskResource,
    TaskPlan,
    WorkflowCall,
    workflow_for,
)
from src.tools.registry import ToolRegistry


@dataclass(frozen=True)
class CompiledTaskV2:
    task: ResolvedTask
    capability: Capability
    capability_version: str
    intent_schema_version: str
    execution_policy: ExecutionPolicy
    resource_fingerprint: str
    freshness_policy: FreshnessPolicy = FreshnessPolicy()
    input_artifact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledIntentGraphV2:
    run_id: str
    plan: TaskPlan
    tasks: tuple[CompiledTaskV2, ...]
    assumptions: tuple[Any, ...]

    @property
    def resolved_tasks(self) -> list[ResolvedTask]:
        return [item.task for item in self.tasks]

    @property
    def policy_by_task_id(self) -> Mapping[str, ExecutionPolicy]:
        return {item.task.task_id: item.execution_policy for item in self.tasks}



__all__ = [
    "CompiledIntentGraphV2",
    "CompiledTaskV2",
    "compile_intent_graph_v2",
    "compile_workflow_call_v2",
    "restore_compiled_intent_graph_v2",
    "serialize_compiled_intent_graph_v2",
    "task_plan_from_v2",
]


from . import _runtime_functions1 as _runtime_functions1
from . import _runtime_functions2 as _runtime_functions2
from . import _runtime_functions3 as _runtime_functions3


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_runtime_functions1, _runtime_functions2, _runtime_functions3):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))
