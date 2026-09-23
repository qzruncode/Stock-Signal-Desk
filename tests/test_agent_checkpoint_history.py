# -*- coding: utf-8 -*-
"""Read-only LangGraph checkpoint history contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.agent.chat_background_runner import _agent_mode, _graph_history_mode
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
import src.auth as auth


def _snapshot(
    checkpoint_id: str,
    parent_checkpoint_id: str | None,
    *,
    status: str,
    step: int,
):
    return SimpleNamespace(
        config={"configurable": {"thread_id": "agent-v2:conversation", "checkpoint_id": checkpoint_id}},
        parent_config=(
            {"configurable": {"thread_id": "agent-v2:conversation", "checkpoint_id": parent_checkpoint_id}}
            if parent_checkpoint_id
            else None
        ),
        created_at="2026-08-12T10:00:00+08:00",
        metadata={"source": "loop", "step": step, "writes": {"status": status}},
        next=("model",) if status == "running" else (),
        tasks=(SimpleNamespace(id=f"task-{step}", name="model", interrupts=(), error=None),),
        values={
            "run_id": "run-1",
            "conversation_id": "conversation",
            "status": status,
            "messages": [{"role": "user", "content": "问题"}],
            "tool_results": [{"tool_name": "read_source"}],
            "evidence": [{"evidence_id": "ev-1"}],
            "model_turn_count": step,
            "tool_call_count": step - 1,
            "evidence_repair_count": 0,
            "pending_interrupt": None,
            "answer_final": "" if status == "running" else "答案",
        },
    )


class _HistoryGraph:
    def __init__(self) -> None:
        self.history_calls: list[dict] = []

    async def aget_state_history(self, config, *, before=None, limit=None):
        self.history_calls.append({"config": config, "before": before, "limit": limit})
        for snapshot in (
            _snapshot("cp-3", "cp-2", status="completed", step=3),
            _snapshot("cp-2", "cp-1", status="running", step=2),
            _snapshot("cp-1", None, status="running", step=1),
        ):
            yield snapshot

    async def aget_state(self, _config):
        return _snapshot("cp-3", "cp-2", status="completed", step=3)


def test_get_state_history_is_bounded_and_read_only() -> None:
    manager = LangGraphRuntimeManager()
    graph = _HistoryGraph()
    manager.graph = graph

    result = asyncio.run(manager.get_state_history("conversation", limit=2))

    assert result["read_only"] is True
    assert result["checkpoint_authority"] == "langgraph_checkpointer"
    assert result["run_lifecycle_authority"] == "agent_runs"
    assert result["current_checkpoint_id"] == "cp-3"
    assert [item["checkpoint_id"] for item in result["items"]] == ["cp-3", "cp-2"]
    assert result["has_more"] is True
    assert result["next_before_checkpoint_id"] == "cp-2"
    assert result["items"][0]["state"]["tool_result_count"] == 1
    assert "tool_results" not in result["items"][0]["state"]
    assert graph.history_calls[0]["limit"] == 3


def test_get_state_history_passes_a_thread_scoped_before_cursor() -> None:
    manager = LangGraphRuntimeManager()
    graph = _HistoryGraph()
    manager.graph = graph

    asyncio.run(
        manager.get_state_history(
            "conversation",
            limit=1,
            before_checkpoint_id="cp-2",
        )
    )

    call = graph.history_calls[0]
    assert call["before"]["configurable"]["thread_id"] == "agent-v2:conversation"
    assert call["before"]["configurable"]["checkpoint_id"] == "cp-2"


def test_checkpoint_history_endpoint_is_read_only_and_owner_scoped() -> None:
    service = MagicMock()
    service.get_conversation.return_value = {"id": "conversation"}
    history = {
        "conversation_id": "conversation",
        "thread_id": "agent-v2:conversation",
        "checkpoint_authority": "langgraph_checkpointer",
        "run_lifecycle_authority": "agent_runs",
        "read_only": True,
        "items": [],
        "has_more": False,
        "next_before_checkpoint_id": None,
    }
    auth._auth_enabled = None
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
        patch("api.v1.endpoints.agent.checkpoints.ChatSessionService", return_value=service),
        patch(
            "api.v1.endpoints.agent.checkpoints.agent_graph_runtime.get_state_history",
            new=AsyncMock(return_value=history),
        ) as get_history,
    ):
        with TestClient(create_app()) as client:
            response = client.get(
                "/api/v1/agent/conversations/conversation/checkpoints",
                params={"limit": 7, "before_checkpoint_id": "cp-2"},
            )

    auth._auth_enabled = None
    assert response.status_code == 200
    assert response.json()["read_only"] is True
    get_history.assert_awaited_once_with(
        "conversation",
        limit=7,
        before_checkpoint_id="cp-2",
    )
    service.get_conversation.assert_called_once_with("conversation")


def test_server_follow_up_continues_checkpoint_unless_it_is_an_edit() -> None:
    assert _graph_history_mode(
        {"history_mode": "server", "history_parent_id": "assistant-1"}
    ) == "continue"
    assert _graph_history_mode(
        {
            "history_mode": "server",
            "history_parent_id": "assistant-1",
            "runConfig": {"custom": {"editMessageId": "user-1"}},
        }
    ) == "replace"
    assert _graph_history_mode({"history_mode": "branch"}) == "replace"
    assert _graph_history_mode({}) == "auto"


def test_agent_mode_is_the_five_mode_product_contract() -> None:
    assert _agent_mode({"agent_mode": "auto"}) == "auto"
    assert _agent_mode({"agent_mode": "direct"}) == "direct"
    assert _agent_mode({"agent_mode": "plan"}) == "plan"
    assert _agent_mode({"agent_mode": "team"}) == "team"
    assert _agent_mode({"agent_mode": "goal"}) == "goal"
    assert _agent_mode({}) == "auto"
