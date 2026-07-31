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



"""Focused test slice 4; shared fixtures remain local to this slice."""

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
def test_domain_collection_v2_projects_only_the_named_followup_board() -> None:
    artifact = AgentArtifactV2(
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
                    "label": "机器人执行器",
                    "board_code": "BK1234",
                    "board_queries": ["机器人执行器"],
                    "mapping_type": "catalog_binding",
                    "rationale": "机器人执行器对应执行器环节",
                    "unresolved_parts": [],
                    "tier": 1,
                },
                {
                    "label": "传感器",
                    "board_code": "BK5678",
                    "board_queries": ["传感器"],
                    "mapping_type": "catalog_binding",
                    "rationale": "传感器对应感知环节",
                    "unresolved_parts": [],
                    "tier": 2,
                },
                {
                    "label": "人工智能",
                    "board_code": "BK9012",
                    "board_queries": ["人工智能"],
                    "mapping_type": "catalog_binding",
                    "rationale": "人工智能对应决策环节",
                    "unresolved_parts": [],
                    "tier": 3,
                },
            ],
            "domain_collection_v2": {
                "type": "domain_collection_v2",
                "schema_version": "domain-collection-v2.0",
                "catalog_snapshot_id": "catalog_snapshot",
                "requested_topic": "人形机器人哪些领域最受益",
                "benefit_outline": {
                    "topic": "人形机器人",
                    "roles": [
                        {
                            "role_id": "actuator",
                            "label": "机器人执行器",
                            "benefit_mechanism": "执行机构承接运动控制价值量",
                            "tier": 1,
                        }
                    ],
                    "selection_objective": "选择受益最直接的实时板块",
                },
                "boards": [
                    {
                        "board_id": "BK1234",
                        "board_name": "机器人执行器",
                        "role_id": "actuator",
                        "role_label": "机器人执行器",
                        "tier": 1,
                        "rationale": "机器人执行器对应执行器环节",
                    }
                ],
                "result_selection": {
                    "mode": "top_k",
                    "max_items": 16,
                },
                "assumptions": [],
                "coverage": {
                    "catalog_total": 504,
                    "catalog_supplied": 504,
                    "selected_count": 1,
                    "binding_complete": True,
                },
                "lineage": [
                    "catalog:catalog_snapshot",
                    "program:validated_domain_binding",
                ],
            },
        },
    )

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            return _response(
                function_name,
                {
                    "nodes": [
                        {
                            "node_id": "discover",
                            "capability": "theme_stock_discovery",
                            "objective": "从这些领域发现候选",
                            "input_refs": [
                                {
                                    "source": "artifact",
                                    "node_id": None,
                                    "artifact_id": artifact.artifact_id,
                                    "resource_type": "domain_collection",
                                }
                            ],
                            "result_selection": None,
                        }
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
                    "themes": ["机器人执行器（BK1234）"],
                    "output": None,
                },
            )
        raise AssertionError(function_name)

    graph = asyncio.run(
        plan_intent_graph_v2(
            [{"role": "user", "content": "从这些领域继续找股票"}],
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
                            }
                        ],
                    }
                ],
            },
        )
    )
    task = task_plan_from_v2(
        graph,
        artifacts={artifact.artifact_id: artifact},
    ).tasks[0]
    assert len(task.parameters["domains"]) == 1
    assert task.parameters["domains"][0]["label"] == "机器人执行器"
    assert task.parameters["domains"][0]["board_queries"] == ["机器人执行器"]
    assert {item["label"] for item in task.parameters["domains"]}.isdisjoint({"传感器", "人工智能"})
    calls = compile_task(ResolvedTask(candidate=task))
    assert len(calls) == 1
    assert [item["label"] for item in calls[0].arguments["domains"]] == ["机器人执行器"]
    assert "人形机器人" not in json.dumps(
        {"domains": task.parameters["domains"]},
        ensure_ascii=False,
    )

