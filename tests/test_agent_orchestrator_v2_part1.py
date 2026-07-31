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



"""Focused test slice 1; shared fixtures remain local to this slice."""

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
def test_unified_registry_covers_every_standard_capability_once() -> None:
    assert {item.value for item in Capability} == {item.value for item in StandardTaskKind}
    assert set(CAPABILITY_REGISTRY) == set(Capability)
    assert migration_coverage()["complete"] is True
    assert migration_coverage()["migrated"] == 46
    with pytest.raises(TypeError):
        CAPABILITY_REGISTRY[Capability.GENERAL_RESPONSE] = None  # type: ignore[index]

    for spec in CAPABILITY_REGISTRY.values():
        assert spec.intent_model.model_config.get("extra") == "forbid"
        expected_version = "4.2.0" if spec.capability == Capability.INVESTMENT_DECISION else "4.0.0"
        assert spec.schema_version.startswith(f"{expected_version}:")
        assert spec.result_model.__name__ == "TaskOutcomeV2"
        assert callable(spec.compiler)
        assert callable(spec.projector)
        workflow = workflow_for(StandardTaskKind(spec.capability.value))
        assert spec.supports_result_selection is workflow.supports_result_selection

    catalog = {item["capability"]: item for item in capability_catalog()}
    assert catalog["industry_research"]["supports_result_selection"] is True
    assert catalog["theme_stock_discovery"]["supports_result_selection"] is False
    for spec in CAPABILITY_REGISTRY.values():
        policy_fields = type(spec.execution_policy).model_fields
        assert "timeout_seconds" not in policy_fields
        assert "max_attempts" in policy_fields
        assert "retryable_error_codes" in policy_fields
        assert spec.execution_policy.max_attempts >= 1

def test_financial_statement_periods_share_one_validated_contract() -> None:
    spec = capability_for(Capability.FINANCIAL_STATEMENT_ANALYSIS)
    with pytest.raises(ValueError):
        spec.intent_model.model_validate({"periods": 1})

    defaulted = normalize_capability_intent(
        node_id="financials",
        objective="查看新强联最新财报",
        capability=Capability.FINANCIAL_STATEMENT_ANALYSIS,
        intent={},
        input_refs=(),
        result_selection=None,
        current_year=2026,
    )
    assert defaulted.execution_parameters == {"periods": 4}
    assert defaulted.assumptions[0].field_path == "/periods"

    explicit = normalize_capability_intent(
        node_id="financials",
        objective="比较新强联最近两期财报",
        capability=Capability.FINANCIAL_STATEMENT_ANALYSIS,
        intent={"periods": 2},
        input_refs=(),
        result_selection=None,
        current_year=2026,
    )
    assert explicit.execution_parameters == {"periods": 2}

def test_capability_freshness_is_explicit_and_realtime_never_crosses_runs():
    assert set(CAPABILITY_REGISTRY) == set(Capability)
    realtime = CAPABILITY_REGISTRY[Capability.REALTIME_QUOTE].freshness_policy
    overview = CAPABILITY_REGISTRY[Capability.MARKET_OVERVIEW].freshness_policy
    fundamentals = CAPABILITY_REGISTRY[Capability.FUNDAMENTAL_ANALYSIS].freshness_policy
    assert realtime.reuse_scope == CacheReuseScope.RUN_ONLY
    assert realtime.max_age_seconds is None
    assert overview.reuse_scope == CacheReuseScope.CROSS_RUN
    assert overview.max_age_seconds == 15
    assert overview.require_observed_at is True
    assert fundamentals.reuse_scope == CacheReuseScope.CROSS_RUN
    assert fundamentals.max_age_seconds == 1800

