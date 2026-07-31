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



"""Focused test slice 6; shared fixtures remain local to this slice."""

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
def test_historical_producer_node_ref_is_bound_to_the_unique_matching_artifact() -> None:
    financial_artifact = _artifact(("000001", "000002")).model_copy(
        update={
            "artifact_id": "artifact_financial_filter",
            "producer_node_id": "collection_financial_filter",
            "fingerprint": "financial-filter-fingerprint",
        }
    )
    later_decision_artifact = _artifact(("000001",)).model_copy(
        update={
            "artifact_id": "artifact_previous_decision",
            "producer_node_id": "investment_decision",
            "fingerprint": "previous-decision-fingerprint",
        }
    )
    called_functions: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        called_functions.append(function_name)
        if function_name == "submit_intent_outline_v2":
            return _response(
                function_name,
                {
                    "nodes": [
                        {
                            "node_id": "investment_decision",
                            "capability": "investment_decision",
                            "objective": "对上一轮财务筛选通过的股票执行买入判断",
                            "input_refs": [
                                {
                                    "source": "node",
                                    "node_id": "collection_financial_filter",
                                    "artifact_id": None,
                                    "resource_type": "security_collection",
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
                    "thesis": None,
                    "output": None,
                },
            )
        raise AssertionError(function_name)

    graph = asyncio.run(
        plan_intent_graph_v2(
            [
                {
                    "role": "user",
                    "content": "通过的这些股票，哪些现在就能买？",
                }
            ],
            {"model": "test-model"},
            completion=completion,
            semantic_context={
                "version": "3",
                "turns": [
                    {
                        "terminal_artifacts": [
                            {
                                "artifact_id": financial_artifact.artifact_id,
                                "resource_type": "security_collection",
                                "producer_node_id": (financial_artifact.producer_node_id),
                            }
                        ],
                    },
                    {
                        "terminal_artifacts": [
                            {
                                "artifact_id": financial_artifact.artifact_id,
                                "resource_type": "security_collection",
                                "producer_node_id": (financial_artifact.producer_node_id),
                            },
                            {
                                "artifact_id": later_decision_artifact.artifact_id,
                                "resource_type": "security_collection",
                                "producer_node_id": (later_decision_artifact.producer_node_id),
                            },
                        ],
                    },
                ],
            },
        )
    )

    raw_ref = graph.trace.raw_outline["nodes"][0]["input_refs"][0]
    normalized_ref = graph.trace.normalized_outline["nodes"][0]["input_refs"][0]
    assert raw_ref["source"] == "node"
    assert raw_ref["node_id"] == "collection_financial_filter"
    assert normalized_ref == {
        "source": "artifact",
        "node_id": None,
        "artifact_id": financial_artifact.artifact_id,
        "resource_type": "security_collection",
    }
    assert graph.outline.nodes[0].input_refs[0].artifact_id == (financial_artifact.artifact_id)
    task = task_plan_from_v2(
        graph,
        artifacts={
            financial_artifact.artifact_id: financial_artifact,
            later_decision_artifact.artifact_id: later_decision_artifact,
        },
    ).tasks[0]
    assert task.entities == ["000001", "000002"]
    assert called_functions == [
        "submit_intent_outline_v2",
        "submit_investment_decision_intent_v2",
    ]
    assert graph.trace.repairs == ()

def test_ambiguous_historical_node_ref_requests_minimal_clarification() -> None:
    events: list[AgentStageEventV2] = []
    outline_calls = 0
    stale_payload = {
        "nodes": [
            {
                "node_id": "investment_decision",
                "capability": "investment_decision",
                "objective": "判断这些股票哪些可以买入",
                "input_refs": [
                    {
                        "source": "node",
                        "node_id": "collection_financial_filter",
                        "artifact_id": None,
                        "resource_type": "security_collection",
                    }
                ],
                "result_selection": None,
            }
        ],
        "needs_clarification": False,
        "clarification_question": None,
    }

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal outline_calls
        function_name = _function_name(kwargs)
        assert function_name == "submit_intent_outline_v2"
        outline_calls += 1
        return _response(function_name, stale_payload)

    with pytest.raises(OrchestratorV2Error) as captured:
        asyncio.run(
            plan_intent_graph_v2(
                [{"role": "user", "content": "这些股票哪些可以买入"}],
                {"model": "test-model"},
                completion=completion,
                semantic_context={
                    "version": "3",
                    "turns": [
                        {
                            "terminal_artifacts": [
                                {
                                    "artifact_id": "artifact_first",
                                    "resource_type": "security_collection",
                                    "producer_node_id": "first_filter",
                                },
                                {
                                    "artifact_id": "artifact_second",
                                    "resource_type": "security_collection",
                                    "producer_node_id": "second_filter",
                                },
                            ],
                        }
                    ],
                },
                stage_observer=events.append,
            )
        )

    assert captured.value.code == AgentErrorCode.CLARIFICATION_REQUIRED
    assert outline_calls == 1
    assert str(captured.value) == (
        "找到多个可用的 security_collection 历史结果，" "请说明要使用哪一轮或哪一次筛选结果。"
    )
    assert events[-1].summary == str(captured.value)
    assert "validation error" not in events[-1].summary

def test_unknown_historical_node_keeps_precise_issue_private() -> None:
    events: list[AgentStageEventV2] = []
    outline_calls = 0
    repair_context: dict[str, Any] = {}
    stale_payload = {
        "nodes": [
            {
                "node_id": "investment_decision",
                "capability": "investment_decision",
                "objective": "判断这些股票哪些可以买入",
                "input_refs": [
                    {
                        "source": "node",
                        "node_id": "collection_financial_filter",
                        "artifact_id": None,
                        "resource_type": "security_collection",
                    }
                ],
                "result_selection": None,
            }
        ],
        "needs_clarification": False,
        "clarification_question": None,
    }

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal outline_calls
        function_name = _function_name(kwargs)
        assert function_name == "submit_intent_outline_v2"
        outline_calls += 1
        if outline_calls == 2:
            repair_context.update(json.loads(kwargs["messages"][-1]["content"]))
        return _response(function_name, stale_payload)

    with pytest.raises(OrchestratorV2Error) as captured:
        asyncio.run(
            plan_intent_graph_v2(
                [{"role": "user", "content": "这些股票哪些可以买入"}],
                {"model": "test-model"},
                completion=completion,
                semantic_context={"version": "3", "turns": []},
                stage_observer=events.append,
            )
        )

    assert captured.value.code == AgentErrorCode.PLANNER_SCHEMA_INVALID
    assert outline_calls == 2
    repair = repair_context["targeted_repair"]
    assert repair["issues"] == [
        {
            "pointer": "/nodes/0/input_refs/0",
            "code": "unknown_node_reference",
            "expected": ("a current-graph node_id or one uniquely resolvable " "historical artifact"),
            "allowed": [],
            "message": (
                "'collection_financial_filter' is not a node in this graph " "and resolved to 0 compatible artifacts"
            ),
        }
    ]
    assert events[-1].summary == ("任务图未通过内部强类型契约校验；" "本轮没有调用任何数据工具")
    assert "validation error" not in events[-1].summary
    assert "collection_financial_filter" not in events[-1].summary

def test_targeted_repair_keeps_the_frozen_graph() -> None:
    attempts = 0
    repair_context: dict[str, Any] = {}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal attempts
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            return _response(
                function_name,
                {
                    "nodes": [
                        {
                            "node_id": "filter",
                            "capability": "collection_financial_filter",
                            "objective": "筛选",
                            "input_refs": [],
                            "result_selection": None,
                        }
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
            )
        attempts += 1
        if attempts == 1:
            invalid = _filter_payload()
            invalid["predicates"][0]["batch_size"] = 24
            return _response(function_name, invalid)
        repair_context.update(json.loads(kwargs["messages"][-1]["content"]))
        return _response(function_name, _filter_payload())

    graph = asyncio.run(
        plan_intent_graph_v2(
            [{"role": "user", "content": "筛选这些股票"}],
            {"model": "test-model"},
            completion=completion,
            semantic_context={},
            today=date(2026, 7, 28),
        )
    )
    assert attempts == 2
    assert graph.nodes[0].outline.node_id == "filter"
    repair = repair_context["targeted_repair"]
    assert repair["invalid_payload"]["predicates"][0]["batch_size"] == 24
    assert any(issue["pointer"].endswith("/batch_size") for issue in repair["issues"])
