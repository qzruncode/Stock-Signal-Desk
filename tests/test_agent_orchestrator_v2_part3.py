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



"""Focused test slice 3; shared fixtures remain local to this slice."""

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
def test_all_registered_tools_have_model_generated_args_and_typed_results() -> None:
    registry = ToolRegistry()
    assert len(registry.get_tool_names()) == 66
    for name in registry.get_tool_names():
        tool = registry.get_tool(name)
        assert tool is not None
        assert tool.args_model is not None
        assert tool.result_model is not None
        assert tool.parameters == tool.args_model.model_json_schema()
        assert tool.args_model.model_config.get("extra") == "forbid"
        result_properties = tool.result_model.model_json_schema()["properties"]
        assert {"success", "partial", "errors", "warnings"} <= set(result_properties)
    feed_tool = registry.get_tool("read_financial_feed")
    assert feed_tool is not None
    validated = feed_tool.args_model.model_validate(
        {
            "route_path": "/finance/example/:symbol",
            "params": {"symbol": "600519", "nested": {"page": 1}},
        }
    )
    assert validated.params["nested"]["page"] == 1

def test_first_closed_loop_tools_reject_undeclared_result_fields() -> None:
    registry = ToolRegistry()
    for name in (
        "get_domain_stock_candidates",
        "get_multi_stock_financials",
        "prepare_market_mainline_snapshot",
        "evaluate_market_mainline_gate",
        "evaluate_multi_stock_buy_criteria",
    ):
        tool = registry.get_tool(name)
        assert tool is not None
        assert tool.result_model is not None
        assert tool.result_model.model_config.get("extra") == "forbid"

def test_typed_binding_unwraps_the_named_resource_from_a_tool_envelope() -> None:
    projected = ToolRegistry().project_bound_argument(
        "evaluate_multi_stock_buy_criteria",
        "market_mainline_assessment",
        {
            "success": True,
            "market_mainline_assessment": {
                "dimension_id": "market_mainline",
                "status": "pass",
                "evaluated_subjects": ["减速器"],
                "headline": "减速器属于当前主线",
                "analysis": "多源中期证据支持减速器方向属于当前市场主导叙事。",
                "key_evidence": [],
                "counter_evidence": [],
                "monitoring_points": [],
            },
        },
    )

    assert projected["dimension_id"] == "market_mainline"
    assert projected["status"] == "pass"
    assert "success" not in projected

def test_original_financial_request_plans_and_compiles_against_35_artifact_entities() -> None:
    symbols = tuple(f"{index:06d}" for index in range(1, 36))
    artifact = _artifact(symbols)
    calls: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        calls.append(function_name)
        if function_name == "submit_intent_outline_v2":
            return _response(
                function_name,
                {
                    "nodes": [
                        {
                            "node_id": "filter",
                            "capability": "collection_financial_filter",
                            "objective": "联合剔除不满足三项财务条件的股票",
                            "input_refs": [
                                {
                                    "source": "artifact",
                                    "node_id": None,
                                    "artifact_id": artifact.artifact_id,
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
        if function_name == "submit_collection_financial_filter_intent_v2":
            return _response(function_name, _filter_payload())
        raise AssertionError(function_name)

    semantic_context = {
        "version": "3",
        "turns": [
            {
                "terminal_artifacts": [
                    {
                        "artifact_id": artifact.artifact_id,
                        "resource_type": "security_collection",
                    }
                ],
            }
        ],
    }
    graph = asyncio.run(
        plan_intent_graph_v2(
            [
                {
                    "role": "user",
                    "content": ("剔除其中归母净利润为负，去年营收低于5亿，" "负债率高于70%的股票"),
                }
            ],
            {"model": "test-model"},
            completion=completion,
            semantic_context=semantic_context,
            today=date(2026, 7, 28),
        )
    )

    normalized = CollectionFinancialFilterSpec.model_validate(graph.nodes[0].execution_parameters)
    assert len(normalized.conditions) == 3
    assert {
        (
            condition.metric,
            condition.period_basis,
            condition.fiscal_year,
            condition.threshold,
            condition.threshold_unit,
        )
        for condition in normalized.conditions
    } == {
        ("debt_ratio", "latest_report", None, 70.0, "percent"),
        ("revenue", "fiscal_year", 2025, 500_000_000.0, "cny"),
        ("net_profit", "fiscal_year", 2025, 0.0, "cny"),
    }

    async def unused_completion(**_: Any) -> Any:
        raise AssertionError("financial filter needs no semantic resource call")

    compiled = asyncio.run(
        compile_intent_graph_v2(
            graph,
            {"model": "test-model"},
            completion=unused_completion,
            artifacts={artifact.artifact_id: artifact},
            registry=ToolRegistry(),
        )
    )
    assert compiled.resolved_tasks[0].symbols == symbols
    workflow_calls = compile_task(compiled.resolved_tasks[0])
    assert len(workflow_calls) == 6
    assert calls == [
        "submit_intent_outline_v2",
        "submit_collection_financial_filter_intent_v2",
    ]
    for metric in ("debt_ratio", "revenue", "net_profit"):
        covered = tuple(
            symbol
            for call in workflow_calls
            if call.arguments["metric"] == metric
            for symbol in call.arguments["symbols"].split(",")
        )
        assert covered == symbols

def test_financial_filter_does_not_inherit_domain_parameters_from_security_lineage() -> None:
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
                    "board_code": "BK1100",
                    "board_queries": ["减速器"],
                }
            ],
        },
    )
    security_artifact = _artifact(("001306", "002434")).model_copy(update={"lineage": (domain_artifact.artifact_id,)})
    intent = CollectionFinancialFilterIntent.model_validate(_filter_payload())
    normalized = normalize_capability_intent(
        capability=Capability.COLLECTION_FINANCIAL_FILTER,
        node_id="financial_filter",
        objective="按三项财务条件剔除股票",
        intent=intent,
        input_refs=(
            InputReferenceV2(
                source="artifact",
                artifact_id=security_artifact.artifact_id,
                resource_type=ResourceType.SECURITY_COLLECTION,
            ),
        ),
        result_selection=None,
        current_year=2026,
    )
    outline = IntentOutlineV2.model_validate(
        _with_test_goal(
            {
                "nodes": [
                    {
                        "node_id": "financial_filter",
                        "capability": "collection_financial_filter",
                        "objective": "按三项财务条件剔除股票",
                        "input_refs": [
                            {
                                "source": "artifact",
                                "artifact_id": security_artifact.artifact_id,
                                "resource_type": "security_collection",
                            }
                        ],
                    }
                ],
            }
        )
    )
    graph = PlannedIntentGraphV2(
        run_id="run-financial-filter-lineage",
        outline=outline,
        nodes=(
            PlannedIntentNodeV2(
                outline=outline.nodes[0],
                intent=normalized.intent,
                execution_parameters=normalized.execution_parameters,
                assumptions=normalized.assumptions,
            ),
        ),
        trace=PlanningTraceV2(
            run_id="run-financial-filter-lineage",
            schema_version="test",
        ),
    )

    task = task_plan_from_v2(
        graph,
        artifacts={
            security_artifact.artifact_id: security_artifact,
            domain_artifact.artifact_id: domain_artifact,
        },
    ).tasks[0]

    assert set(task.parameters) == {"conditions"}
    assert task.entities == ["001306", "002434"]
    assert (
        len(
            compile_task(
                ResolvedTask(
                    candidate=task,
                    symbols=tuple(task.entities),
                )
            )
        )
        == 3
    )
