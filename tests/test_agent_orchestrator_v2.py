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
    Capability,
    CoverageV2,
    InputReferenceV2,
    IntentOutlineV2,
    OrchestratorV2Error,
    PlanningTraceV2,
    ResourceType,
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
    migration_coverage,
    normalize_capability_intent,
)
from src.agent.orchestrator_v2.runtime import (
    _project_artifact_domain_subset,
    compile_intent_graph_v2,
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
    compile_task,
    workflow_for,
)
from src.storage.manager import DatabaseManager
from src.tools.registry import ToolRegistry


def _response(function_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "function": {
                        "name": function_name,
                        "arguments": json.dumps(payload, ensure_ascii=False),
                    },
                }],
            },
        }],
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
            "securities": [
                {"symbol": symbol, "name": symbol}
                for symbol in symbols
            ],
        },
    )


def test_unified_registry_covers_every_standard_capability_once() -> None:
    assert {item.value for item in Capability} == {
        item.value for item in StandardTaskKind
    }
    assert set(CAPABILITY_REGISTRY) == set(Capability)
    assert migration_coverage()["complete"] is True
    assert migration_coverage()["migrated"] == 45
    with pytest.raises(TypeError):
        CAPABILITY_REGISTRY[Capability.GENERAL_RESPONSE] = None  # type: ignore[index]

    for spec in CAPABILITY_REGISTRY.values():
        assert spec.intent_model.model_config.get("extra") == "forbid"
        expected_version = (
            "3.2.0"
            if spec.capability == Capability.INVESTMENT_DECISION
            else "3.0.0"
        )
        assert spec.schema_version.startswith(f"{expected_version}:")
        assert spec.result_model.__name__ == "TaskOutcomeV2"
        assert callable(spec.compiler)
        assert callable(spec.projector)
        workflow = workflow_for(StandardTaskKind(spec.capability.value))
        assert (
            spec.supports_result_selection
            is workflow.supports_result_selection
        )

    catalog = {
        item["capability"]: item
        for item in capability_catalog()
    }
    assert catalog["industry_research"]["supports_result_selection"] is True
    assert catalog["theme_stock_discovery"]["supports_result_selection"] is False
    for spec in CAPABILITY_REGISTRY.values():
        policy_fields = type(spec.execution_policy).model_fields
        assert "timeout_seconds" not in policy_fields
        assert "max_attempts" not in policy_fields
        assert "retryable_error_codes" not in policy_fields


def test_every_capability_accepts_and_normalizes_one_exact_typed_intent() -> None:
    values: dict[Capability, dict[str, Any]] = {
        capability: {} for capability in Capability
    }
    values.update({
        Capability.SECURITY_LOOKUP: {"query": "贵州茅台"},
        Capability.MACRO_ANALYSIS: {"indicators": ["PMI"]},
        Capability.INDUSTRY_RESEARCH: {
            "explicit_subjects": ["人形机器人"],
        },
        Capability.THEME_STOCK_DISCOVERY: {
            "selection_mode": "named_subset",
            "themes": ["机器人执行器"],
        },
        Capability.STOCK_SCREENING: {"screen_spec": {
            "version": "1.0",
            "universe": {
                "status": "active",
                "markets": ["sh"],
                "include_st": False,
                "min_listing_trading_days": 60,
                "price_adjustment": "qfq",
            },
            "technical_rule": {
                "strategy": "atr_relative_frequency",
                "atr_period": 14,
                "atr_average": "wilder",
                "baseline_period": 20,
                "baseline_average": "sma",
                "threshold_operator": "multiply",
                "threshold_value": 1.2,
                "daily_comparison": "gt",
                "lookback_days": 20,
                "min_qualified_days": 5,
                "min_qualified_ratio_pct": None,
            },
            "financial_filters": [],
            "sort": {"field": "code", "order": "asc"},
            "output_fields": ["current_atr_pct"],
            "preview_limit": 10,
        }},
        Capability.COLLECTION_FINANCIAL_FILTER: {
            "predicates": [{
                "metric": "debt_ratio",
                "operator": "gt",
                "percent": 70,
                "action": "exclude_matching",
            }],
        },
        Capability.WATCHLIST_MUTATION: {"action": "add"},
        Capability.WATCHLIST_GROUP_MANAGEMENT: {"action": "list"},
        Capability.FORMAL_ANALYSIS: {"action": "status"},
        Capability.ANALYSIS_HISTORY: {"action": "search"},
        Capability.ANALYSIS_TEMPLATE_MANAGEMENT: {"action": "list"},
        Capability.BATCH_ANALYSIS: {
            "scope": "symbols",
            "analysis_mode": "buy_criteria",
        },
        Capability.BATCH_RUN_MANAGEMENT: {"action": "list"},
        Capability.ANALYSIS_SCHEDULE_MANAGEMENT: {"action": "get"},
        Capability.NOTIFICATION: {"action": "status"},
        Capability.FINANCIAL_FEED_READ: {"route_path": "/finance/example"},
        Capability.FINANCIAL_ARTICLE_READ: {
            "route_path": "/finance/example",
            "title": "示例",
        },
        Capability.WEBPAGE_FEED_TRANSFORM: {"url": "https://example.com"},
        Capability.FINANCIAL_FEED_EXPORT: {"route_path": "/finance/example"},
        Capability.PUBLIC_WEB_RESEARCH: {"query": "市场研究"},
    })

    for capability, payload in values.items():
        normalized = normalize_capability_intent(
            node_id="node",
            objective="验证强类型能力",
            capability=capability,
            intent=payload,
            input_refs=(),
            result_selection=None,
            current_year=2026,
        )
        assert isinstance(
            normalized.intent,
            CAPABILITY_REGISTRY[capability].intent_model,
        )
        with pytest.raises(ValueError):
            CAPABILITY_REGISTRY[capability].intent_model.model_validate({
                **payload,
                "_program_owned_field": "forbidden",
            })


