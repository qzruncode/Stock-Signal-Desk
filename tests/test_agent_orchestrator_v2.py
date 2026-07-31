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



"""Shared fixtures for the focused test slices."""

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
