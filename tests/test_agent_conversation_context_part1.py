from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text

from src.agent.conversation_context import (
    ConversationContext,
    build_turn_reference,
    recover_context_from_thread_state,
)
from src.agent.task_executor import CallOutcome, PlanExecutionResult, TaskExecutionResult
from src.agent.task_planner import resolve_plan_entities
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResultSelectionMode,
    ResultSelectionSpec,
    ResolvedTask,
    SecurityEntity,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
)
from src.services.chat_session_service import ChatSessionService
from src.storage.migrations import ensure_compatible_schema



"""Focused test slice 1; shared fixtures remain local to this slice."""

def _quote_task() -> StandardTask:
    return StandardTask(
        task_id="quotes",
        kind=StandardTaskKind.REALTIME_QUOTE,
        objective="查看两只股票当前行情",
        entity_scope=EntityScope.CURRENT_MESSAGE,
        entities=["贵州茅台", "五粮液"],
        parameters={},
        depends_on=[],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.98,
    )

def _legacy_rich_thread_state() -> dict:
    return {
        "messages": [
            {
                "message": {
                    "id": "user-domain",
                    "role": "user",
                    "content": [{"type": "text", "text": "按这些领域找A股公司"}],
                },
            },
            {
                "message": {
                    "id": "assistant-domain",
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "这里是渲染后的候选名单，不参与迁移。"},
                        {
                            "type": "tool-call",
                            "toolCallId": "call-domain",
                            "toolName": "get_domain_stock_candidates",
                            "args": {
                                "domains": [{"label": "机器人执行器"}],
                            },
                            "result": {
                                "success": True,
                                "domainResults": [
                                    {
                                        "domain": "机器人执行器",
                                        "items": [
                                            {"symbol": "000001", "name": "甲公司"},
                                            {"symbol": "000002", "name": "乙公司"},
                                        ],
                                    },
                                ],
                            },
                        },
                    ],
                },
            },
            {
                "message": {
                    "id": "user-filter",
                    "role": "user",
                    "content": [{"type": "text", "text": "剔除不符合条件的公司"}],
                },
            },
            {
                "message": {
                    "id": "assistant-error",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "规划服务超时。"}],
                },
            },
        ],
    }
def test_turn_reference_uses_execution_objects_not_rendered_answer() -> None:
    candidate = _quote_task()
    resolved = ResolvedTask(candidate=candidate, symbols=("600519", "000858"))
    call = CallOutcome(
        task_id="quotes",
        step_id="quotes",
        tool_name="get_realtime_quotes",
        arguments={"symbols": "600519,000858"},
        result={
            "success": True,
            "items": [
                {"symbol": "600519", "name": "贵州茅台", "price": 1500},
                {"symbol": "000858", "name": "五粮液", "price": 120},
            ],
        },
        executed=True,
        reused=False,
    )
    execution = PlanExecutionResult(
        tasks=[
            TaskExecutionResult(
                task=resolved,
                status="completed",
                calls=[call],
                output_entities=(
                    SecurityEntity(symbol="600519", name="贵州茅台"),
                    SecurityEntity(symbol="000858", name="五粮液"),
                ),
            )
        ]
    )

    turn = build_turn_reference(
        "换一种完全不同的问法也要稳定",
        TaskPlan(tasks=[candidate]),
        [resolved],
        execution,
    )

    assert [item.symbol for item in turn.entities] == ["600519", "000858"]
    payload = turn.model_dump()
    assert "get_realtime_quotes" not in json.dumps(payload, ensure_ascii=False)
    assert payload["tasks"][0]["kind"] == "realtime_quote"

def test_turn_reference_preserves_large_terminal_candidate_collection() -> None:
    candidate = StandardTask(
        task_id="domain_candidates",
        kind=StandardTaskKind.THEME_STOCK_DISCOVERY,
        objective="取得完整板块候选集合",
        entity_scope=EntityScope.NONE,
        entities=[],
        parameters={
            "domains": [
                {
                    "label": "机器人执行器",
                    "board_queries": ["机器人执行器"],
                    "mapping_type": "catalog_binding",
                    "rationale": "实时板块精确对应",
                    "unresolved_parts": [],
                }
            ]
        },
        depends_on=[],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.98,
    )
    securities = tuple(SecurityEntity(symbol=f"{index:06d}", name=f"公司{index}") for index in range(1, 870))
    resolved = ResolvedTask(candidate=candidate)
    execution = PlanExecutionResult(
        tasks=[
            TaskExecutionResult(
                task=resolved,
                status="completed",
                output_entities=securities,
            )
        ]
    )

    turn = build_turn_reference(
        "找板块全部股票",
        TaskPlan(tasks=[candidate]),
        [resolved],
        execution,
    )

    assert len(turn.entities) == 869
    assert turn.entities[-1].symbol == "000869"