def test_no_capability_exposes_one_open_parameters_object() -> None:
    for capability, spec in CAPABILITY_REGISTRY.items():
        schema = spec.intent_model.model_json_schema()
        assert schema.get("additionalProperties") is False, capability
        assert "parameters" not in (schema.get("properties") or {}), capability

    outline_schema = json.dumps(
        __import__(
            "src.agent.orchestrator_v2.contracts",
            fromlist=["IntentOutlineV2"],
        ).IntentOutlineV2.model_json_schema(),
        sort_keys=True,
    )
    for forbidden in (
        "parameters",
        "tool_name",
        "batch_size",
        "timeout_seconds",
        "max_attempts",
        "concurrency",
        "cache_policy",
    ):
        assert forbidden not in outline_schema


def test_industry_result_count_defaults_to_program_owned_top_16() -> None:
    outline = IntentOutlineV2.model_validate({
        "nodes": [{
            "node_id": "industry",
            "capability": "industry_research",
            "objective": "判断人形机器人哪些领域最受益",
            "input_refs": [],
            "result_selection": None,
        }],
        "needs_clarification": False,
        "clarification_question": None,
    })
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
        nodes=(PlannedIntentNodeV2(
            outline=outline.nodes[0],
            intent=normalized.intent,
            execution_parameters=normalized.execution_parameters,
            assumptions=normalized.assumptions,
        ),),
        trace=PlanningTraceV2(
            run_id="run-default-selection",
            schema_version="test",
        ),
    )

    task = task_plan_from_v2(graph).tasks[0]

    assert task.result_selection is not None
    assert task.result_selection.mode.value == "top_k"
    assert task.result_selection.max_items == 16
    assert task.parameters["_assumptions"] == [{
        "field_path": "/result_selection",
        "value": {"mode": "top_k", "max_items": 16},
        "reason": "用户未指定返回数量，产业受益板块默认最多返回 16 个。",
        "source": "program_default",
    }]
    assert normalized.assumptions[0].field_path == "/result_selection"


def test_explicit_industry_top_k_count_is_not_clamped_to_the_default() -> None:
    outline = IntentOutlineV2.model_validate({
        "nodes": [{
            "node_id": "industry",
            "capability": "industry_research",
            "objective": "返回人形机器人受益最大的 40 个板块",
            "input_refs": [],
            "result_selection": {"mode": "top_k", "max_items": 40},
        }],
        "needs_clarification": False,
        "clarification_question": None,
    })
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
        nodes=(PlannedIntentNodeV2(
            outline=outline.nodes[0],
            intent=normalized.intent,
            execution_parameters=normalized.execution_parameters,
            assumptions=normalized.assumptions,
        ),),
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


def test_planner_does_not_install_local_timeouts() -> None:
    calls: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        calls.append(function_name)
        await asyncio.sleep(0.01)
        if function_name == "submit_intent_outline_v2":
            return _response(function_name, {
                "nodes": [{
                    "node_id": "industry",
                    "capability": "industry_research",
                    "objective": "判断人形机器人最受益领域",
                    "input_refs": [],
                    "result_selection": {
                        "mode": "all_relevant",
                        "max_items": None,
                    },
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        if function_name == "submit_industry_research_intent_v2":
            return _response(function_name, {
                "explicit_subjects": ["人形机器人"],
            })
        raise AssertionError(function_name)

    with patch(
        "src.agent.orchestrator_v2.planner.asyncio.timeout",
        side_effect=AssertionError("planner must not install a local timeout"),
    ):
        graph = asyncio.run(plan_intent_graph_v2(
            [{"role": "user", "content": "人形机器人哪些领域最受益"}],
            {"model": "test-model"},
            completion=completion,
            today=date(2026, 7, 28),
        ))

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
        return _response(function_name, {
            "nodes": [{
                "node_id": "answer",
                "capability": "general_response",
                "objective": "回答问候",
                "input_refs": [],
                "result_selection": None,
            }],
            "needs_clarification": False,
            "clarification_question": None,
        })

    asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "你好"}],
        {"model": "test-model"},
        completion=completion,
    ))

    assert "不能只完成前置候选发现" in observed_system_prompt
    assert (
        "候选成员关系不等于公司业务匹配" in observed_system_prompt
    )
    assert (
        "domain_collection 和 security_collection"
        in observed_system_prompt
    )


