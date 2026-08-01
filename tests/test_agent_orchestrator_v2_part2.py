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



"""Focused test slice 2; shared fixtures remain local to this slice."""

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
def test_industry_result_count_defaults_to_program_owned_top_16() -> None:
    outline = IntentOutlineV2.model_validate(
        _with_test_goal(
            {
                "nodes": [
                    {
                        "node_id": "industry",
                        "capability": "industry_research",
                        "objective": "判断人形机器人哪些领域最受益",
                        "input_refs": [],
                        "result_selection": None,
                    }
                ],
                "needs_clarification": False,
                "clarification_question": None,
            }
        )
    )
    normalized = normalize_capability_intent(
        node_id="industry",
        objective=outline.nodes[0].objective,
        capability=Capability.INDUSTRY_RESEARCH,
        intent={"explicit_subjects": ["人形机器人"]},
        input_refs=(),
        result_selection=None,
        current_year=2026,
    )
    graph = PlannedIntentGraphV2(
        run_id="run-default-selection",
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
            run_id="run-default-selection",
            schema_version="test",
        ),
    )

    task = task_plan_from_v2(graph).tasks[0]

    assert task.result_selection is not None
    assert task.result_selection.mode.value == "top_k"
    assert task.result_selection.max_items == 16
    assert task.parameters["_assumptions"] == [
        {
            "field_path": "/result_selection",
            "value": {"mode": "top_k", "max_items": 16},
            "reason": "用户未指定返回数量，产业受益板块默认最多返回 16 个。",
            "source": "program_default",
        }
    ]
    assert normalized.assumptions[0].field_path == "/result_selection"

def test_explicit_industry_top_k_count_is_not_clamped_to_the_default() -> None:
    outline = IntentOutlineV2.model_validate(
        _with_test_goal(
            {
                "nodes": [
                    {
                        "node_id": "industry",
                        "capability": "industry_research",
                        "objective": "返回人形机器人受益最大的 40 个板块",
                        "input_refs": [],
                        "result_selection": {"mode": "top_k", "max_items": 40},
                    }
                ],
                "needs_clarification": False,
                "clarification_question": None,
            }
        )
    )
    normalized = normalize_capability_intent(
        node_id="industry",
        objective=outline.nodes[0].objective,
        capability=Capability.INDUSTRY_RESEARCH,
        intent={"explicit_subjects": ["人形机器人"]},
        input_refs=(),
        result_selection=outline.nodes[0].result_selection,
        current_year=2026,
    )
    graph = PlannedIntentGraphV2(
        run_id="run-explicit-selection",
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
            run_id="run-explicit-selection",
            schema_version="test",
        ),
    )

    task = task_plan_from_v2(graph).tasks[0]

    assert task.result_selection is not None
    assert task.result_selection.mode.value == "top_k"
    assert task.result_selection.max_items == 40
    assert normalized.assumptions == ()

