# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import json
from typing import Any
from unittest.mock import patch

import pytest

from src.agent.orchestrator_v2.contracts import (
    AgentArtifactV2,
    AgentErrorCode,
    AgentStageEventV2,
    CacheReuseScope,
    Capability,
    CoverageV2,
    InputReferenceV2,
    IntentOutlineV2,
    OrchestratorV2Error,
    PlanningTraceV2,
    ResourceType,
)
from src.agent.orchestrator_v2.cache import (
    execution_cache_key_v2,
    save_execution_cache_v2,
)
from src.agent.orchestrator_v2.intents import CollectionFinancialFilterIntent
from src.agent.orchestrator_v2.outcomes import task_outcome_v2
from src.agent.orchestrator_v2.planner import (
    PlannedIntentGraphV2,
    PlannedIntentNodeV2,
    _payload_from_response,
    plan_intent_graph_v2,
)
from src.agent.orchestrator_v2.registry import (
    CAPABILITY_REGISTRY,
    capability_catalog,
    capability_for,
    migration_coverage,
    normalize_capability_intent,
)
from src.agent.orchestrator_v2.runtime import (
    CompiledIntentGraphV2,
    CompiledTaskV2,
    _project_artifact_domain_subset,
    compile_intent_graph_v2,
    restore_compiled_intent_graph_v2,
    serialize_compiled_intent_graph_v2,
    task_plan_from_v2,
)
from src.agent.orchestrator_v2.state import (
    ConversationContextV2,
    TurnSummaryV2,
    migrate_legacy_context,
)
from src.agent.result_contracts import CollectionFinancialFilterSpec
from src.agent.task_executor import TaskExecutionResult, action_fingerprint
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResolvedTask,
    SecurityEntity,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
    WorkflowCall,
    compile_task,
    workflow_for,
)
from src.storage.manager import DatabaseManager
from src.tools.registry import ToolRegistry



"""Focused test slice 7; shared fixtures remain local to this slice."""

def _response(function_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if function_name == "submit_intent_outline_v2":
        payload = _with_test_goal(payload)
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": function_name,
                                "arguments": json.dumps(payload, ensure_ascii=False),
                            },
                        }
                    ],
                },
            }
        ],
    }

def _with_test_goal(payload: dict[str, Any]) -> dict[str, Any]:
    if "goal" in payload:
        return payload
    nodes = [item for item in payload.get("nodes") or () if isinstance(item, dict) and item.get("capability")]
    capabilities = [Capability(str(item["capability"])) for item in nodes]
    specs = [capability_for(item) for item in capabilities]
    if any(Capability.INVESTMENT_DECISION == item for item in capabilities):
        question_type = "decision"
        uncertainty_mode = "bounded"
    elif any(spec.execution_policy.effect.value != "read" for spec in specs):
        question_type = "operation"
        uncertainty_mode = "not_applicable"
    elif any(
        __import__(
            "src.agent.orchestrator_v2.contracts",
            fromlist=["QuestionType"],
        ).QuestionType.RESEARCH
        in spec.supported_question_types
        for spec in specs
    ):
        question_type = "research"
        uncertainty_mode = "bounded"
    else:
        question_type = "direct"
        uncertainty_mode = "bounded"
    dimensions = sorted({dimension.value for spec in specs for dimension in spec.evidence_dimensions})
    objective = (
        "；".join(
            str(item.get("objective") or "").strip() for item in nodes if str(item.get("objective") or "").strip()
        )
        or "完成当前请求"
    )
    return {
        **payload,
        "goal": {
            "objective": objective,
            "question_type": question_type,
            "uncertainty_mode": uncertainty_mode,
            "time_horizon": None,
            "deliverables": [objective],
            "claims": [
                {
                    "claim_id": "answer",
                    "question": objective,
                    "required_dimensions": dimensions or ["general_knowledge"],
                    "optional_dimensions": [],
                    "mandatory": True,
                }
            ],
        },
    }

def _function_name(kwargs: dict[str, Any]) -> str:
    return kwargs["tool_choice"]["function"]["name"]