def test_planner_emits_heartbeats_without_stopping_the_model() -> None:
    summaries: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        await asyncio.sleep(0.03)
        if function_name == "submit_intent_outline_v2":
            return _response(function_name, {
                "nodes": [{
                    "node_id": "industry",
                    "capability": "industry_research",
                    "objective": "判断人形机器人最受益领域",
                    "input_refs": [],
                    "result_selection": {
                        "mode": "all_relevant",
                        "max_items": None,
                    },
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        return _response(function_name, {
            "explicit_subjects": ["人形机器人"],
        })

    def observe(event: Any) -> None:
        summaries.append(event.summary)

    with patch(
        "src.agent.orchestrator_v2.planner.MODEL_PROGRESS_HEARTBEAT_SECONDS",
        0.005,
    ):
        graph = asyncio.run(plan_intent_graph_v2(
            [{"role": "user", "content": "人形机器人哪些领域最受益"}],
            {"model": "test-model"},
            completion=completion,
            stage_observer=observe,
            today=date(2026, 7, 28),
        ))

    assert graph.nodes[0].outline.capability == Capability.INDUSTRY_RESEARCH
    assert any(
        "模型正在识别能力与资源关系，已持续分析" in item
        for item in summaries
    )
    assert any(
        "模型正在填写“产业研究”业务 Schema，已持续分析" in item
        for item in summaries
    )


def test_provider_fallback_accepts_valid_fenced_json_after_malformed_tool_args() -> None:
    response = {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "function": {
                        "name": "submit_investment_decision_intent_v2",
                        "arguments": (
                            '{"thesis":"减速器",'
                            '"mainline_strategy":confirmed_mainline}'
                        ),
                    },
                }],
                "content": None,
                "reasoning_content": (
                    "已按同一份精确 Schema 修正：\n"
                    "```json\n"
                    '{"thesis":"减速器",'
                    '"mainline_strategy":"confirmed_mainline"}\n'
                    "```"
                ),
            },
        }],
    }

    assert _payload_from_response(
        response,
        "submit_investment_decision_intent_v2",
    ) == {
        "thesis": "减速器",
        "mainline_strategy": "confirmed_mainline",
    }


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
        assert {"success", "partial", "errors", "warnings"} <= set(
            result_properties
        )
    feed_tool = registry.get_tool("read_financial_feed")
    assert feed_tool is not None
    validated = feed_tool.args_model.model_validate({
        "route_path": "/finance/example/:symbol",
        "params": {"symbol": "600519", "nested": {"page": 1}},
    })
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
            return _response(function_name, {
                "nodes": [{
                    "node_id": "filter",
                    "capability": "collection_financial_filter",
                    "objective": "联合剔除不满足三项财务条件的股票",
                    "input_refs": [{
                        "source": "artifact",
                        "node_id": None,
                        "artifact_id": artifact.artifact_id,
                        "resource_type": "security_collection",
                    }],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        if function_name == "submit_collection_financial_filter_intent_v2":
            return _response(function_name, _filter_payload())
        raise AssertionError(function_name)

    semantic_context = {
        "version": "3",
        "turns": [{
            "terminal_artifacts": [{
                "artifact_id": artifact.artifact_id,
                "resource_type": "security_collection",
            }],
        }],
    }
    graph = asyncio.run(plan_intent_graph_v2(
        [{
            "role": "user",
            "content": (
                "剔除其中归母净利润为负，去年营收低于5亿，"
                "负债率高于70%的股票"
            ),
        }],
        {"model": "test-model"},
        completion=completion,
        semantic_context=semantic_context,
        today=date(2026, 7, 28),
    ))

    normalized = CollectionFinancialFilterSpec.model_validate(
        graph.nodes[0].execution_parameters
    )
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

    compiled = asyncio.run(compile_intent_graph_v2(
        graph,
        {"model": "test-model"},
        completion=unused_completion,
        artifacts={artifact.artifact_id: artifact},
        registry=ToolRegistry(),
    ))
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
            "domains": [{
                "label": "减速器",
                "board_code": "BK1100",
                "board_queries": ["减速器"],
            }],
        },
    )
    security_artifact = _artifact(("001306", "002434")).model_copy(
        update={"lineage": (domain_artifact.artifact_id,)}
    )
    intent = CollectionFinancialFilterIntent.model_validate(
        _filter_payload()
    )
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
    outline = IntentOutlineV2.model_validate({
        "nodes": [{
            "node_id": "financial_filter",
            "capability": "collection_financial_filter",
            "objective": "按三项财务条件剔除股票",
            "input_refs": [{
                "source": "artifact",
                "artifact_id": security_artifact.artifact_id,
                "resource_type": "security_collection",
            }],
        }],
    })
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
    assert len(compile_task(ResolvedTask(
        candidate=task,
        symbols=tuple(task.entities),
    ))) == 3


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
            "domains": [{
                "label": "机器人执行器",
                "board_code": "BK1234",
                "board_queries": ["机器人执行器"],
                "mapping_type": "catalog_binding",
                "rationale": "机器人执行器对应执行器环节",
                "unresolved_parts": [],
                "tier": 1,
            }, {
                "label": "传感器",
                "board_code": "BK5678",
                "board_queries": ["传感器"],
                "mapping_type": "catalog_binding",
                "rationale": "传感器对应感知环节",
                "unresolved_parts": [],
                "tier": 2,
            }, {
                "label": "人工智能",
                "board_code": "BK9012",
                "board_queries": ["人工智能"],
                "mapping_type": "catalog_binding",
                "rationale": "人工智能对应决策环节",
                "unresolved_parts": [],
                "tier": 3,
            }],
            "domain_collection_v2": {
                "type": "domain_collection_v2",
                "schema_version": "domain-collection-v2.0",
                "catalog_snapshot_id": "catalog_snapshot",
                "requested_topic": "人形机器人哪些领域最受益",
                "benefit_outline": {
                    "topic": "人形机器人",
                    "roles": [{
                        "role_id": "actuator",
                        "label": "机器人执行器",
                        "benefit_mechanism": "执行机构承接运动控制价值量",
                        "tier": 1,
                    }],
                    "selection_objective": "选择受益最直接的实时板块",
                },
                "boards": [{
                    "board_id": "BK1234",
                    "board_name": "机器人执行器",
                    "role_id": "actuator",
                    "role_label": "机器人执行器",
                    "tier": 1,
                    "rationale": "机器人执行器对应执行器环节",
                }],
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
            return _response(function_name, {
                "nodes": [{
                    "node_id": "discover",
                    "capability": "theme_stock_discovery",
                    "objective": "从这些领域发现候选",
                    "input_refs": [{
                        "source": "artifact",
                        "node_id": None,
                        "artifact_id": artifact.artifact_id,
                        "resource_type": "domain_collection",
                    }],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        if function_name == "submit_theme_stock_discovery_intent_v2":
            return _response(function_name, {
                "selection_mode": "named_subset",
                "themes": ["机器人执行器（BK1234）"],
                "output": None,
            })
        raise AssertionError(function_name)

    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "从这些领域继续找股票"}],
        {"model": "test-model"},
        completion=completion,
        semantic_context={
            "version": "3",
            "turns": [{
                "terminal_artifacts": [{
                    "artifact_id": artifact.artifact_id,
                    "resource_type": "domain_collection",
                }],
            }],
        },
    ))
    task = task_plan_from_v2(
        graph,
        artifacts={artifact.artifact_id: artifact},
    ).tasks[0]
    assert len(task.parameters["domains"]) == 1
    assert task.parameters["domains"][0]["label"] == "机器人执行器"
    assert task.parameters["domains"][0]["board_queries"] == ["机器人执行器"]
    assert {
        item["label"] for item in task.parameters["domains"]
    }.isdisjoint({"传感器", "人工智能"})
    calls = compile_task(ResolvedTask(candidate=task))
    assert len(calls) == 1
    assert [
        item["label"] for item in calls[0].arguments["domains"]
    ] == ["机器人执行器"]
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
            "domains": [{
                "label": "减速器",
                "board_queries": ["减速器"],
                "mapping_type": "catalog_binding",
                "rationale": "减速器对应谐波减速器环节",
                "unresolved_parts": [],
            }],
            "domain_collection_v2": {
                "type": "domain_collection_v2",
                "schema_version": "2.0",
                "catalog_snapshot_id": "catalog_snapshot",
                "requested_topic": "人形机器人哪些领域最受益",
                "benefit_outline": {
                    "topic": "人形机器人",
                    "roles": [{
                        "role_id": "harmonic_reducer",
                        "label": "谐波减速器",
                        "benefit_mechanism": "关节减速传动核心部件",
                        "tier": 1,
                    }],
                    "selection_objective": "选择受益最直接的实时板块",
                },
                "boards": [{
                    "board_id": "BK1100",
                    "board_name": "减速器",
                    "role_id": "harmonic_reducer",
                    "role_label": "谐波减速器",
                    "tier": 1,
                    "rationale": "减速器对应谐波减速器环节",
                }],
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
            return _response(function_name, {
                "nodes": [{
                    "node_id": "discover",
                    "capability": "theme_stock_discovery",
                    "objective": "寻找谐波减速器相关公司",
                    "input_refs": [{
                        "source": "artifact",
                        "artifact_id": artifact.artifact_id,
                        "resource_type": "domain_collection",
                    }],
                }],
            })
        if function_name == "submit_theme_stock_discovery_intent_v2":
            return _response(function_name, {
                "selection_mode": "named_subset",
                "themes": ["减速器", "谐波减速器"],
            })
        raise AssertionError(function_name)

    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "找减速器中的谐波减速器公司"}],
        {"model": "test-model"},
        completion=completion,
        semantic_context={
            "version": "3",
            "turns": [{
                "terminal_artifacts": [{
                    "artifact_id": artifact.artifact_id,
                    "resource_type": "domain_collection",
                }],
            }],
        },
    ))
    task = task_plan_from_v2(
        graph,
        artifacts={artifact.artifact_id: artifact},
    ).tasks[0]

    assert task.parameters["domains"] == [{
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
    }]
    calls = compile_task(ResolvedTask(candidate=task))
    assert calls[0].arguments["domains"][0]["board_id"] == "BK1100"
    assert calls[0].arguments["domains"][0]["role_id"] == "harmonic_reducer"


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
            "domains": [{
                "label": "减速器",
                "board_code": "BK1100",
                "board_queries": ["减速器"],
                "mapping_type": "catalog_binding",
                "rationale": (
                    "减速器对应谐波减速器环节；"
                    "人形机器人旋转关节核心减速部件"
                ),
                "unresolved_parts": [],
                "tier": 1,
            }],
        },
    )
    calls: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        calls.append(function_name)
        if function_name == "submit_intent_outline_v2":
            return _response(function_name, {
                "nodes": [{
                    "node_id": "harmonic_reducer_industry",
                    "capability": "industry_research",
                    "objective": "找出人形机器人谐波减速器环节中大力发展的公司",
                    "input_refs": [{
                        "source": "artifact",
                        "artifact_id": artifact.artifact_id,
                        "resource_type": "domain_collection",
                    }],
                    "result_selection": None,
                }, {
                    "node_id": "find_stocks",
                    "capability": "theme_stock_discovery",
                    "objective": "从谐波减速器产业板块中找出相关大力发展的公司",
                    "input_refs": [{
                        "source": "node",
                        "node_id": "harmonic_reducer_industry",
                        "resource_type": "domain_collection",
                    }],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        if function_name == "submit_theme_stock_discovery_intent_v2":
            return _response(function_name, {
                "selection_mode": "named_subset",
                "themes": [
                    "减速器（BK1100）：减速器对应“谐波减速器”环节；"
                    "人形机器人旋转关节核心减速部件"
                ],
                "output": None,
            })
        raise AssertionError(
            f"identity bridge must not parameterize {function_name}"
        )

    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "找上面第一个减速器中的相关公司"}],
        {"model": "test-model"},
        completion=completion,
        semantic_context={
            "version": "3",
            "turns": [{
                "terminal_artifacts": [{
                    "artifact_id": artifact.artifact_id,
                    "resource_type": "domain_collection",
                    "producer_node_id": artifact.producer_node_id,
                }],
            }],
        },
    ))

    assert calls == [
        "submit_intent_outline_v2",
        "submit_theme_stock_discovery_intent_v2",
    ]
    assert len(graph.nodes) == 1
    node = graph.nodes[0]
    assert node.outline.node_id == "find_stocks"
    assert [
        ref.model_dump(mode="json")
        for ref in node.outline.input_refs
    ] == [{
        "source": "artifact",
        "node_id": None,
        "artifact_id": artifact.artifact_id,
        "resource_type": "domain_collection",
    }]

    async def unused_completion(**_: Any) -> Any:
        raise AssertionError(
            "a validated DomainCollection artifact must not be rebound by a model"
        )

    compiled = asyncio.run(compile_intent_graph_v2(
        graph,
        {"model": "test-model"},
        completion=unused_completion,
        artifacts={artifact.artifact_id: artifact},
        registry=ToolRegistry(),
    ))
    assert len(compiled.resolved_tasks) == 1
    assert compiled.resolved_tasks[0].candidate.depends_on == []
    assert compiled.resolved_tasks[0].candidate.parameters["domains"] == [{
        "label": "减速器",
        "board_queries": ["减速器"],
        "mapping_type": "catalog_binding",
        "rationale": (
            "减速器对应谐波减速器环节；"
            "人形机器人旋转关节核心减速部件"
        ),
        "unresolved_parts": [],
    }]


