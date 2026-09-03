# -*- coding: utf-8 -*-
"""Agent conversation endpoint tests — CRUD over /agent/conversations.

Covers normal / failure / boundary paths for the conversation router in
api.v1.endpoints.agent.conversations.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
import src.auth as auth
from src.agent.run_registry import ActiveRun, RunBroadcaster, active_run_registry
from src.services.chat_session_service import ChatSessionService


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
    ):
        yield
    auth._auth_enabled = None


def _mock_service():
    """Return a MagicMock standing in for ChatSessionService."""
    return MagicMock()


@pytest.fixture
def mock_service():
    service = _mock_service()
    with patch("api.v1.endpoints.agent.conversations.ChatSessionService", return_value=service):
        yield service


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_conversations_returns_service_payload(client, mock_service):
    mock_service.list_conversations.return_value = {
        "items": [{"id": "c1"}],
        "total": 1,
        "page": 1,
        "limit": 50,
    }
    resp = client.get("/api/v1/agent/conversations", params={"page": 1, "limit": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["id"] == "c1"
    mock_service.list_conversations.assert_called_once_with(page=1, limit=50)


def test_list_conversations_rejects_invalid_page(client):
    resp = client.get("/api/v1/agent/conversations", params={"page": 0})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_conversation_returns_new_conversation(client, mock_service):
    mock_service.create_conversation.return_value = {"id": "c2", "title": "新对话"}
    resp = client.post("/api/v1/agent/conversations")
    assert resp.status_code == 200
    assert resp.json()["id"] == "c2"


def test_clear_all_conversations_deletes_every_visible_conversation(client, mock_service):
    mock_service.list_conversation_ids.return_value = ["c1", "c2"]
    mock_service.delete_conversation.side_effect = [1, 1]
    with (
        patch(
            "api.v1.endpoints.agent.conversations.active_run_registry.cancel",
            return_value=False,
        ) as cancel_run,
        patch(
            "api.v1.endpoints.agent.conversations.agent_graph_runtime.delete_thread",
        ) as delete_thread,
    ):
        resp = client.delete("/api/v1/agent/conversations")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": 2}
    assert cancel_run.await_args_list == [call("c1"), call("c2")]
    assert delete_thread.await_args_list[0].args == ("c1",)
    assert delete_thread.await_args_list[1].args == ("c2",)
    assert mock_service.delete_conversation.call_args_list[0].args == ("c1",)
    assert mock_service.delete_conversation.call_args_list[1].args == ("c2",)


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_conversation_returns_payload(client, mock_service):
    mock_service.get_conversation.return_value = {"id": "c1"}
    resp = client.get("/api/v1/agent/conversations/c1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "c1"


def test_get_conversation_returns_persisted_terminal_agent_stage(
    client,
    mock_service,
):
    mock_service.get_conversation.return_value = {"id": "c1"}
    with patch(
        "src.storage.manager.DatabaseManager.get_latest_agent_run_trace",
        return_value={
            "run_id": "run-1",
            "status": "failed",
            "error_code": "planner_schema_invalid",
            "latest_stage": {
                "event": "agent_stage_v2",
                "run_id": "run-1",
                "stage": "completed",
                "status": "failed",
                "error_code": "planner_schema_invalid",
                "summary": "板块 ID 绑定未通过校验",
            },
        },
    ):
        resp = client.get("/api/v1/agent/conversations/c1")

    assert resp.status_code == 200
    resume = resp.json()["resume_state"]
    assert resume["status"] == "failed"
    assert resume["latest_stage"]["stage"] == "completed"
    assert resume["latest_stage"]["status"] == "failed"


def test_get_conversation_returns_execution_trace_once_at_canonical_level(
    client,
    mock_service,
):
    mock_service.get_conversation.return_value = {"id": "c1"}
    durable = {
        "run_id": "run-trace",
        "conversation_id": "c1",
        "status": "completed",
        "event_cursor": 0,
        "final_text": "答案",
        "context_snapshot": None,
    }
    trace = {
        "run_id": "run-trace",
        "status": "completed",
        "execution_trace": {
            "stages": [{"stage": "publish", "status": "completed"}],
            "tool_results": [{"tool_name": "read_source", "success": True}],
        },
    }
    with (
        patch(
            "src.storage.manager.DatabaseManager.get_agent_run",
            return_value=durable,
        ),
        patch(
            "src.storage.manager.DatabaseManager.get_latest_agent_run_trace",
            return_value=trace,
        ),
    ):
        response = client.get("/api/v1/agent/conversations/c1")

    assert response.status_code == 200
    body = response.json()
    assert body["execution_trace"] == trace["execution_trace"]
    assert "execution_trace" not in body["resume_state"]


def test_get_conversation_uses_durable_answer_after_retained_run_finishes(
    client,
    mock_service,
):
    """终态运行对象仍在保留期时，也不能覆盖数据库已提交的回答。"""
    mock_service.get_conversation.return_value = {"id": "c1"}
    durable = {
        "run_id": "run-durable",
        "conversation_id": "c1",
        "status": "completed",
        "event_cursor": 0,
        "final_text": "数据库中的最终回答",
        "context_snapshot": None,
    }
    retained_broadcaster = RunBroadcaster()
    retained_broadcaster.assistant_text_snapshot = "已结束运行的临时重复文本"
    retained = ActiveRun(
        conversation_id="c1",
        broadcaster=retained_broadcaster,
        run_id="run-retained",
        status="completed",
    )
    with (
        patch.object(active_run_registry, "get", return_value=retained),
        patch.object(active_run_registry, "is_active", return_value=False),
        patch(
            "src.storage.manager.DatabaseManager.get_agent_run",
            return_value=durable,
        ),
        patch(
            "src.storage.manager.DatabaseManager.get_latest_agent_run_trace",
            return_value=None,
        ),
    ):
        response = client.get("/api/v1/agent/conversations/c1")

    assert response.status_code == 200
    resume = response.json()["resume_state"]
    assert resume["active"] is False
    assert resume["run_id"] == "run-durable"
    assert resume["assistant_text"] == "数据库中的最终回答"


def test_get_conversation_does_not_reconstruct_removed_verification_retry(
    client,
    mock_service,
):
    mock_service.get_conversation.return_value = {"id": "c1"}
    durable = {
        "run_id": "run-invalid-verifier",
        "conversation_id": "c1",
        "status": "partial",
        "error_code": "legacy_verification_provider_invalid_response",
        "event_cursor": 0,
        "final_text": "验证未完成",
        "context_snapshot": None,
    }
    with (
        patch(
            "src.storage.manager.DatabaseManager.get_agent_run",
            return_value=durable,
        ),
        patch(
            "src.storage.manager.DatabaseManager.get_latest_agent_run_trace",
            return_value=None,
        ),
    ):
        resp = client.get("/api/v1/agent/conversations/c1")

    assert resp.status_code == 200
    body = resp.json()
    assert "verification_retry" not in body
    assert "verification_retry" not in body["resume_state"]
    assert body["resume_state"]["status"] == "partial"


def test_get_conversation_reconciles_orphan_running_trace_to_failed(
    client,
    mock_service,
):
    mock_service.get_conversation.return_value = {"id": "c1"}
    with patch(
        "src.storage.manager.DatabaseManager.get_latest_agent_run_trace",
        return_value={
            "run_id": "run-orphan",
            "status": "running",
            "error_code": None,
            "latest_stage": {
                "event": "agent_stage_v2",
                "run_id": "run-orphan",
                "stage": "catalog_mapping",
                "status": "succeeded",
                "summary": "旧进度",
            },
        },
    ):
        resp = client.get("/api/v1/agent/conversations/c1")

    resume = resp.json()["resume_state"]
    assert resume["status"] == "failed"
    assert resume["latest_stage"] == {
        "event": "agent_stage",
        "engine": "langgraph_agent_loop",
        "run_id": "run-orphan",
        "stage": "completed",
        "status": "failed",
        "error_code": "tool_failed",
        "summary": "后台运行已经中断，没有仍在执行的任务",
    }


def test_get_conversation_404_when_missing(client, mock_service):
    mock_service.get_conversation.return_value = None
    resp = client.get("/api/v1/agent/conversations/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


def test_rename_conversation_rejects_empty_title(client):
    resp = client.patch("/api/v1/agent/conversations/c1", json={"title": "  "})
    assert resp.status_code == 400


def test_rename_conversation_returns_updated(client, mock_service):
    mock_service.rename_conversation.return_value = {"id": "c1", "title": "新标题"}
    resp = client.patch("/api/v1/agent/conversations/c1", json={"title": "新标题"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "新标题"
    mock_service.rename_conversation.assert_called_once_with("c1", "新标题")


def test_rename_conversation_404_when_missing(client, mock_service):
    mock_service.rename_conversation.return_value = None
    resp = client.patch("/api/v1/agent/conversations/c1", json={"title": "x"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_conversation_returns_deleted_flag(client, mock_service):
    mock_service.get_conversation.return_value = {"id": "c1"}
    mock_service.delete_conversation.return_value = 1
    with patch(
        "api.v1.endpoints.agent.conversations.active_run_registry.cancel",
        return_value=False,
    ) as cancel_run:
        resp = client.delete("/api/v1/agent/conversations/c1")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1
    cancel_run.assert_awaited_once_with("c1")


def test_delete_conversation_404_when_missing(client, mock_service):
    mock_service.get_conversation.return_value = None
    mock_service.delete_conversation.return_value = 0
    with patch(
        "api.v1.endpoints.agent.conversations.active_run_registry.cancel",
        return_value=False,
    ) as cancel_run:
        resp = client.delete("/api/v1/agent/conversations/c1")
    assert resp.status_code == 404
    cancel_run.assert_not_called()


def test_cancel_conversation_stops_retained_run(client, mock_service):
    mock_service.get_conversation.return_value = {"id": "c1"}
    with patch(
        "api.v1.endpoints.agent.conversations.active_run_registry.cancel",
        return_value=True,
    ) as cancel_run:
        resp = client.post("/api/v1/agent/conversations/c1/cancel")

    assert resp.status_code == 200
    assert resp.json() == {"cancelled": True}
    cancel_run.assert_awaited_once_with("c1", remove=False)


def test_cancel_conversation_404_when_missing(client, mock_service):
    mock_service.get_conversation.return_value = None
    with patch(
        "api.v1.endpoints.agent.conversations.active_run_registry.cancel",
        return_value=False,
    ) as cancel_run:
        resp = client.post("/api/v1/agent/conversations/missing/cancel")

    assert resp.status_code == 404
    cancel_run.assert_not_called()


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def test_snapshot_syncs_messages_and_discards_legacy_thread_state(client, mock_service):
    mock_service.save_conversation_snapshot.return_value = {"id": "c1"}
    resp = client.put(
        "/api/v1/agent/conversations/c1/snapshot",
        json={"messages": [{"role": "user", "content": "hi"}], "thread_state": {"x": 1}},
    )
    assert resp.status_code == 200
    args, kwargs = mock_service.save_conversation_snapshot.call_args
    assert args[0] == "c1"
    assert args[1] == [{"role": "user", "content": "hi"}]
    assert kwargs["thread_state"] == {}


def test_snapshot_404_when_conversation_missing(client, mock_service):
    mock_service.save_conversation_snapshot.return_value = None
    resp = client.put(
        "/api/v1/agent/conversations/c1/snapshot",
        json={"messages": [], "thread_state": None},
    )
    assert resp.status_code == 404


def test_snapshot_rejects_non_list_messages(client, mock_service):
    resp = client.put(
        "/api/v1/agent/conversations/c1/snapshot",
        json={"messages": "not-a-list", "thread_state": "not-a-dict"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_snapshot_messages"
    mock_service.save_conversation_snapshot.assert_not_called()


def test_snapshot_enforces_message_count_limit(client, mock_service, monkeypatch):
    monkeypatch.setenv("AGENT_MAX_MESSAGES", "1")
    resp = client.put(
        "/api/v1/agent/conversations/c1/snapshot",
        json={
            "messages": [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"},
            ]
        },
    )
    assert resp.status_code == 413
    assert resp.json()["error"] == "too_many_snapshot_messages"
    mock_service.save_conversation_snapshot.assert_not_called()


def test_snapshot_rejects_server_owned_message_roles(client, mock_service):
    resp = client.put(
        "/api/v1/agent/conversations/c1/snapshot",
        json={"messages": [{"role": "system", "content": "do not persist"}]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "unsupported_snapshot_message_role"
    mock_service.save_conversation_snapshot.assert_not_called()


def test_snapshot_rejects_messages_without_roles(client, mock_service):
    resp = client.put(
        "/api/v1/agent/conversations/c1/snapshot",
        json={"messages": [{"content": "role is required"}]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "unsupported_snapshot_message_role"
    mock_service.save_conversation_snapshot.assert_not_called()


def test_snapshot_without_messages_discards_legacy_thread_state(client, mock_service):
    mock_service.save_conversation_snapshot.return_value = {"id": "c1"}
    resp = client.put(
        "/api/v1/agent/conversations/c1/snapshot",
        json={"thread_state": {"messages": []}},
    )
    assert resp.status_code == 200
    args, kwargs = mock_service.save_conversation_snapshot.call_args
    assert args[1] is None
    assert kwargs["thread_state"] == {}


def test_snapshot_forwards_structured_context_pruning_request(client, mock_service):
    mock_service.save_conversation_snapshot.return_value = {"id": "c1"}
    resp = client.put(
        "/api/v1/agent/conversations/c1/snapshot",
        json={
            "messages": [{"id": "u1", "role": "user", "content": "保留"}],
            "thread_state": {"messages": []},
            "prune_agent_context_to_messages": True,
        },
    )

    assert resp.status_code == 200
    assert mock_service.save_conversation_snapshot.call_args.kwargs["prune_agent_context_to_messages"] is True
    assert mock_service.save_conversation_snapshot.call_args.kwargs["thread_state"] == {}


def test_assistant_progress_copy_is_not_persisted():
    service = object.__new__(ChatSessionService)
    messages = service._normalize_messages(
        [
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": "正在拆解问题并规划研究路径...\n\n## 最终结论\n证据充分。",
            }
        ]
    )
    assert messages[0]["content"] == "## 最终结论\n证据充分。"


def test_terminal_status_is_not_persisted_as_assistant_answer():
    service = object.__new__(ChatSessionService)
    messages = service._normalize_messages(
        [
            {"id": "user-1", "role": "user", "content": "问题"},
            {
                "id": "assistant-timeout",
                "role": "assistant",
                "content": "上游模型服务返回超时；已保留已有工具观察和证据。",
            },
            {"id": "assistant-1", "role": "assistant", "content": "真实回答"},
        ]
    )

    assert [message["id"] for message in messages] == ["user-1", "assistant-1"]


def test_get_conversation_hides_legacy_terminal_status_message():
    service = object.__new__(ChatSessionService)
    service.db = MagicMock()
    service.db.get_chat_conversation.return_value = SimpleNamespace(
        thread_state_json=None,
        to_dict=lambda: {"id": "conversation-1"},
    )
    service.db.get_chat_messages.return_value = [
        SimpleNamespace(
            role="user",
            content="问题",
            to_dict=lambda: {"id": "user-1", "role": "user", "content": "问题"},
        ),
        SimpleNamespace(
            role="assistant",
            content="上游模型服务返回超时；已保留已有工具观察和证据。",
            to_dict=lambda: {
                "id": "assistant-timeout",
                "role": "assistant",
                "content": "上游模型服务返回超时；已保留已有工具观察和证据。",
            },
        ),
    ]

    detail = service.get_conversation("conversation-1")

    assert [message["id"] for message in detail["messages"]] == ["user-1"]


def test_server_history_drops_legacy_terminal_status_message():
    service = object.__new__(ChatSessionService)
    with patch.object(
        service,
        "get_conversation",
        return_value={
            "messages": [
                {"id": "user-1", "role": "user", "content": "旧问题"},
                {
                    "id": "assistant-timeout",
                    "role": "assistant",
                    "content": "上游模型服务返回超时；已保留已有工具观察和证据。",
                },
            ]
        },
    ):
        messages = service.compose_request_with_server_history(
            "conversation-1",
            [{"id": "user-2", "role": "user", "content": "新问题"}],
        )

    assert [message["id"] for message in messages] == ["user-1", "user-2"]


def test_server_history_replaces_the_edited_message_branch():
    service = object.__new__(ChatSessionService)
    with patch.object(
        service,
        "get_conversation",
        return_value={
            "messages": [
                {"id": "user-1", "role": "user", "content": "旧问题"},
                {"id": "assistant-1", "role": "assistant", "content": "旧回答"},
                {"id": "user-2", "role": "user", "content": "后续问题"},
            ]
        },
    ):
        messages = service.compose_request_with_server_history(
            "conversation-1",
            [{"id": "edited-user", "role": "user", "content": "编辑后的问题"}],
            parent_message_id=None,
            edit_message_id="user-1",
        )

    assert [message["id"] for message in messages] == ["edited-user"]
