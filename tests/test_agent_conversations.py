# -*- coding: utf-8 -*-
"""Agent conversation endpoint tests — CRUD over /agent/conversations.

Covers normal / failure / boundary paths for the conversation router in
api.v1.endpoints.agent.conversations.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
import src.auth as auth
from src.services.chat_session_service import ChatSessionService


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False):
        yield
    auth._auth_enabled = None


def _mock_service():
    """Return a MagicMock standing in for ChatSessionService."""
    return MagicMock()


@pytest.fixture
def mock_service():
    service = _mock_service()
    with patch(
        "api.v1.endpoints.agent.conversations.ChatSessionService", return_value=service
    ):
        yield service


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_conversations_returns_service_payload(client, mock_service):
    mock_service.list_conversations.return_value = {
        "items": [{"id": "c1"}], "total": 1, "page": 1, "limit": 50,
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


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

def test_get_conversation_returns_payload(client, mock_service):
    mock_service.get_conversation.return_value = {"id": "c1"}
    resp = client.get("/api/v1/agent/conversations/c1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "c1"


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
    resp = client.patch(
        "/api/v1/agent/conversations/c1", json={"title": "新标题"}
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "新标题"
    mock_service.rename_conversation.assert_called_once_with("c1", "新标题")


def test_rename_conversation_404_when_missing(client, mock_service):
    mock_service.rename_conversation.return_value = None
    resp = client.patch(
        "/api/v1/agent/conversations/c1", json={"title": "x"}
    )
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

def test_snapshot_syncs_messages_and_thread_state(client, mock_service):
    mock_service.save_conversation_snapshot.return_value = {"id": "c1"}
    resp = client.put(
        "/api/v1/agent/conversations/c1/snapshot",
        json={"messages": [{"role": "user", "content": "hi"}], "thread_state": {"x": 1}},
    )
    assert resp.status_code == 200
    args, kwargs = mock_service.save_conversation_snapshot.call_args
    assert args[0] == "c1"
    assert args[1] == [{"role": "user", "content": "hi"}]
    assert kwargs["thread_state"] == {"x": 1}


def test_snapshot_404_when_conversation_missing(client, mock_service):
    mock_service.save_conversation_snapshot.return_value = None
    resp = client.put(
        "/api/v1/agent/conversations/c1/snapshot",
        json={"messages": [], "thread_state": None},
    )
    assert resp.status_code == 404


def test_snapshot_coerces_non_list_messages_to_empty(client, mock_service):
    mock_service.save_conversation_snapshot.return_value = {"id": "c1"}
    resp = client.put(
        "/api/v1/agent/conversations/c1/snapshot",
        json={"messages": "not-a-list", "thread_state": "not-a-dict"},
    )
    assert resp.status_code == 200
    args, kwargs = mock_service.save_conversation_snapshot.call_args
    assert args[1] == []
    assert kwargs["thread_state"] is None


def test_assistant_progress_copy_is_not_persisted():
    service = object.__new__(ChatSessionService)
    messages = service._normalize_messages([
        {
            "id": "assistant-1",
            "role": "assistant",
            "content": "正在拆解问题并规划研究路径...\n\n## 最终结论\n证据充分。",
        }
    ])
    assert messages[0]["content"] == "## 最终结论\n证据充分。"