def test_named_domain_projection_never_widens_an_unmatched_subset() -> None:
    domains = [{
        "label": "机器人执行器",
        "board_code": "BK1145",
        "board_queries": ["机器人执行器"],
    }, {
        "label": "传感器",
        "board_code": "BK1000",
        "board_queries": ["传感器"],
    }]

    with pytest.raises(OrchestratorV2Error) as exc:
        _project_artifact_domain_subset(
            domains,
            [{"label": "不存在的板块"}],
            selection_mode="named_subset",
            task_id="discover",
        )

    assert exc.value.code == AgentErrorCode.RESOURCE_UNAVAILABLE
    assert "没有扩大为整个上游集合" in str(exc.value)
    assert _project_artifact_domain_subset(
        domains,
        [],
        selection_mode="all_bound",
        task_id="discover",
    ) == domains


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
            "domains": [{
                "label": "减速器",
                "board_queries": ["减速器"],
                "mapping_type": "catalog_binding",
                "rationale": "人形机器人关节传动环节",
                "unresolved_parts": [],
                "tier": 1,
            }],
        },
    )
    security_artifact = _artifact(("000001", "000002")).model_copy(
        update={"lineage": (domain_artifact.artifact_id,)}
    )

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            return _response(function_name, {
                "nodes": [{
                    "node_id": "decision",
                    "capability": "investment_decision",
                    "objective": "通过的这些股票哪些现在能买",
                    "input_refs": [{
                        "source": "artifact",
                        "node_id": None,
                        "artifact_id": security_artifact.artifact_id,
                        "resource_type": "security_collection",
                    }],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        if function_name == "submit_investment_decision_intent_v2":
            return _response(function_name, {
                "thesis": None,
                "output": None,
            })
        raise AssertionError(function_name)

    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "通过的这些股票哪些现在能买"}],
        {"model": "test-model"},
        completion=completion,
        semantic_context={
            "version": "3",
            "turns": [{
                "terminal_artifacts": [{
                    "artifact_id": security_artifact.artifact_id,
                    "resource_type": "security_collection",
                    "fingerprint": security_artifact.fingerprint,
                    "producer_node_id": security_artifact.producer_node_id,
                }],
            }],
        },
    ))
    task = task_plan_from_v2(
        graph,
        artifacts={
            security_artifact.artifact_id: security_artifact,
            domain_artifact.artifact_id: domain_artifact,
        },
    ).tasks[0]

    assert task.parameters["thesis"] == "减速器"
    assert task.parameters["thesis_context"]["summary"] == "减速器"
    assert task.parameters["thesis_context"]["domains"][0][
        "board_queries"
    ] == ["减速器"]
    assert task.parameters["mainline_strategy"] == "confirmed_mainline"
    assert any(
        assumption.field_path == "/mainline_strategy"
        and assumption.value == "confirmed_mainline"
        for assumption in graph.nodes[0].assumptions
    )