def _filter_payload() -> dict[str, Any]:
    return {
        "predicates": [
            {
                "metric": "net_profit",
                "operator": "lt",
                "amount": {"value": 0, "unit": "cny"},
                "period": {"kind": "previous_fiscal_year"},
                "action": "exclude_matching",
            },
            {
                "metric": "revenue",
                "operator": "lt",
                "amount": {"value": 5, "unit": "yi_cny"},
                "period": {"kind": "previous_fiscal_year"},
                "action": "exclude_matching",
            },
            {
                "metric": "debt_ratio",
                "operator": "gt",
                "percent": 70,
                "period": None,
                "action": "exclude_matching",
            },
        ],
        "output": None,
    }

def _artifact(symbols: tuple[str, ...]) -> AgentArtifactV2:
    return AgentArtifactV2(
        artifact_id="artifact_candidates",
        schema_version="agent-artifact-3.0",
        run_id="prior",
        conversation_id="conversation",
        producer_node_id="discover",
        resource_type=ResourceType.SECURITY_COLLECTION,
        coverage=CoverageV2(
            requested=len(symbols),
            covered=len(symbols),
            missing=(),
            complete=True,
        ),
        sources=(),
        produced_at=datetime.now(timezone.utc),
        fingerprint="candidate-fingerprint",
        lineage=(),
        payload={
            "securities": [{"symbol": symbol, "name": symbol} for symbol in symbols],
        },
    )