def test_failed_terminal_filter_does_not_promote_upstream_candidates() -> None:
    discovery = StandardTask(
        task_id="domain_candidates",
        kind=StandardTaskKind.THEME_STOCK_DISCOVERY,
        objective="取得板块候选集合",
        entity_scope=EntityScope.NONE,
        entities=[],
        parameters={
            "domains": [
                {
                    "label": "减速器",
                    "board_queries": ["减速器"],
                    "mapping_type": "catalog_binding",
                    "rationale": "实时板块精确对应",
                    "unresolved_parts": [],
                }
            ]
        },
        depends_on=[],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.98,
    )
    evidence = StandardTask(
        task_id="business_evidence",
        kind=StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        objective="核验候选公司业务事实",
        entity_scope=EntityScope.NONE,
        entities=[],
        parameters={
            "domains": [{"label": "减速器"}],
            "candidate_scope": "candidate_collection",
        },
        depends_on=["domain_candidates"],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.98,
    )
    discovery_resolved = ResolvedTask(candidate=discovery)
    evidence_resolved = ResolvedTask(candidate=evidence)
    execution = PlanExecutionResult(
        tasks=[
            TaskExecutionResult(
                task=discovery_resolved,
                status="completed",
                output_entities=tuple(
                    SecurityEntity(symbol=f"{index:06d}", name=f"公司{index}") for index in range(1, 870)
                ),
            ),
            TaskExecutionResult(
                task=evidence_resolved,
                status="failed",
                errors=["公开来源暂不可用"],
            ),
        ]
    )

    turn = build_turn_reference(
        "找板块中正在大力发展的股票",
        TaskPlan(tasks=[discovery, evidence]),
        [discovery_resolved, evidence_resolved],
        execution,
    )

    assert turn.entities == []
    assert len(turn.tasks[0].entities) == 869

def test_ranked_domain_artifact_is_saved_for_followup_planning() -> None:
    candidate = StandardTask(
        task_id="industry",
        kind=StandardTaskKind.INDUSTRY_RESEARCH,
        objective="分析人形机器人最受益领域",
        entity_scope=EntityScope.NONE,
        entities=[],
        parameters={
            "query": "人形机器人最受益领域",
            "domains": [
                {
                    "label": "人形机器人",
                    "board_queries": ["人形机器人"],
                    "mapping_type": "catalog_binding",
                    "rationale": "精确对应",
                    "unresolved_parts": [],
                }
            ],
        },
        depends_on=[],
        result_selection=ResultSelectionSpec(
            mode=ResultSelectionMode.ALL_RELEVANT,
            max_items=None,
        ),
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.98,
    )
    resolved = ResolvedTask(candidate=candidate)
    artifact = {
        "type": "ranked_domains",
        "topic": "人形机器人",
        "result_selection": {
            "mode": "all_relevant",
            "max_items": None,
        },
        "groups": [
            {
                "tier": 1,
                "domains": [
                    {
                        "label": "机器人执行器",
                        "tier": 1,
                        "board_queries": ["机器人执行器"],
                        "mapping_type": "catalog_binding",
                    },
                    {
                        "label": "减速器",
                        "tier": 1,
                        "board_queries": ["减速器"],
                        "mapping_type": "catalog_binding",
                    },
                    {
                        "label": "传感器",
                        "tier": 1,
                        "board_queries": ["传感器"],
                        "mapping_type": "catalog_binding",
                    },
                ],
            }
        ],
    }
    execution = PlanExecutionResult(
        tasks=[
            TaskExecutionResult(
                task=resolved,
                status="completed",
                derived_results=[
                    {
                        "task_id": "industry",
                        "step_id": "result_ranked_domain_selection",
                        "processor": "ranked_domain_selection",
                        "result": {
                            "success": True,
                            "semantic_artifacts": [artifact],
                            "resource_outputs": {
                                "domain_collection": [
                                    {"label": "机器人执行器", "tier": 1},
                                    {"label": "减速器", "tier": 1},
                                    {"label": "传感器", "tier": 1},
                                ],
                            },
                        },
                    }
                ],
                resource_outputs={
                    "domain_collection": [
                        {"label": "机器人执行器", "tier": 1},
                        {"label": "减速器", "tier": 1},
                        {"label": "传感器", "tier": 1},
                    ],
                },
            )
        ]
    )

    turn = build_turn_reference(
        "人形机器人哪些领域最受益？",
        TaskPlan(tasks=[candidate]),
        [resolved],
        execution,
    )
    context = ConversationContext(turns=[turn])
    planner_payload = context.task_selection_payload()

    saved = planner_payload["turns"][0]["tasks"][0]["semantic_artifacts"]
    assert saved[0]["type"] == "ranked_domains"
    assert planner_payload["turns"][0]["tasks"][0]["result_selection"] == {"mode": "all_relevant", "max_items": None}
    assert [item["label"] for item in saved[0]["groups"][0]["domains"]] == ["机器人执行器", "减速器", "传感器"]
    assert "markdown" not in json.dumps(planner_payload, ensure_ascii=False).lower()