def test_historical_producer_node_ref_is_bound_to_the_unique_matching_artifact() -> None:
    financial_artifact = _artifact(("000001", "000002")).model_copy(update={
        "artifact_id": "artifact_financial_filter",
        "producer_node_id": "collection_financial_filter",
        "fingerprint": "financial-filter-fingerprint",
    })
    later_decision_artifact = _artifact(("000001",)).model_copy(update={
        "artifact_id": "artifact_previous_decision",
        "producer_node_id": "investment_decision",
        "fingerprint": "previous-decision-fingerprint",
    })
    called_functions: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        called_functions.append(function_name)
        if function_name == "submit_intent_outline_v2":
            return _response(function_name, {
                "nodes": [{
                    "node_id": "investment_decision",
                    "capability": "investment_decision",
                    "objective": "对上一轮财务筛选通过的股票执行买入判断",
                    "input_refs": [{
                        "source": "node",
                        "node_id": "collection_financial_filter",
                        "artifact_id": None,
                        "resource_type": "security_collection",
                    }],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        if function_name == "submit_investment_decision_intent_v2":
            return _response(function_name, {
                "thesis": None,
                "output": None,
            })
        raise AssertionError(function_name)

    graph = asyncio.run(plan_intent_graph_v2(
        [{
            "role": "user",
            "content": "通过的这些股票，哪些现在就能买？",
        }],
        {"model": "test-model"},
        completion=completion,
        semantic_context={
            "version": "3",
            "turns": [
                {
                    "terminal_artifacts": [{
                        "artifact_id": financial_artifact.artifact_id,
                        "resource_type": "security_collection",
                        "producer_node_id": (
                            financial_artifact.producer_node_id
                        ),
                    }],
                },
                {
                    "terminal_artifacts": [
                        {
                            "artifact_id": financial_artifact.artifact_id,
                            "resource_type": "security_collection",
                            "producer_node_id": (
                                financial_artifact.producer_node_id
                            ),
                        },
                        {
                            "artifact_id": later_decision_artifact.artifact_id,
                            "resource_type": "security_collection",
                            "producer_node_id": (
                                later_decision_artifact.producer_node_id
                            ),
                        },
                    ],
                },
            ],
        },
    ))

    raw_ref = graph.trace.raw_outline["nodes"][0]["input_refs"][0]
    normalized_ref = graph.trace.normalized_outline[
        "nodes"
    ][0]["input_refs"][0]
    assert raw_ref["source"] == "node"
    assert raw_ref["node_id"] == "collection_financial_filter"
    assert normalized_ref == {
        "source": "artifact",
        "node_id": None,
        "artifact_id": financial_artifact.artifact_id,
        "resource_type": "security_collection",
    }
    assert graph.outline.nodes[0].input_refs[0].artifact_id == (
        financial_artifact.artifact_id
    )
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
        "nodes": [{
            "node_id": "investment_decision",
            "capability": "investment_decision",
            "objective": "判断这些股票哪些可以买入",
            "input_refs": [{
                "source": "node",
                "node_id": "collection_financial_filter",
                "artifact_id": None,
                "resource_type": "security_collection",
            }],
            "result_selection": None,
        }],
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
        asyncio.run(plan_intent_graph_v2(
            [{"role": "user", "content": "这些股票哪些可以买入"}],
            {"model": "test-model"},
            completion=completion,
            semantic_context={
                "version": "3",
                "turns": [{
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
                }],
            },
            stage_observer=events.append,
        ))

    assert captured.value.code == AgentErrorCode.CLARIFICATION_REQUIRED
    assert outline_calls == 1
    assert str(captured.value) == (
        "找到多个可用的 security_collection 历史结果，"
        "请说明要使用哪一轮或哪一次筛选结果。"
    )
    assert events[-1].summary == str(captured.value)
    assert "validation error" not in events[-1].summary


def test_unknown_historical_node_keeps_precise_issue_private() -> None:
    events: list[AgentStageEventV2] = []
    outline_calls = 0
    repair_context: dict[str, Any] = {}
    stale_payload = {
        "nodes": [{
            "node_id": "investment_decision",
            "capability": "investment_decision",
            "objective": "判断这些股票哪些可以买入",
            "input_refs": [{
                "source": "node",
                "node_id": "collection_financial_filter",
                "artifact_id": None,
                "resource_type": "security_collection",
            }],
            "result_selection": None,
        }],
        "needs_clarification": False,
        "clarification_question": None,
    }

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal outline_calls
        function_name = _function_name(kwargs)
        assert function_name == "submit_intent_outline_v2"
        outline_calls += 1
        if outline_calls == 2:
            repair_context.update(json.loads(
                kwargs["messages"][-1]["content"]
            ))
        return _response(function_name, stale_payload)

    with pytest.raises(OrchestratorV2Error) as captured:
        asyncio.run(plan_intent_graph_v2(
            [{"role": "user", "content": "这些股票哪些可以买入"}],
            {"model": "test-model"},
            completion=completion,
            semantic_context={"version": "3", "turns": []},
            stage_observer=events.append,
        ))

    assert captured.value.code == AgentErrorCode.PLANNER_SCHEMA_INVALID
    assert outline_calls == 2
    repair = repair_context["targeted_repair"]
    assert repair["issues"] == [{
        "pointer": "/nodes/0/input_refs/0",
        "code": "unknown_node_reference",
        "expected": (
            "a current-graph node_id or one uniquely resolvable "
            "historical artifact"
        ),
        "allowed": [],
        "message": (
            "'collection_financial_filter' is not a node in this graph "
            "and resolved to 0 compatible artifacts"
        ),
    }]
    assert events[-1].summary == (
        "任务图未通过内部强类型契约校验；"
        "本轮没有调用任何数据工具"
    )
    assert "validation error" not in events[-1].summary
    assert "collection_financial_filter" not in events[-1].summary


def test_targeted_repair_keeps_the_frozen_graph() -> None:
    attempts = 0
    repair_context: dict[str, Any] = {}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal attempts
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            return _response(function_name, {
                "nodes": [{
                    "node_id": "filter",
                    "capability": "collection_financial_filter",
                    "objective": "筛选",
                    "input_refs": [],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        attempts += 1
        if attempts == 1:
            invalid = _filter_payload()
            invalid["predicates"][0]["batch_size"] = 24
            return _response(function_name, invalid)
        repair_context.update(json.loads(kwargs["messages"][-1]["content"]))
        return _response(function_name, _filter_payload())

    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "筛选这些股票"}],
        {"model": "test-model"},
        completion=completion,
        semantic_context={},
        today=date(2026, 7, 28),
    ))
    assert attempts == 2
    assert graph.nodes[0].outline.node_id == "filter"
    repair = repair_context["targeted_repair"]
    assert repair["invalid_payload"]["predicates"][0]["batch_size"] == 24
    assert any(
        issue["pointer"].endswith("/batch_size")
        for issue in repair["issues"]
    )


def test_outline_repairs_result_selection_from_capability_contract() -> None:
    outline_attempts = 0
    repair_context: dict[str, Any] = {}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal outline_attempts
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            outline_attempts += 1
            if outline_attempts == 2:
                repair_context.update(
                    json.loads(kwargs["messages"][-1]["content"])
                )
            return _response(function_name, {
                "nodes": [{
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
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        if function_name == "submit_security_lookup_intent_v2":
            return _response(function_name, {
                "query": "贵州茅台",
            })
        raise AssertionError(function_name)

    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "查找贵州茅台"}],
        {"model": "test-model"},
        completion=completion,
        today=date(2026, 7, 28),
    ))

    assert outline_attempts == 2
    assert graph.outline.nodes[0].result_selection is None
    repair = repair_context["targeted_repair"]
    assert repair["invalid_payload"]["nodes"][0]["result_selection"] == {
        "mode": "all_relevant",
        "max_items": None,
    }
    assert repair["issues"] == [{
        "pointer": "/nodes/0/result_selection",
        "code": "capability_result_selection_forbidden",
        "expected": (
            "null because capability security_lookup does not support "
            "result selection"
        ),
        "allowed": [None],
        "message": "security_lookup does not support result_selection",
    }]


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
                repair_context.update(
                    json.loads(kwargs["messages"][-1]["content"])
                )
            decision_node = {
                "node_id": "decision",
                "capability": "investment_decision",
                "objective": "判断平安银行现在是否可以买入",
                "input_refs": [],
                "result_selection": None,
            }
            if outline_attempts == 1:
                return _response(function_name, {
                    "nodes": [{
                        **decision_node,
                        "input_refs": [{
                            "source": "node",
                            "node_id": None,
                            "resource_type": "security_collection",
                        }],
                    }],
                    "needs_clarification": False,
                    "clarification_question": None,
                })
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
                        "input_refs": [{
                            "source": "node",
                            "node_id": "lookup",
                            "artifact_id": None,
                            "resource_type": "security_collection",
                        }],
                    },
                ]
            return _response(function_name, {
                "nodes": nodes,
                "needs_clarification": False,
                "clarification_question": None,
            })
        parameterized_functions.append(function_name)
        if function_name == "submit_investment_decision_intent_v2":
            return _response(function_name, {
                "thesis": None,
                "output": None,
            })
        raise AssertionError(function_name)

    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "平安银行（000001）现在能买入吗？"}],
        {"model": "test-model"},
        completion=completion,
        today=date(2026, 7, 28),
    ))

    assert outline_attempts == 2
    assert [node.capability for node in graph.outline.nodes] == [
        Capability.INVESTMENT_DECISION
    ]
    assert parameterized_functions == [
        "submit_investment_decision_intent_v2"
    ]
    repair = repair_context["targeted_repair"]
    assert any(
        issue["pointer"] == "/nodes/0/input_refs/0"
        for issue in repair["issues"]
    )
    assert graph.outline.nodes[0].input_refs == ()