def test_cross_run_cache_requires_policy_and_authoritative_observation_time():
    def compiled_for(capability: Capability) -> CompiledTaskV2:
        spec = capability_for(capability)
        candidate = StandardTask(
            task_id="cache",
            kind=StandardTaskKind(capability.value),
            objective="cache contract",
        )
        return CompiledTaskV2(
            task=ResolvedTask(candidate=candidate),
            capability=capability,
            capability_version=spec.version,
            intent_schema_version=spec.schema_version,
            execution_policy=spec.execution_policy,
            freshness_policy=spec.freshness_policy,
            resource_fingerprint="resource",
        )

    call = WorkflowCall(
        task_id="cache",
        step_id="read",
        tool_name="get_realtime_quotes",
        arguments={},
    )
    assert (
        execution_cache_key_v2(
            compiled_for(Capability.REALTIME_QUOTE),
            call,
            {"symbol": "600519"},
            model_config={"model": "test"},
        )
        is None
    )
    overview = compiled_for(Capability.MARKET_OVERVIEW)
    cache_key = execution_cache_key_v2(
        overview,
        call,
        {},
        model_config={"model": "test"},
    )
    assert cache_key is not None

    class Database:
        saved = 0

        def save_tool_cache(self, _key, _payload):
            self.saved += 1

    database = Database()
    save_execution_cache_v2(
        database,
        cache_key,
        {"success": True, "partial": False},
        freshness_policy=overview.freshness_policy,
    )
    assert database.saved == 0
    save_execution_cache_v2(
        database,
        cache_key,
        {
            "success": True,
            "partial": False,
            "observed_at": "2026-07-30T10:00:00+08:00",
        },
        freshness_policy=overview.freshness_policy,
    )
    assert database.saved == 1

def test_compiled_graph_checkpoint_round_trips_and_rejects_contract_drift():
    spec = capability_for(Capability.GENERAL_RESPONSE)
    candidate = StandardTask(
        task_id="answer",
        kind=StandardTaskKind.GENERAL_RESPONSE,
        objective="解释市盈率",
    )
    compiled = CompiledIntentGraphV2(
        run_id="checkpoint-run",
        plan=TaskPlan(tasks=[candidate]),
        tasks=(
            CompiledTaskV2(
                task=ResolvedTask(candidate=candidate),
                capability=Capability.GENERAL_RESPONSE,
                capability_version=spec.version,
                intent_schema_version=spec.schema_version,
                execution_policy=spec.execution_policy,
                freshness_policy=spec.freshness_policy,
                resource_fingerprint="resource-fingerprint",
            ),
        ),
        assumptions=(),
    )
    trace = PlanningTraceV2(
        run_id="checkpoint-run",
        schema_version="orchestrator-4.0",
    )
    checkpoint = serialize_compiled_intent_graph_v2(
        compiled,
        planning_trace=trace,
        request_fingerprint="request-fingerprint",
    )

    restored = restore_compiled_intent_graph_v2(
        checkpoint,
        expected_run_id="checkpoint-run",
        request_fingerprint="request-fingerprint",
    )
    assert restored is not None
    restored_graph, restored_trace = restored
    assert restored_graph.plan == compiled.plan
    assert restored_graph.tasks[0].freshness_policy == spec.freshness_policy
    assert restored_trace == trace

    drifted = {
        **checkpoint,
        "compiled_graph": {
            **checkpoint["compiled_graph"],
            "tasks": [
                {
                    **checkpoint["compiled_graph"]["tasks"][0],
                    "capability_version": "stale",
                }
            ],
        },
    }
    assert (
        restore_compiled_intent_graph_v2(
            drifted,
            expected_run_id="checkpoint-run",
            request_fingerprint="request-fingerprint",
        )
        is None
    )

def test_every_capability_accepts_and_normalizes_one_exact_typed_intent() -> None:
    values: dict[Capability, dict[str, Any]] = {capability: {} for capability in Capability}
    values.update(
        {
            Capability.SECURITY_LOOKUP: {"query": "贵州茅台"},
            Capability.MACRO_ANALYSIS: {"indicators": ["PMI"]},
            Capability.INDUSTRY_RESEARCH: {
                "explicit_subjects": ["人形机器人"],
            },
            Capability.THEME_STOCK_DISCOVERY: {
                "selection_mode": "named_subset",
                "themes": ["机器人执行器"],
            },
            Capability.STOCK_SCREENING: {
                "screen_spec": {
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
                }
            },
            Capability.COLLECTION_FINANCIAL_FILTER: {
                "predicates": [
                    {
                        "metric": "debt_ratio",
                        "operator": "gt",
                        "percent": 70,
                        "action": "exclude_matching",
                    }
                ],
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
        }
    )

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
            CAPABILITY_REGISTRY[capability].intent_model.model_validate(
                {
                    **payload,
                    "_program_owned_field": "forbidden",
                }
            )

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