def test_financial_filter_turn_exposes_retained_collection_not_input_collection() -> None:
    candidate = StandardTask(
        task_id="filter",
        kind=StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        objective="剔除负债率高于70%的公司",
        entity_scope=EntityScope.PREVIOUS_ANSWER,
        entities=[],
        parameters={
            "conditions": [
                {
                    "metric": "debt_ratio",
                    "period_basis": "latest_report",
                    "operator": "gt",
                    "threshold": 70,
                    "threshold_unit": "percent",
                    "action": "exclude_matching",
                }
            ]
        },
        depends_on=[],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.99,
    )
    resolved = ResolvedTask(
        candidate=candidate,
        symbols=("600519", "000858", "300750"),
    )
    call = CallOutcome(
        task_id="filter",
        step_id="financial_filter_batch_1",
        tool_name="get_multi_stock_financials",
        arguments={},
        result={
            "success": True,
            "items": [
                {"symbol": "600519", "name": "贵州茅台", "financial_value": 20.0},
                {"symbol": "000858", "name": "五粮液", "financial_value": 72.0},
                {"symbol": "300750", "name": "宁德时代", "financial_value": 60.0},
            ],
        },
        executed=True,
        reused=False,
    )
    execution = PlanExecutionResult(
        tasks=[
            TaskExecutionResult(
                task=resolved,
                status="completed",
                calls=[call],
                output_entities=(
                    SecurityEntity(symbol="600519", name="贵州茅台"),
                    SecurityEntity(symbol="300750", name="宁德时代"),
                ),
            )
        ]
    )

    turn = build_turn_reference(
        "剔除负债率高于70%的公司",
        TaskPlan(tasks=[candidate]),
        [resolved],
        execution,
    )

    assert [item.symbol for item in turn.entities] == ["600519", "300750"]

def test_previous_scope_uses_complete_program_collection_not_planner_sample() -> None:
    previous = [{"symbol": f"{index:06d}", "name": f"公司{index}"} for index in range(10, 20)]
    candidate = StandardTask(
        task_id="buy",
        kind=StandardTaskKind.INVESTMENT_DECISION,
        objective="他们中哪些能买",
        entity_scope=EntityScope.PREVIOUS_ANSWER,
        # Simulate a model copying one identity from a bounded context sample.
        entities=["贵州茅台"],
        parameters={},
        depends_on=[],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.99,
    )

    resolved = resolve_plan_entities(
        TaskPlan(tasks=[candidate]),
        current_entities=[],
        previous_answer_entities=previous,
    )

    assert resolved[0].symbols == tuple(item["symbol"] for item in previous)

def test_conversation_context_keeps_recent_verified_scopes() -> None:
    context = ConversationContext.from_value(
        {
            "version": "1",
            "turns": [
                {
                    "request": f"request-{index}",
                    "tasks": [],
                    "entities": [{"symbol": f"{index:06d}", "name": f"公司{index}"}],
                }
                for index in range(6)
            ],
        }
    )

    assert [turn.request for turn in context.turns] == [
        "request-2",
        "request-3",
        "request-4",
        "request-5",
    ]
    assert context.latest_entities() == [{"symbol": "000005", "name": "公司5"}]

def test_regeneration_uses_context_before_the_same_completed_request() -> None:
    context = ConversationContext.from_value(
        {
            "version": "1",
            "turns": [
                {
                    "request": "筛选后保留这些公司",
                    "tasks": [],
                    "entities": [
                        {"symbol": "600519", "name": "贵州茅台"},
                        {"symbol": "000858", "name": "五粮液"},
                    ],
                },
                {
                    "request": "他们中哪些能买入？",
                    "tasks": [],
                    "entities": [{"symbol": "600519", "name": "贵州茅台"}],
                },
            ],
        }
    )

    prior = context.before_request("他们中哪些能买入？")

    assert prior.latest_entities() == [
        {"symbol": "600519", "name": "贵州茅台"},
        {"symbol": "000858", "name": "五粮液"},
    ]