def test_outline_drops_type_impossible_resource_edge_before_binding() -> None:
    outline_attempts = 0

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal outline_attempts
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            outline_attempts += 1
            return _response(function_name, {
                "nodes": [{
                    "node_id": "decision",
                    "capability": "investment_decision",
                    "objective": "判断贵州茅台现在是否可以买入",
                    "input_refs": [{
                        "source": "artifact",
                        "artifact_id": "unrelated-evidence",
                        "node_id": None,
                        "resource_type": "evidence_collection",
                    }],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        if function_name == "submit_investment_decision_intent_v2":
            return _response(function_name, {
                "thesis": "判断贵州茅台现在是否可以买入",
                "output": None,
            })
        raise AssertionError(function_name)

    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "贵州茅台现在能买入吗？"}],
        {"model": "test-model"},
        completion=completion,
        today=date(2026, 7, 29),
    ))

    assert outline_attempts == 1
    assert graph.outline.nodes[0].capability == Capability.INVESTMENT_DECISION
    assert graph.outline.nodes[0].input_refs == ()
    assert graph.trace.raw_outline["nodes"][0]["input_refs"][0][
        "resource_type"
    ] == "evidence_collection"
    assert graph.trace.normalized_outline["nodes"][0]["input_refs"] == []


def test_direct_entity_request_does_not_bind_ambiguous_historical_sets() -> None:
    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            return _response(function_name, {
                "nodes": [{
                    "node_id": "decision",
                    "capability": "investment_decision",
                    "objective": "重新判断贵州茅台现在是否可以买入",
                    "input_refs": [],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        if function_name == "submit_investment_decision_intent_v2":
            return _response(function_name, {
                "thesis": "重新判断贵州茅台现在是否可以买入",
                "output": None,
            })
        raise AssertionError(function_name)

    semantic_context = {
        "turns": [
            {
                "terminal_artifacts": [{
                    "artifact_id": "old-set-1",
                    "resource_type": "security_collection",
                    "producer_node_id": "filter-1",
                }],
            },
            {
                "terminal_artifacts": [{
                    "artifact_id": "old-set-2",
                    "resource_type": "security_collection",
                    "producer_node_id": "filter-2",
                }],
            },
        ],
    }
    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "贵州茅台现在能买入吗？"}],
        {"model": "test-model"},
        completion=completion,
        semantic_context=semantic_context,
        current_entities=[{"symbol": "600519", "name": "贵州茅台"}],
        today=date(2026, 7, 29),
    ))

    assert graph.outline.nodes[0].input_refs == ()