def test_planner_does_not_install_a_total_deadline() -> None:
    calls: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        calls.append(function_name)
        await asyncio.sleep(0.01)
        if function_name == "submit_intent_outline_v2":
            return _response(
                function_name,
                {
                    "nodes": [
                        {
                            "node_id": "industry",
                            "capability": "industry_research",
                            "objective": "判断人形机器人最受益领域",
                            "input_refs": [],
                            "result_selection": {
                                "mode": "all_relevant",
                                "max_items": None,
                            },
                        }
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
            )
        if function_name == "submit_industry_research_intent_v2":
            return _response(
                function_name,
                {
                    "explicit_subjects": ["人形机器人"],
                },
            )
        raise AssertionError(function_name)

    with patch(
        "src.agent.orchestrator_v2.planner.asyncio.timeout",
        side_effect=AssertionError("planner must wait for the model"),
    ) as timeout_factory:
        graph = asyncio.run(
            plan_intent_graph_v2(
                [{"role": "user", "content": "人形机器人哪些领域最受益"}],
                {"model": "test-model"},
                completion=completion,
                today=date(2026, 7, 28),
            )
        )

    timeout_factory.assert_not_called()
    assert graph.nodes[0].outline.capability == Capability.INDUSTRY_RESEARCH
    assert calls == [
        "submit_intent_outline_v2",
        "submit_industry_research_intent_v2",
    ]

def test_outline_prompt_requires_terminal_company_judgment_after_discovery() -> None:
    observed_system_prompt = ""

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal observed_system_prompt
        function_name = _function_name(kwargs)
        assert function_name == "submit_intent_outline_v2"
        observed_system_prompt = kwargs["messages"][0]["content"]
        return _response(
            function_name,
            {
                "nodes": [
                    {
                        "node_id": "answer",
                        "capability": "general_response",
                        "objective": "回答问候",
                        "input_refs": [],
                        "result_selection": None,
                    }
                ],
                "needs_clarification": False,
                "clarification_question": None,
            },
        )

    asyncio.run(
        plan_intent_graph_v2(
            [{"role": "user", "content": "你好"}],
            {"model": "test-model"},
            completion=completion,
        )
    )

    assert "不能只完成前置候选发现" in observed_system_prompt
    assert "候选成员关系不等于公司业务匹配" in observed_system_prompt
    assert "domain_collection 和 security_collection" in observed_system_prompt

def test_planner_emits_heartbeats_without_stopping_the_model() -> None:
    summaries: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        await asyncio.sleep(0.03)
        if function_name == "submit_intent_outline_v2":
            return _response(
                function_name,
                {
                    "nodes": [
                        {
                            "node_id": "industry",
                            "capability": "industry_research",
                            "objective": "判断人形机器人最受益领域",
                            "input_refs": [],
                            "result_selection": {
                                "mode": "all_relevant",
                                "max_items": None,
                            },
                        }
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
            )
        return _response(
            function_name,
            {
                "explicit_subjects": ["人形机器人"],
            },
        )

    def observe(event: Any) -> None:
        summaries.append(event.summary)

    with patch(
        "src.agent.orchestrator_v2.planner.MODEL_PROGRESS_HEARTBEAT_SECONDS",
        0.005,
    ):
        graph = asyncio.run(
            plan_intent_graph_v2(
                [{"role": "user", "content": "人形机器人哪些领域最受益"}],
                {"model": "test-model"},
                completion=completion,
                stage_observer=observe,
                today=date(2026, 7, 28),
            )
        )

    assert graph.nodes[0].outline.capability == Capability.INDUSTRY_RESEARCH
    assert any("模型正在识别能力与资源关系，已持续分析" in item for item in summaries)
    assert any("模型正在填写“产业研究”业务 Schema，已持续分析" in item for item in summaries)

def test_provider_fallback_accepts_valid_fenced_json_after_malformed_tool_args() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_investment_decision_intent_v2",
                                "arguments": ('{"thesis":"减速器",' '"mainline_strategy":confirmed_mainline}'),
                            },
                        }
                    ],
                    "content": None,
                    "reasoning_content": (
                        "已按同一份精确 Schema 修正：\n"
                        "```json\n"
                        '{"thesis":"减速器",'
                        '"mainline_strategy":"confirmed_mainline"}\n'
                        "```"
                    ),
                },
            }
        ],
    }

    assert _payload_from_response(
        response,
        "submit_investment_decision_intent_v2",
    ) == {
        "thesis": "减速器",
        "mainline_strategy": "confirmed_mainline",
    }


def test_provider_payload_parser_recovers_repairable_json_syntax() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_investment_decision_intent_v2",
                                "arguments": (
                                    '{"thesis":"减速器" '
                                    '"mainline_strategy":"confirmed_mainline"}'
                                ),
                            },
                        }
                    ],
                    "content": None,
                },
            }
        ],
    }

    assert _payload_from_response(
        response,
        "submit_investment_decision_intent_v2",
    ) == {
        "thesis": "减速器",
        "mainline_strategy": "confirmed_mainline",
    }
