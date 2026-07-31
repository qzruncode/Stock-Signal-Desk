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



"""Focused test slice 9; shared fixtures remain local to this slice."""

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
def test_legacy_ranked_domains_migrate_as_typed_resource_not_answer_text() -> None:
    context, artifacts = migrate_legacy_context(
        {
            "version": "1",
            "turns": [
                {
                    "request": "人形机器人最受益领域",
                    "entities": [],
                    "tasks": [
                        {
                            "task_id": "industry",
                            "kind": "industry_research",
                            "objective": "结构化领域排序",
                            "status": "completed",
                            "semantic_artifacts": [
                                {
                                    "type": "ranked_domains",
                                    "topic": "人形机器人",
                                    "groups": [
                                        {
                                            "tier": 1,
                                            "domains": [
                                                {
                                                    "label": "机器人执行器",
                                                    "board_queries": ["机器人执行器"],
                                                    "mapping_type": "catalog_binding",
                                                    "rationale": "执行器是核心环节",
                                                    "unresolved_parts": [],
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        conversation_id="conversation",
    )
    domain_artifact = next(item for item in artifacts if item.resource_type == ResourceType.DOMAIN_COLLECTION)
    assert domain_artifact.payload["domains"][0]["label"] == "机器人执行器"
    assert context.turns[0].terminal_artifacts[0].artifact_id == (domain_artifact.artifact_id)
    assert "answer" not in domain_artifact.model_dump_json().lower()

def test_program_defaults_are_recorded_not_model_invented() -> None:
    normalized = normalize_capability_intent(
        node_id="discover",
        objective="发现候选",
        capability=Capability.THEME_STOCK_DISCOVERY,
        intent={
            "selection_mode": "named_subset",
            "themes": ["低空经济"],
        },
        input_refs=(),
        result_selection=None,
        current_year=2026,
    )
    assert {item.field_path for item in normalized.assumptions} == {
        "/output/language",
        "/output/format",
        "/output/include_assumptions",
    }
    context = ConversationContextV2()
    payload = context.planner_payload()
    assert "parameters" not in json.dumps(payload)
    assert "tool_name" not in json.dumps(payload)

    confirmed = normalize_capability_intent(
        node_id="decision-default",
        objective="判断现在能否买入",
        capability=Capability.INVESTMENT_DECISION,
        intent={"thesis": "减速器"},
        input_refs=(),
        result_selection=None,
        current_year=2026,
    )
    assert confirmed.execution_parameters["mainline_strategy"] == ("confirmed_mainline")
    assert any(item.field_path == "/mainline_strategy" for item in confirmed.assumptions)

    early = normalize_capability_intent(
        node_id="decision-early",
        objective="提前布局下一阶段主线",
        capability=Capability.INVESTMENT_DECISION,
        intent={
            "thesis": "减速器",
            "mainline_strategy": "early_positioning",
        },
        input_refs=(),
        result_selection=None,
        current_year=2026,
    )
    assert early.execution_parameters["mainline_strategy"] == ("early_positioning")
    assert not any(item.field_path == "/mainline_strategy" for item in early.assumptions)

def test_planner_context_deduplicates_retries_and_excludes_current_request() -> None:
    context = ConversationContextV2(
        turns=(
            TurnSummaryV2(
                run_id="filter",
                request_summary="按财务条件筛选这些股票",
            ),
            TurnSummaryV2(
                run_id="decision-failed",
                request_summary="通过的这些股票，哪些现在就能买？",
            ),
            TurnSummaryV2(
                run_id="decision-succeeded",
                request_summary="  通过的这些股票，哪些现在就能买？  ",
            ),
            TurnSummaryV2(
                run_id="other",
                request_summary="解释筛选口径",
            ),
        )
    )

    deduplicated = context.planner_payload()
    assert [turn["run_id"] for turn in deduplicated["turns"]] == [
        "filter",
        "decision-succeeded",
        "other",
    ]

    fresh_run = context.planner_payload(
        current_request="通过的这些股票，哪些现在就能买？",
    )
    assert [turn["run_id"] for turn in fresh_run["turns"]] == [
        "filter",
        "other",
    ]

def test_real_35_candidate_decision_compiles_one_snapshot_and_every_stock() -> None:
    symbols = tuple(f"{index:06d}" for index in range(1, 36))
    task = StandardTask(
        task_id="decision",
        kind=StandardTaskKind.INVESTMENT_DECISION,
        objective="逐股八维判断",
        entity_scope=EntityScope.NONE,
        depends_on=["filter"],
    )
    calls = compile_task(ResolvedTask(candidate=task, symbols=symbols))
    assert len(calls) == 37
    assert calls[0].tool_name == "prepare_market_mainline_snapshot"
    assert calls[1].tool_name == "evaluate_market_mainline_gate"
    assert tuple(call.arguments["symbols"] for call in calls[2:]) == symbols
    assert all(
        call.result_bindings
        == (
            ("market_mainline_snapshot", "market_mainline_snapshot"),
            ("market_mainline_assessment", "market_mainline_gate"),
            ("market_mainline_model_error", "market_mainline_gate"),
        )
        for call in calls[2:]
    )

def test_company_evidence_coverage_counts_every_terminal_candidate() -> None:
    task = StandardTask(
        task_id="company_evidence",
        kind=StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        objective="逐股核验候选公司",
        entity_scope=EntityScope.NONE,
        execution_parameters={"candidate_scope": "candidate_collection"},
    )
    resolved = ResolvedTask(
        candidate=task,
        symbols=("000001", "000002", "000003"),
        entity_names=(
            ("000001", "甲公司"),
            ("000002", "乙公司"),
            ("000003", "丙公司"),
        ),
    )
    execution = TaskExecutionResult(
        task=resolved,
        status="completed",
        derived_results=[
            {
                "processor": "company_theme_evidence_analysis",
                "result": {
                    "success": True,
                    "partial": False,
                    "candidate_scope": "candidate_collection",
                    "company_results": [
                        {"symbol": "000001", "verdict": "pass"},
                        {"symbol": "000002", "verdict": "fail"},
                        {"symbol": "000003", "verdict": "insufficient"},
                    ],
                },
            }
        ],
        output_entities=(SecurityEntity(symbol="000001", name="甲公司"),),
    )

    outcome = task_outcome_v2(execution)

    assert outcome.coverage.requested == 3
    assert outcome.coverage.covered == 3
    assert outcome.coverage.missing == ()
    assert outcome.coverage.complete is True