def test_runtime_rejects_unsupported_result_selection_as_v2_error() -> None:
    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = _function_name(kwargs)
        if function_name == "submit_intent_outline_v2":
            return _response(function_name, {
                "nodes": [{
                    "node_id": "lookup",
                    "capability": "security_lookup",
                    "objective": "查找贵州茅台",
                    "input_refs": [],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            })
        return _response(function_name, {
            "query": "贵州茅台",
        })

    valid = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "查找贵州茅台"}],
        {"model": "test-model"},
        completion=completion,
    ))
    invalid_outline = IntentOutlineV2.model_validate({
        **valid.outline.model_dump(mode="json"),
        "nodes": [{
            **valid.outline.nodes[0].model_dump(mode="json"),
            "result_selection": {
                "mode": "all_relevant",
                "max_items": None,
            },
        }],
    })
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
                return _response(function_name, {
                    "nodes": [{
                        "node_id": "delete_history",
                        "capability": "analysis_history",
                        "objective": "删除指定历史记录",
                        "input_refs": [],
                        "result_selection": None,
                    }],
                    "needs_clarification": False,
                    "clarification_question": None,
                })
            return _response(function_name, {
                "action": "delete",
                "record_ids": [7],
                "user_confirmed": confirmed,
            })

        return await plan_intent_graph_v2(
            [{"role": "user", "content": "确认删除第7条历史记录"}],
            {"model": "test-model"},
            completion=completion,
        )

    pending_task = task_plan_from_v2(asyncio.run(planned(False))).tasks[0]
    confirmed_task = task_plan_from_v2(asyncio.run(planned(True))).tasks[0]
    assert pending_task.confirmation == ConfirmationState.MISSING
    assert confirmed_task.confirmation == ConfirmationState.EXPLICIT
    assert (
        action_fingerprint(ResolvedTask(candidate=pending_task))
        == action_fingerprint(ResolvedTask(candidate=confirmed_task))
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
                "predicates": [{
                    "metric": "net_profit",
                    "operator": "lt",
                    "amount": {"value": 0, "unit": "cny"},
                    "period": {"kind": "ttm"},
                    "action": "exclude_matching",
                }],
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
        "turns": [{
            "request": "这些股票",
            "tasks": [],
            "entities": [{"symbol": "000001", "name": "平安银行"}],
        }],
    }
    context, artifacts = migrate_legacy_context(
        legacy,
        conversation_id="conversation",
    )
    assert context.version == "3"
    assert artifacts[0].payload == {
        "securities": [{"symbol": "000001", "name": "平安银行"}]
    }
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