def test_exact_contract_unwraps_provider_stringified_nested_models() -> None:
    goal = _with_test_goal(
        {
            "nodes": [
                {
                    "node_id": "lookup",
                    "capability": "security_lookup",
                    "objective": "查找贵州茅台",
                    "input_refs": [],
                    "result_selection": None,
                }
            ],
        }
    )["goal"]

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            return _response(
                function_name,
                {
                    "goal": json.dumps(goal, ensure_ascii=False),
                    "nodes": [
                        {
                            "node_id": "lookup",
                            "capability": "security_lookup",
                            "objective": "查找贵州茅台",
                            "input_refs": [],
                            "result_selection": None,
                        }
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
            )
        if function_name == "submit_security_lookup_intent_v2":
            return _response(function_name, {"query": "贵州茅台"})
        raise AssertionError(function_name)

    graph = asyncio.run(
        plan_intent_graph_v2(
            [{"role": "user", "content": "查找贵州茅台"}],
            {"model": "test-model"},
            completion=completion,
            today=date(2026, 7, 28),
        )
    )

    assert graph.outline.goal.objective
    assert graph.outline.nodes[0].capability == Capability.SECURITY_LOOKUP

def test_outline_repairs_result_selection_from_capability_contract() -> None:
    outline_attempts = 0
    repair_context: dict[str, Any] = {}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal outline_attempts
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            outline_attempts += 1
            if outline_attempts == 2:
                repair_context.update(json.loads(kwargs["messages"][-1]["content"]))
            return _response(
                function_name,
                {
                    "nodes": [
                        {
                            "node_id": "lookup",
                            "capability": "security_lookup",
                            "objective": "查找用户明确要求的证券",
                            "input_refs": [],
                            "result_selection": (
                                {
                                    "mode": "all_relevant",
                                    "max_items": None,
                                }
                                if outline_attempts == 1
                                else None
                            ),
                        }
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
            )
        if function_name == "submit_security_lookup_intent_v2":
            return _response(
                function_name,
                {
                    "query": "贵州茅台",
                },
            )
        raise AssertionError(function_name)

    graph = asyncio.run(
        plan_intent_graph_v2(
            [{"role": "user", "content": "查找贵州茅台"}],
            {"model": "test-model"},
            completion=completion,
            today=date(2026, 7, 28),
        )
    )

    assert outline_attempts == 2
    assert graph.outline.nodes[0].result_selection is None
    repair = repair_context["targeted_repair"]
    assert repair["invalid_payload"]["nodes"][0]["result_selection"] == {
        "mode": "all_relevant",
        "max_items": None,
    }
    assert repair["issues"] == [
        {
            "pointer": "/nodes/0/result_selection",
            "code": "capability_result_selection_forbidden",
            "expected": ("null because capability security_lookup does not support " "result selection"),
            "allowed": [None],
            "message": "security_lookup does not support result_selection",
        }
    ]

def test_outline_removes_capabilities_owned_by_investment_workflow() -> None:
    outline_attempts = 0
    repair_context: dict[str, Any] = {}
    parameterized_functions: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal outline_attempts
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            outline_attempts += 1
            if outline_attempts == 2:
                repair_context.update(json.loads(kwargs["messages"][-1]["content"]))
            decision_node = {
                "node_id": "decision",
                "capability": "investment_decision",
                "objective": "判断平安银行现在是否可以买入",
                "input_refs": [],
                "result_selection": None,
            }
            if outline_attempts == 1:
                return _response(
                    function_name,
                    {
                        "nodes": [
                            {
                                **decision_node,
                                "input_refs": [
                                    {
                                        "source": "node",
                                        "node_id": None,
                                        "resource_type": "security_collection",
                                    }
                                ],
                            }
                        ],
                        "needs_clarification": False,
                        "clarification_question": None,
                    },
                )
            nodes = [
                {
                    "node_id": "lookup",
                    "capability": "security_lookup",
                    "objective": "识别平安银行",
                    "input_refs": [],
                    "result_selection": None,
                },
                {
                    "node_id": "valuation",
                    "capability": "valuation_analysis",
                    "objective": "分析平安银行估值",
                    "input_refs": [],
                    "result_selection": None,
                },
                {
                    **decision_node,
                    "input_refs": [
                        {
                            "source": "node",
                            "node_id": "lookup",
                            "artifact_id": None,
                            "resource_type": "security_collection",
                        }
                    ],
                },
            ]
            return _response(
                function_name,
                {
                    "nodes": nodes,
                    "needs_clarification": False,
                    "clarification_question": None,
                },
            )
        parameterized_functions.append(function_name)
        if function_name == "submit_investment_decision_intent_v2":
            return _response(
                function_name,
                {
                    "thesis": None,
                    "output": None,
                },
            )
        raise AssertionError(function_name)

    graph = asyncio.run(
        plan_intent_graph_v2(
            [{"role": "user", "content": "平安银行（000001）现在能买入吗？"}],
            {"model": "test-model"},
            completion=completion,
            today=date(2026, 7, 28),
        )
    )

    assert outline_attempts == 2
    assert [node.capability for node in graph.outline.nodes] == [Capability.INVESTMENT_DECISION]
    assert parameterized_functions == ["submit_investment_decision_intent_v2"]
    repair = repair_context["targeted_repair"]
    assert any(issue["pointer"] == "/nodes/0/input_refs/0" for issue in repair["issues"])
    assert graph.outline.nodes[0].input_refs == ()

def test_outline_drops_type_impossible_resource_edge_before_binding() -> None:
    outline_attempts = 0

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal outline_attempts
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            outline_attempts += 1
            return _response(
                function_name,
                {
                    "nodes": [
                        {
                            "node_id": "decision",
                            "capability": "investment_decision",
                            "objective": "判断贵州茅台现在是否可以买入",
                            "input_refs": [
                                {
                                    "source": "artifact",
                                    "artifact_id": "unrelated-evidence",
                                    "node_id": None,
                                    "resource_type": "evidence_collection",
                                }
                            ],
                            "result_selection": None,
                        }
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
            )
        if function_name == "submit_investment_decision_intent_v2":
            return _response(
                function_name,
                {
                    "thesis": "判断贵州茅台现在是否可以买入",
                    "output": None,
                },
            )
        raise AssertionError(function_name)

    graph = asyncio.run(
        plan_intent_graph_v2(
            [{"role": "user", "content": "贵州茅台现在能买入吗？"}],
            {"model": "test-model"},
            completion=completion,
            today=date(2026, 7, 29),
        )
    )

    assert outline_attempts == 1
    assert graph.outline.nodes[0].capability == Capability.INVESTMENT_DECISION
    assert graph.outline.nodes[0].input_refs == ()
    assert graph.trace.raw_outline["nodes"][0]["input_refs"][0]["resource_type"] == "evidence_collection"
    assert graph.trace.normalized_outline["nodes"][0]["input_refs"] == []