def test_domain_collection_v2_resolves_board_and_role_names_to_one_board_id() -> None:
    artifact = AgentArtifactV2(
        artifact_id="artifact_reducer_domain",
        schema_version="agent-artifact-3.0",
        run_id="prior",
        conversation_id="conversation",
        producer_node_id="industry",
        resource_type=ResourceType.DOMAIN_COLLECTION,
        coverage=CoverageV2(
            requested=504,
            covered=504,
            missing=(),
            complete=True,
        ),
        sources=(),
        produced_at=datetime.now(timezone.utc),
        fingerprint="reducer-domain-fingerprint",
        lineage=(),
        payload={
            "domains": [
                {
                    "label": "减速器",
                    "board_queries": ["减速器"],
                    "mapping_type": "catalog_binding",
                    "rationale": "减速器对应谐波减速器环节",
                    "unresolved_parts": [],
                }
            ],
            "domain_collection_v2": {
                "type": "domain_collection_v2",
                "schema_version": "2.0",
                "catalog_snapshot_id": "catalog_snapshot",
                "requested_topic": "人形机器人哪些领域最受益",
                "benefit_outline": {
                    "topic": "人形机器人",
                    "roles": [
                        {
                            "role_id": "harmonic_reducer",
                            "label": "谐波减速器",
                            "benefit_mechanism": "关节减速传动核心部件",
                            "tier": 1,
                        }
                    ],
                    "selection_objective": "选择受益最直接的实时板块",
                },
                "boards": [
                    {
                        "board_id": "BK1100",
                        "board_name": "减速器",
                        "role_id": "harmonic_reducer",
                        "role_label": "谐波减速器",
                        "tier": 1,
                        "rationale": "减速器对应谐波减速器环节",
                    }
                ],
                "result_selection": {
                    "mode": "top_k",
                    "max_items": 16,
                },
                "assumptions": [],
                "coverage": {
                    "catalog_total": 504,
                    "catalog_supplied": 504,
                    "selected_count": 1,
                    "binding_complete": True,
                },
                "source_name": "实时板块目录",
                "source_date": "2026-07-30T09:51:24+08:00",
                "lineage": ["catalog:catalog_snapshot"],
            },
        },
    )

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            return _response(
                function_name,
                {
                    "nodes": [
                        {
                            "node_id": "discover",
                            "capability": "theme_stock_discovery",
                            "objective": "寻找谐波减速器相关公司",
                            "input_refs": [
                                {
                                    "source": "artifact",
                                    "artifact_id": artifact.artifact_id,
                                    "resource_type": "domain_collection",
                                }
                            ],
                        }
                    ],
                },
            )
        if function_name == "submit_theme_stock_discovery_intent_v2":
            return _response(
                function_name,
                {
                    "selection_mode": "named_subset",
                    "themes": ["减速器", "谐波减速器"],
                },
            )
        raise AssertionError(function_name)

    graph = asyncio.run(
        plan_intent_graph_v2(
            [{"role": "user", "content": "找减速器中的谐波减速器公司"}],
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
                            }
                        ],
                    }
                ],
            },
        )
    )
    task = task_plan_from_v2(
        graph,
        artifacts={artifact.artifact_id: artifact},
    ).tasks[0]

    assert task.parameters["domains"] == [
        {
            "label": "减速器",
            "catalog_snapshot_id": "catalog_snapshot",
            "board_id": "BK1100",
            "board_name": "减速器",
            "role_id": "harmonic_reducer",
            "role_label": "谐波减速器",
            "board_queries": ["减速器"],
            "mapping_type": "catalog_binding",
            "rationale": "减速器对应谐波减速器环节",
            "unresolved_parts": [],
        }
    ]
    calls = compile_task(ResolvedTask(candidate=task))
    assert calls[0].arguments["domains"][0]["board_id"] == "BK1100"
    assert calls[0].arguments["domains"][0]["role_id"] == "harmonic_reducer"