def test_legacy_ranked_domains_migrate_as_typed_resource_not_answer_text() -> None:
    context, artifacts = migrate_legacy_context(
        {
            "version": "1",
            "turns": [{
                "request": "人形机器人最受益领域",
                "entities": [],
                "tasks": [{
                    "task_id": "industry",
                    "kind": "industry_research",
                    "objective": "结构化领域排序",
                    "status": "completed",
                    "semantic_artifacts": [{
                        "type": "ranked_domains",
                        "topic": "人形机器人",
                        "groups": [{
                            "tier": 1,
                            "domains": [{
                                "label": "机器人执行器",
                                "board_queries": ["机器人执行器"],
                                "mapping_type": "catalog_binding",
                                "rationale": "执行器是核心环节",
                                "unresolved_parts": [],
                            }],
                        }],
                    }],
                }],
            }],
        },
        conversation_id="conversation",
    )
    domain_artifact = next(
        item
        for item in artifacts
        if item.resource_type == ResourceType.DOMAIN_COLLECTION
    )
    assert domain_artifact.payload["domains"][0]["label"] == "机器人执行器"
    assert context.turns[0].terminal_artifacts[0].artifact_id == (
        domain_artifact.artifact_id
    )
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
    assert {
        item.field_path for item in normalized.assumptions
    } == {
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
    assert confirmed.execution_parameters["mainline_strategy"] == (
        "confirmed_mainline"
    )
    assert any(
        item.field_path == "/mainline_strategy"
        for item in confirmed.assumptions
    )

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
    assert early.execution_parameters["mainline_strategy"] == (
        "early_positioning"
    )
    assert not any(
        item.field_path == "/mainline_strategy"
        for item in early.assumptions
    )


def test_planner_context_deduplicates_retries_and_excludes_current_request() -> None:
    context = ConversationContextV2(turns=(
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
    ))

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
        call.result_bindings == (
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
        derived_results=[{
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
        }],
        output_entities=(SecurityEntity(symbol="000001", name="甲公司"),),
    )

    outcome = task_outcome_v2(execution)

    assert outcome.coverage.requested == 3
    assert outcome.coverage.covered == 3
    assert outcome.coverage.missing == ()
    assert outcome.coverage.complete is True
