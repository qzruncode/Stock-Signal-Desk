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



"""Focused test slice 2; shared fixtures remain local to this slice."""

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
def test_context_pruning_keeps_only_surviving_user_turns() -> None:
    context = ConversationContext.from_value(
        {
            "version": "1",
            "turns": [
                {"request_message_id": "u1", "request": "问题一"},
                {"request_message_id": "u2", "request": "问题二"},
                {"request_message_id": "u3", "request": "问题三"},
            ],
        }
    )

    retained = context.retain_for_messages(
        [
            {"id": "u1", "role": "user", "content": "问题一"},
            {"id": "a1", "role": "assistant", "content": "回答一"},
            {"id": "u3", "role": "user", "content": "问题三"},
        ]
    )

    assert [turn.request_message_id for turn in retained.turns] == ["u1", "u3"]

def test_context_pruning_matches_legacy_turns_by_exact_request_order() -> None:
    context = ConversationContext.from_value(
        {
            "version": "1",
            "turns": [
                {"request": "问题一"},
                {"request": "问题二"},
            ],
        }
    )

    retained = context.retain_for_messages(
        [
            {"id": "u1", "role": "user", "content": "问题一"},
        ]
    )

    assert len(retained.turns) == 1
    assert retained.turns[0].request_message_id == "u1"

