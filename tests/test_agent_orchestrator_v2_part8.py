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



"""Focused test slice 8; shared fixtures remain local to this slice."""

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
def test_direct_entity_request_does_not_bind_ambiguous_historical_sets() -> None:
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
                            "objective": "重新判断贵州茅台现在是否可以买入",
                            "input_refs": [],
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
                    "thesis": "重新判断贵州茅台现在是否可以买入",
                    "output": None,
                },
            )
        raise AssertionError(function_name)

    semantic_context = {
        "turns": [
            {
                "terminal_artifacts": [
                    {
                        "artifact_id": "old-set-1",
                        "resource_type": "security_collection",
                        "producer_node_id": "filter-1",
                    }
                ],
            },
            {
                "terminal_artifacts": [
                    {
                        "artifact_id": "old-set-2",
                        "resource_type": "security_collection",
                        "producer_node_id": "filter-2",
                    }
                ],
            },
        ],
    }
    graph = asyncio.run(
        plan_intent_graph_v2(
            [{"role": "user", "content": "贵州茅台现在能买入吗？"}],
            {"model": "test-model"},
            completion=completion,
            semantic_context=semantic_context,
            current_entities=[{"symbol": "600519", "name": "贵州茅台"}],
            today=date(2026, 7, 29),
        )
    )

    assert graph.outline.nodes[0].input_refs == ()

def test_runtime_rejects_unsupported_result_selection_as_v2_error() -> None:
    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            return _response(
                function_name,
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
                    "needs_clarification": False,
                    "clarification_question": None,
                },
            )
        return _response(
            function_name,
            {
                "query": "贵州茅台",
            },
        )

    valid = asyncio.run(
        plan_intent_graph_v2(
            [{"role": "user", "content": "查找贵州茅台"}],
            {"model": "test-model"},
            completion=completion,
        )
    )
    invalid_outline = IntentOutlineV2.model_validate(
        _with_test_goal(
            {
                **valid.outline.model_dump(mode="json"),
                "nodes": [
                    {
                        **valid.outline.nodes[0].model_dump(mode="json"),
                        "result_selection": {
                            "mode": "all_relevant",
                            "max_items": None,
                        },
                    }
                ],
            }
        )
    )
    invalid = PlannedIntentGraphV2(
        run_id=valid.run_id,
        outline=invalid_outline,
        nodes=(
            valid.nodes[0].__class__(
                outline=invalid_outline.nodes[0],
                intent=valid.nodes[0].intent,
                execution_parameters=valid.nodes[0].execution_parameters,
                assumptions=valid.nodes[0].assumptions,
            ),
        ),
        trace=valid.trace,
    )

    with pytest.raises(OrchestratorV2Error) as captured:
        task_plan_from_v2(invalid)
    assert captured.value.code == AgentErrorCode.PLANNER_SCHEMA_INVALID
    assert captured.value.task_id == "lookup"

def test_confirmation_signal_cannot_change_the_reviewed_action_fingerprint() -> None:
    async def planned(confirmed: bool):
        async def completion(**kwargs: Any) -> dict[str, Any]:
            function_name = _function_name(kwargs)
            if function_name == "submit_intent_outline_v2":
                return _response(
                    function_name,
                    {
                        "nodes": [
                            {
                                "node_id": "delete_history",
                                "capability": "analysis_history",
                                "objective": "删除指定历史记录",
                                "input_refs": [],
                                "result_selection": None,
                            }
                        ],
                        "needs_clarification": False,
                        "clarification_question": None,
                    },
                )
            return _response(
                function_name,
                {
                    "action": "delete",
                    "record_ids": [7],
                    "user_confirmed": confirmed,
                },
            )

        return await plan_intent_graph_v2(
            [{"role": "user", "content": "确认删除第7条历史记录"}],
            {"model": "test-model"},
            completion=completion,
        )

    pending_task = task_plan_from_v2(asyncio.run(planned(False))).tasks[0]
    confirmed_task = task_plan_from_v2(asyncio.run(planned(True))).tasks[0]
    assert pending_task.confirmation == ConfirmationState.MISSING
    assert confirmed_task.confirmation == ConfirmationState.EXPLICIT
    assert action_fingerprint(ResolvedTask(candidate=pending_task)) == action_fingerprint(
        ResolvedTask(candidate=confirmed_task)
    )

def test_financial_schema_excludes_program_owned_execution_fields() -> None:
    schema_text = json.dumps(
        CollectionFinancialFilterIntent.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    for forbidden in (
        "period_basis",
        "threshold_unit",
        "batch_size",
        "tool_name",
        "timeout",
        "retry",
        "concurrency",
        "symbols_csv",
    ):
        assert forbidden not in schema_text

def test_unsupported_explicit_financial_basis_requires_clarification() -> None:
    with pytest.raises(OrchestratorV2Error) as captured:
        normalize_capability_intent(
            node_id="filter",
            objective="筛选",
            capability=Capability.COLLECTION_FINANCIAL_FILTER,
            intent={
                "predicates": [
                    {
                        "metric": "net_profit",
                        "operator": "lt",
                        "amount": {"value": 0, "unit": "cny"},
                        "period": {"kind": "ttm"},
                        "action": "exclude_matching",
                    }
                ],
            },
            input_refs=(),
            result_selection=None,
            current_year=2026,
        )
    assert captured.value.code == AgentErrorCode.CLARIFICATION_REQUIRED

def test_currency_units_and_predicate_order_canonicalize() -> None:
    def normalized(value: float, unit: str, reverse: bool = False):
        predicates = [
            {
                "metric": "debt_ratio",
                "operator": "gt",
                "percent": 70,
                "action": "exclude_matching",
            },
            {
                "metric": "revenue",
                "operator": "lt",
                "amount": {"value": value, "unit": unit},
                "period": {"kind": "previous_fiscal_year"},
                "action": "exclude_matching",
            },
        ]
        if reverse:
            predicates.reverse()
        return normalize_capability_intent(
            node_id="filter",
            objective="筛选",
            capability=Capability.COLLECTION_FINANCIAL_FILTER,
            intent={"predicates": predicates},
            input_refs=(),
            result_selection=None,
            current_year=2026,
        )

    values = [
        normalized(5, "yi_cny"),
        normalized(50_000, "wan_cny", reverse=True),
        normalized(500_000_000, "cny"),
    ]
    assert values[0].intent == values[1].intent == values[2].intent
    assert (
        dict(values[0].execution_parameters)
        == dict(values[1].execution_parameters)
        == dict(values[2].execution_parameters)
    )

def test_legacy_context_adapter_creates_terminal_artifact_without_markdown() -> None:
    legacy = {
        "version": "1",
        "turns": [
            {
                "request": "这些股票",
                "tasks": [],
                "entities": [{"symbol": "000001", "name": "平安银行"}],
            }
        ],
    }
    context, artifacts = migrate_legacy_context(
        legacy,
        conversation_id="conversation",
    )
    assert context.version == "3"
    assert artifacts[0].payload == {"securities": [{"symbol": "000001", "name": "平安银行"}]}
    serialized = context.model_dump_json()
    assert "parameters" not in serialized
    assert "result_context" not in serialized
    assert "markdown" not in serialized.lower()

    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        db.create_chat_conversation("conversation")
        assert db.save_agent_artifacts(artifacts) == 1
        loaded = db.get_agent_artifact(artifacts[0].artifact_id)
        assert loaded is not None
        assert loaded.fingerprint == artifacts[0].fingerprint
    finally:
        DatabaseManager.reset_instance()
