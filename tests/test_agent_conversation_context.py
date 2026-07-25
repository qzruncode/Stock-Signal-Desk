from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

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
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
)
from src.services.chat_session_service import ChatSessionService


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
    execution = PlanExecutionResult(tasks=[TaskExecutionResult(
        task=resolved,
        status="completed",
        calls=[call],
    )])

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


def test_financial_filter_turn_exposes_retained_collection_not_input_collection() -> None:
    candidate = StandardTask(
        task_id="filter",
        kind=StandardTaskKind.COLLECTION_FINANCIAL_FILTER,
        objective="剔除负债率高于70%的公司",
        entity_scope=EntityScope.PREVIOUS_ANSWER,
        entities=[],
        parameters={
            "metric": "debt_ratio",
            "period_basis": "latest_report",
            "operator": "gt",
            "threshold": 70,
            "threshold_unit": "percent",
            "action": "exclude_matching",
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
    execution = PlanExecutionResult(tasks=[TaskExecutionResult(
        task=resolved,
        status="completed",
        calls=[call],
    )])

    turn = build_turn_reference(
        "剔除负债率高于70%的公司",
        TaskPlan(tasks=[candidate]),
        [resolved],
        execution,
    )

    assert [item.symbol for item in turn.entities] == ["600519", "300750"]


def test_previous_scope_uses_complete_program_collection_not_planner_sample() -> None:
    previous = [
        {"symbol": f"{index:06d}", "name": f"公司{index}"}
        for index in range(10, 20)
    ]
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
    context = ConversationContext.from_value({
        "version": "1",
        "turns": [
            {
                "request": f"request-{index}",
                "tasks": [],
                "entities": [{"symbol": f"{index:06d}", "name": f"公司{index}"}],
            }
            for index in range(6)
        ],
    })

    assert [turn.request for turn in context.turns] == [
        "request-2", "request-3", "request-4", "request-5",
    ]
    assert context.latest_entities() == [{"symbol": "000005", "name": "公司5"}]


def test_regeneration_uses_context_before_the_same_completed_request() -> None:
    context = ConversationContext.from_value({
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
    })

    prior = context.before_request("他们中哪些能买入？")

    assert prior.latest_entities() == [
        {"symbol": "600519", "name": "贵州茅台"},
        {"symbol": "000858", "name": "五粮液"},
    ]


def test_frontend_snapshot_preserves_server_agent_context() -> None:
    service = object.__new__(ChatSessionService)
    service.db = MagicMock()
    service.db.get_chat_conversation.return_value = SimpleNamespace(
        thread_state_json=json.dumps({
            "agent_context": {"version": "1", "turns": []},
        }, ensure_ascii=False),
        title_source="manual",
    )
    service.db.replace_chat_messages.return_value = None
    service.get_conversation = MagicMock(return_value={"id": "c1"})

    service.save_conversation_snapshot(
        "c1",
        [],
        thread_state={
            "messages": [{
                "message": {
                    "id": "u1",
                    "role": "user",
                    "content": [{"type": "text", "text": "继续"}],
                },
            }],
        },
    )

    serialized = service.db.replace_chat_messages.call_args.kwargs["thread_state_json"]
    saved_state = json.loads(serialized)
    assert saved_state["agent_context"] == {"version": "1", "turns": []}


def test_server_context_update_does_not_replace_fresh_messages_with_stale_thread_state() -> None:
    service = object.__new__(ChatSessionService)
    service.db = MagicMock()
    service.db.get_chat_conversation.return_value = SimpleNamespace(
        thread_state_json=json.dumps({
            "messages": [{
                "message": {
                    "id": "old",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "旧回复"}],
                },
            }],
        }, ensure_ascii=False),
        title_source="manual",
    )
    service.db.replace_chat_messages.return_value = None
    service.get_conversation = MagicMock(return_value={"id": "c1"})

    service.save_conversation_snapshot(
        "c1",
        [{"id": "new", "role": "assistant", "content": "新回复"}],
        agent_context={"version": "1", "turns": []},
    )

    saved_messages = service.db.replace_chat_messages.call_args.args[1]
    assert [message["content"] for message in saved_messages] == ["新回复"]


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


def test_legacy_context_recovery_uses_only_completed_structured_calls() -> None:
    context = recover_context_from_thread_state(_legacy_rich_thread_state())

    assert len(context.turns) == 1
    assert context.turns[0].request == "按这些领域找A股公司"
    assert context.turns[0].tasks[0].kind == "legacy_structured_execution"
    assert context.latest_entities() == [
        {"symbol": "000001", "name": "甲公司"},
        {"symbol": "000002", "name": "乙公司"},
    ]
    serialized = json.dumps(context.model_dump(), ensure_ascii=False)
    assert "get_domain_stock_candidates" not in serialized
    assert "toolName" not in serialized


def test_chat_session_lazily_recovers_legacy_agent_context() -> None:
    service = object.__new__(ChatSessionService)
    service.db = MagicMock()
    service.db.get_chat_conversation.return_value = SimpleNamespace(
        thread_state_json=json.dumps(_legacy_rich_thread_state(), ensure_ascii=False),
    )

    recovered = service.get_agent_context("legacy-conversation")

    assert recovered["turns"][0]["tasks"][0]["kind"] == "legacy_structured_execution"
    assert recovered["turns"][0]["entities"] == [
        {"symbol": "000001", "name": "甲公司"},
        {"symbol": "000002", "name": "乙公司"},
    ]


def test_turn_reference_never_extracts_securities_from_prose_fields() -> None:
    candidate = StandardTask(
        task_id="general",
        kind=StandardTaskKind.GENERAL_RESPONSE,
        objective="解释行业背景",
        entity_scope=EntityScope.NONE,
        entities=[],
        parameters={},
        depends_on=[],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.9,
    )
    resolved = ResolvedTask(candidate=candidate)
    call = CallOutcome(
        task_id="general",
        step_id="answer",
        tool_name="general_financial_qa",
        arguments={"question": "机器人"},
        result={
            "success": True,
            "summary": "文字里即使出现贵州茅台 600519，也不是结构化证券结果。",
        },
        executed=True,
        reused=False,
    )
    execution = PlanExecutionResult(tasks=[TaskExecutionResult(
        task=resolved,
        status="completed",
        calls=[call],
    )])

    turn = build_turn_reference(
        "解释一下",
        TaskPlan(tasks=[candidate]),
        [resolved],
        execution,
    )

    assert turn.entities == []