def test_schema_migration_backfills_server_context_from_legacy_thread_state() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
            CREATE TABLE chat_conversations (
                id VARCHAR(64) PRIMARY KEY,
                thread_state_json TEXT
            )
        """
            )
        )
        connection.execute(
            text("INSERT INTO chat_conversations (id, thread_state_json) " "VALUES (:id, :thread_state_json)"),
            {
                "id": "legacy",
                "thread_state_json": json.dumps(
                    {
                        "agent_context": {
                            "version": "1",
                            "turns": [{"request": "旧问题"}],
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        )

    ensure_compatible_schema(engine, is_sqlite_engine=True)

    with engine.connect() as connection:
        raw = connection.execute(
            text("SELECT agent_context_json FROM chat_conversations WHERE id='legacy'")
        ).scalar_one()
    assert json.loads(raw)["turns"][0]["request"] == "旧问题"

def test_frontend_snapshot_does_not_write_server_agent_context() -> None:
    service = object.__new__(ChatSessionService)
    service.db = MagicMock()
    service.db.get_chat_conversation.return_value = SimpleNamespace(
        thread_state_json=json.dumps({"messages": []}, ensure_ascii=False),
        agent_context_json=json.dumps(
            {"version": "1", "turns": []},
            ensure_ascii=False,
        ),
        title_source="manual",
    )
    service.db.replace_chat_messages.return_value = None
    service.get_conversation = MagicMock(return_value={"id": "c1"})

    service.save_conversation_snapshot(
        "c1",
        [],
        thread_state={
            "messages": [
                {
                    "message": {
                        "id": "u1",
                        "role": "user",
                        "content": [{"type": "text", "text": "继续"}],
                    },
                }
            ],
        },
    )

    serialized = service.db.replace_chat_messages.call_args.kwargs["thread_state_json"]
    saved_state = json.loads(serialized)
    assert "agent_context" not in saved_state
    assert service.db.replace_chat_messages.call_args.kwargs["agent_context_json"] is None

def test_frontend_deletion_prunes_server_agent_context_to_remaining_messages() -> None:
    service = object.__new__(ChatSessionService)
    service.db = MagicMock()
    service.db.get_chat_conversation.return_value = SimpleNamespace(
        thread_state_json=json.dumps({"messages": []}, ensure_ascii=False),
        agent_context_json=json.dumps(
            {
                "version": "1",
                "turns": [
                    {"request_message_id": "u1", "request": "保留问题"},
                    {"request_message_id": "u2", "request": "删除问题"},
                ],
            },
            ensure_ascii=False,
        ),
        title_source="manual",
    )
    service.db.replace_chat_messages.return_value = None
    service.get_conversation = MagicMock(return_value={"id": "c1"})

    service.save_conversation_snapshot(
        "c1",
        [
            {"id": "u1", "role": "user", "content": "保留问题"},
            {"id": "a1", "role": "assistant", "content": "保留回答"},
        ],
        thread_state={"messages": []},
        prune_agent_context_to_messages=True,
    )

    serialized = service.db.replace_chat_messages.call_args.kwargs["agent_context_json"]
    saved_context = json.loads(serialized)
    assert [turn["request_message_id"] for turn in saved_context["turns"]] == ["u1"]

def test_empty_auto_snapshot_resets_title_and_preview() -> None:
    service = object.__new__(ChatSessionService)
    service.db = MagicMock()
    service.db.get_chat_conversation.return_value = SimpleNamespace(
        thread_state_json=None,
        title_source="auto",
    )
    service.db.replace_chat_messages.return_value = None
    service.get_conversation = MagicMock(return_value={"id": "c1"})

    service.save_conversation_snapshot(
        "c1",
        [],
        thread_state={"messages": [], "agent_context": None},
    )

    service.db.update_chat_conversation.assert_called_once()
    assert service.db.update_chat_conversation.call_args.kwargs["title"] == ChatSessionService.DEFAULT_TITLE
    assert service.db.update_chat_conversation.call_args.kwargs["preview_text"] is None

def test_thread_only_snapshot_does_not_replace_canonical_messages() -> None:
    service = object.__new__(ChatSessionService)
    service.db = MagicMock()
    service.db.get_chat_conversation.return_value = SimpleNamespace(
        thread_state_json=None,
        title_source="manual",
    )
    service.get_conversation = MagicMock(return_value={"id": "c1"})

    service.save_conversation_snapshot(
        "c1",
        None,
        thread_state={
            "messages": [
                {
                    "message": {
                        "id": "blank",
                        "role": "assistant",
                        "content": [],
                    },
                }
            ],
        },
    )

    service.db.replace_chat_messages.assert_not_called()
    service.db.update_chat_thread_state.assert_called_once()

def test_server_history_transport_keeps_canonical_history_and_prunes_a_branch() -> None:
    service = object.__new__(ChatSessionService)
    service.get_conversation = MagicMock(
        return_value={
            "messages": [
                {
                    "id": "u1",
                    "role": "user",
                    "content": "第一问",
                    "created_at": "2026-07-29T10:00:00",
                },
                {
                    "id": "a1",
                    "role": "assistant",
                    "content": "第一答",
                    "created_at": "2026-07-29T10:01:00",
                },
                {
                    "id": "u-old-branch",
                    "role": "user",
                    "content": "旧分支",
                    "created_at": "2026-07-29T10:02:00",
                },
                {
                    "id": "c1-assistant-pending",
                    "role": "assistant",
                    "content": "执行中",
                    "created_at": "2026-07-29T10:03:00",
                },
            ],
        }
    )

    result = service.compose_request_with_server_history(
        "c1",
        [{"id": "u2", "role": "user", "content": "新分支"}],
        parent_message_id="a1",
    )

    assert [message["id"] for message in result] == ["u1", "a1", "u2"]
    assert result[-1]["content"] == "新分支"

def test_server_context_update_does_not_replace_fresh_messages_with_stale_thread_state() -> None:
    service = object.__new__(ChatSessionService)
    service.db = MagicMock()
    service.db.get_chat_conversation.return_value = SimpleNamespace(
        thread_state_json=json.dumps(
            {
                "messages": [
                    {
                        "message": {
                            "id": "old",
                            "role": "assistant",
                            "content": [{"type": "text", "text": "旧回复"}],
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
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

def test_legacy_context_recovery_uses_only_completed_structured_calls() -> None:
    context = recover_context_from_thread_state(_legacy_rich_thread_state())

    assert len(context.turns) == 1
    assert context.turns[0].request == "按这些领域找A股公司"
    assert context.turns[0].tasks[0].kind == "theme_stock_discovery"
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

    assert recovered["turns"][0]["tasks"][0]["kind"] == "theme_stock_discovery"
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
    execution = PlanExecutionResult(
        tasks=[
            TaskExecutionResult(
                task=resolved,
                status="completed",
                calls=[call],
            )
        ]
    )

    turn = build_turn_reference(
        "解释一下",
        TaskPlan(tasks=[candidate]),
        [resolved],
        execution,
    )

    assert turn.entities == []
