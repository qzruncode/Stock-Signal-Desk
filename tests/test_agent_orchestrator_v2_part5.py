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



"""Focused test slice 5; shared fixtures remain local to this slice."""

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
def test_historical_domain_identity_bridge_collapses_before_parameterization() -> None:
    artifact = AgentArtifactV2(
        artifact_id="artifact_domains",
        schema_version="agent-artifact-3.0",
        run_id="prior",
        conversation_id="conversation",
        producer_node_id="humanoid_robot_industry",
        resource_type=ResourceType.DOMAIN_COLLECTION,
        coverage=CoverageV2(
            requested=504,
            covered=504,
            missing=(),
            complete=True,
        ),
        sources=(),
        produced_at=datetime.now(timezone.utc),
        fingerprint="domain-fingerprint",
        lineage=(),
        payload={
            "domains": [
                {
                    "label": "减速器",
                    "board_code": "BK1100",
                    "board_queries": ["减速器"],
                    "mapping_type": "catalog_binding",
                    "rationale": ("减速器对应谐波减速器环节；" "人形机器人旋转关节核心减速部件"),
                    "unresolved_parts": [],
                    "tier": 1,
                }
            ],
        },
    )
    calls: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        calls.append(function_name)
        if function_name == "submit_intent_outline_v2":
            return _response(
                function_name,
                {
                    "goal": {
                        "objective": "从上轮减速器领域找出相关公司",
                        "question_type": "research",
                        "uncertainty_mode": "bounded",
                        "time_horizon": None,
                        "deliverables": ["减速器领域相关公司候选集合"],
                        "claims": [
                            {
                                "claim_id": "candidates",
                                "question": "哪些公司属于上轮减速器领域候选集合",
                                "required_dimensions": [
                                    "domain_candidates",
                                    "security_identity",
                                ],
                                "optional_dimensions": [],
                                "mandatory": True,
                            }
                        ],
                    },
                    "nodes": [
                        {
                            "node_id": "harmonic_reducer_industry",
                            "capability": "industry_research",
                            "objective": "找出人形机器人谐波减速器环节中大力发展的公司",
                            "input_refs": [
                                {
                                    "source": "artifact",
                                    "artifact_id": artifact.artifact_id,
                                    "resource_type": "domain_collection",
                                }
                            ],
                            "result_selection": None,
                        },
                        {
                            "node_id": "find_stocks",
                            "capability": "theme_stock_discovery",
                            "objective": "从谐波减速器产业板块中找出相关大力发展的公司",
                            "input_refs": [
                                {
                                    "source": "node",
                                    "node_id": "harmonic_reducer_industry",
                                    "resource_type": "domain_collection",
                                }
                            ],
                            "result_selection": None,
                        },
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
            )
        if function_name == "submit_theme_stock_discovery_intent_v2":
            return _response(
                function_name,
                {
                    "selection_mode": "named_subset",
                    "themes": ["减速器（BK1100）：减速器对应“谐波减速器”环节；" "人形机器人旋转关节核心减速部件"],
                    "output": None,
                },
            )
        raise AssertionError(f"identity bridge must not parameterize {function_name}")

    graph = asyncio.run(
        plan_intent_graph_v2(
            [{"role": "user", "content": "找上面第一个减速器中的相关公司"}],
            {"model": "test-model"},
            completion=completion,
            semantic_context={
                "version": "3",
                "turns": [
                    {
                        "terminal_artifacts": [
                            {
                                "artifact_id": artifact.artifact_id,
                                "resource_type": "domain_collection",
                                "producer_node_id": artifact.producer_node_id,
                            }
                        ],
                    }
                ],
            },
        )
    )

    assert calls == [
        "submit_intent_outline_v2",
        "submit_theme_stock_discovery_intent_v2",
    ]
    assert len(graph.nodes) == 1
    node = graph.nodes[0]
    assert node.outline.node_id == "find_stocks"
    assert [ref.model_dump(mode="json") for ref in node.outline.input_refs] == [
        {
            "source": "artifact",
            "node_id": None,
            "artifact_id": artifact.artifact_id,
            "resource_type": "domain_collection",
        }
    ]

    async def unused_completion(**_: Any) -> Any:
        raise AssertionError("a validated DomainCollection artifact must not be rebound by a model")

    compiled = asyncio.run(
        compile_intent_graph_v2(
            graph,
            {"model": "test-model"},
            completion=unused_completion,
            artifacts={artifact.artifact_id: artifact},
            registry=ToolRegistry(),
        )
    )
    assert len(compiled.resolved_tasks) == 1
    assert compiled.resolved_tasks[0].candidate.depends_on == []
    assert compiled.resolved_tasks[0].candidate.parameters["domains"] == [
        {
            "label": "减速器",
            "board_queries": ["减速器"],
            "mapping_type": "catalog_binding",
            "rationale": ("减速器对应谐波减速器环节；" "人形机器人旋转关节核心减速部件"),
            "unresolved_parts": [],
        }
    ]

def test_named_domain_projection_never_widens_an_unmatched_subset() -> None:
    domains = [
        {
            "label": "机器人执行器",
            "board_code": "BK1145",
            "board_queries": ["机器人执行器"],
        },
        {
            "label": "传感器",
            "board_code": "BK1000",
            "board_queries": ["传感器"],
        },
    ]

    with pytest.raises(OrchestratorV2Error) as exc:
        _project_artifact_domain_subset(
            domains,
            [{"label": "不存在的板块"}],
            selection_mode="named_subset",
            task_id="discover",
        )

    assert exc.value.code == AgentErrorCode.RESOURCE_UNAVAILABLE
    assert "没有扩大为整个上游集合" in str(exc.value)
    assert (
        _project_artifact_domain_subset(
            domains,
            [],
            selection_mode="all_bound",
            task_id="discover",
        )
        == domains
    )

def test_investment_decision_recovers_structured_thesis_from_collection_lineage() -> None:
    domain_artifact = AgentArtifactV2(
        artifact_id="artifact_domains",
        schema_version="agent-artifact-3.0",
        run_id="prior",
        conversation_id="conversation",
        producer_node_id="industry",
        resource_type=ResourceType.DOMAIN_COLLECTION,
        coverage=CoverageV2(
            requested=1,
            covered=1,
            missing=(),
            complete=True,
        ),
        sources=(),
        produced_at=datetime.now(timezone.utc),
        fingerprint="domain-fingerprint",
        lineage=(),
        payload={
            "domains": [
                {
                    "label": "减速器",
                    "board_queries": ["减速器"],
                    "mapping_type": "catalog_binding",
                    "rationale": "人形机器人关节传动环节",
                    "unresolved_parts": [],
                    "tier": 1,
                }
            ],
        },
    )
    security_artifact = _artifact(("000001", "000002")).model_copy(update={"lineage": (domain_artifact.artifact_id,)})

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            return _response(
                function_name,
                {
                    "nodes": [
                        {
                            "node_id": "decision",
                            "capability": "investment_decision",
                            "objective": "通过的这些股票哪些现在能买",
                            "input_refs": [
                                {
                                    "source": "artifact",
                                    "node_id": None,
                                    "artifact_id": security_artifact.artifact_id,
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
            [{"role": "user", "content": "通过的这些股票哪些现在能买"}],
            {"model": "test-model"},
            completion=completion,
            semantic_context={
                "version": "3",
                "turns": [
                    {
                        "terminal_artifacts": [
                            {
                                "artifact_id": security_artifact.artifact_id,
                                "resource_type": "security_collection",
                                "fingerprint": security_artifact.fingerprint,
                                "producer_node_id": security_artifact.producer_node_id,
                            }
                        ],
                    }
                ],
            },
        )
    )
    task = task_plan_from_v2(
        graph,
        artifacts={
            security_artifact.artifact_id: security_artifact,
            domain_artifact.artifact_id: domain_artifact,
        },
    ).tasks[0]

    assert task.parameters["thesis"] == "减速器"
    assert task.parameters["thesis_context"]["summary"] == "减速器"
    assert task.parameters["thesis_context"]["domains"][0]["board_queries"] == ["减速器"]
    assert task.parameters["mainline_strategy"] == "confirmed_mainline"
    assert any(
        assumption.field_path == "/mainline_strategy" and assumption.value == "confirmed_mainline"
        for assumption in graph.nodes[0].assumptions
    )
