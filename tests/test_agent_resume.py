# -*- coding: utf-8 -*-
"""Agent resume 端点 + 续流补齐集成测试。

覆盖:
- /agent/chat/resume 无活跃 run 时返回 {active: false}
- 有活跃 run 时返回 data-stream,且补齐已生成文本 + 实时增量
- /agent/chat 在已有活跃 run 时返回 409 run_in_progress
- getConversation 附加 is_generating
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.agent import chat as chat_mod
from src.agent.run_registry import ActiveRun, RunBroadcaster, active_run_registry
import src.auth as auth


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


@pytest.fixture(autouse=True)
def isolate_registry():
    """每个测试清空模块级 registry,避免跨测试残留。"""
    active_run_registry._runs.clear()
    yield
    for run in list(active_run_registry._runs.values()):
        if run.task is not None and not run.task.done():
            run.task.cancel()
    active_run_registry._runs.clear()


def _chunk(content):
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = None
    c = MagicMock()
    c.choices = [MagicMock(delta=delta)]
    return c


class _AsyncChunkStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


def _slow_async_completion(chunks, delay=0.2):
    """每个 chunk 之间延迟,模拟流式生成(给续流留出窗口)。"""
    async def _fake(**kwargs):
        async def _delayed():
            for c in chunks:
                await asyncio.sleep(delay)
                yield c
        return _delayed()
    return _fake


async def _emit_kline_tool_chunks(broadcaster: RunBroadcaster) -> None:
    tool = await broadcaster.add_tool_call("get_kline", "call_kline")
    tool.append_args_text('{"symbol":"601318","count":60}')
    tool.set_response({
        "symbol": "601318",
        "name": "中国平安",
        "recent": [],
        "latest": {"close": 49.1},
    })


def test_resume_returns_inactive_when_no_run(client):
    """无活跃 run → {active: false}。"""
    resp = client.post("/api/v1/agent/chat/resume", json={"conversation_id": "no-such-run"})
    assert resp.status_code == 200
    assert resp.json() == {"active": False}


def test_chat_returns_409_when_run_in_progress(client):
    """同一对话已有活跃 run → 409 run_in_progress。

    用真实 running ActiveRun 占位(而非 mock is_active):409 判定现由 try_claim
    在锁内基于 registry 真实状态做出,mock is_active 已无法触发该分支。conversation_id
    必须用真实创建的对话 id(agent_chat 的 ensure_conversation 会把不存在的 id 换成新 uuid,
    导致 try_claim 查不到占位的 run)。
    """
    created = client.post("/api/v1/agent/conversations").json()
    cid = created["id"]
    active_run_registry._runs[cid] = ActiveRun(
        conversation_id=cid,
        broadcaster=RunBroadcaster(),
        status="running",
    )
    with patch("api.v1.endpoints.agent.chat._get_llm_config",
               return_value={"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}):
        resp = client.post("/api/v1/agent/chat", json={
            "messages": [{"role": "user", "content": "hi"}],
            "conversation_id": cid,
        })
    assert resp.status_code == 409
    assert resp.json()["error"] == "run_in_progress"


def test_chat_resume_existing_replays_retained_run(client):
    """resume_existing 通过 /agent/chat 重放既有 data-stream,包含工具 chunk。"""
    created = client.post("/api/v1/agent/conversations").json()
    cid = created["id"]
    broadcaster = RunBroadcaster()
    broadcaster.append_text("已生成内容")
    asyncio.run(_emit_kline_tool_chunks(broadcaster))
    broadcaster.mark_finished()
    active_run_registry._runs[cid] = ActiveRun(
        conversation_id=cid,
        broadcaster=broadcaster,
        status="completed",
    )

    with patch("api.v1.endpoints.agent.chat._get_llm_config",
               return_value={"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}):
        with client.stream("POST", "/api/v1/agent/chat", json={
            "messages": [{"role": "user", "content": "hi"}],
            "conversation_id": cid,
            "resume_existing": True,
            "after_chunk_index": 0,
        }) as resp:
            body = b"".join(resp.iter_bytes()).decode("utf-8")

    assert resp.status_code == 200
    first_line = body.strip().splitlines()[0]
    assert json.loads(first_line.removeprefix("0:")) == "已生成内容"
    assert "get_kline" in body
    assert "call_kline" in body
    assert "601318" in body


def test_chat_resume_existing_without_run_returns_409(client):
    """resume_existing 找不到保留 run 时返回 409,避免误开第二轮生成。"""
    created = client.post("/api/v1/agent/conversations").json()
    cid = created["id"]

    with patch("api.v1.endpoints.agent.chat._get_llm_config",
               return_value={"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}), \
         patch("api.v1.endpoints.agent.chat.litellm") as llm:
        resp = client.post("/api/v1/agent/chat", json={
            "messages": [{"role": "user", "content": "hi"}],
            "conversation_id": cid,
            "resume_existing": True,
            "after_chunk_index": 0,
        })

    assert resp.status_code == 409
    assert resp.json()["error"] == "run_not_active"
    llm.acompletion.assert_not_called()


def test_get_conversation_includes_is_generating(client):
    """getConversation 返回运行态;completed retained run 也可 resume replay。"""
    created = client.post("/api/v1/agent/conversations").json()
    cid = created["id"]
    detail = client.get(f"/api/v1/agent/conversations/{cid}").json()
    assert "is_generating" in detail
    assert detail["is_generating"] is False
    assert detail["resume_state"]["active"] is False

    # mock 有活跃 run → is_generating=True
    with patch.object(active_run_registry, "is_active", return_value=True):
        detail2 = client.get(f"/api/v1/agent/conversations/{cid}").json()
    assert detail2["is_generating"] is True

    broadcaster = RunBroadcaster()
    broadcaster.append_text("done")
    broadcaster.mark_finished()
    active_run_registry._runs[cid] = ActiveRun(
        conversation_id=cid,
        broadcaster=broadcaster,
        status="completed",
    )
    detail3 = client.get(f"/api/v1/agent/conversations/{cid}").json()
    assert detail3["is_generating"] is False
    assert detail3["resume_state"]["active"] is True
    assert detail3["resume_state"]["status"] == "completed"
    assert detail3["resume_state"]["has_tool_events"] is False

    asyncio.run(_emit_kline_tool_chunks(broadcaster))
    detail4 = client.get(f"/api/v1/agent/conversations/{cid}").json()
    assert detail4["resume_state"]["has_tool_events"] is True


def test_history_persisted_at_generation_start(client):
    """生成开始时即落库本次 messages(含 user),而非等生成完成。

    这是 resume 能恢复"完整对话 + 续流"的前提:刷新后 getConversation 拿得到
    本次 user 消息。验证方式:监控 save_conversation_snapshot,断言生成开始时
    被调用且传入的 messages 含本次 user。
    """
    created = client.post("/api/v1/agent/conversations").json()
    cid = created["id"]
    user_msg = {"role": "user", "content": "这是本次新消息"}

    snapshot_calls: list = []

    real_snapshot = chat_mod.ChatSessionService.save_conversation_snapshot
    plan = chat_mod.TaskPlan.model_validate({
        "tasks": [{
            "task_id": "answer",
            "kind": "general_response",
            "objective": "回复用户",
            "entity_scope": "none",
            "entities": [],
            "parameters": {},
            "depends_on": [],
            "output_requirements": [],
            "confirmation": "not_required",
            "confidence": 1.0,
        }],
        "needs_clarification": False,
        "clarification_question": None,
    })

    def spy_snapshot(self, conversation_id, messages, thread_state=None, skip_title=False):
        snapshot_calls.append({
            "conversation_id": conversation_id,
            "messages": list(messages) if messages else [],
            "skip_title": skip_title,
        })
        return real_snapshot(self, conversation_id, messages, thread_state) \
            if thread_state else real_snapshot(self, conversation_id, messages)

    with patch("api.v1.endpoints.agent.chat._get_llm_config",
               return_value={"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}), \
         patch(
             "api.v1.endpoints.agent.chat.resolve_task_plan",
             new=AsyncMock(return_value=plan),
         ), \
         patch("api.v1.endpoints.agent.chat.litellm") as llm, \
         patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()), \
         patch("src.services.agent_prompt_service.AgentPromptService") as PS, \
         patch.object(chat_mod.ChatSessionService, "save_conversation_snapshot", spy_snapshot):
        PS.return_value.get_active_system_prompt.return_value = ("sys", False)
        llm.acompletion = _slow_async_completion([_chunk("回复")], delay=0.01)
        with client.stream("POST", "/api/v1/agent/chat",
                            json={"messages": [user_msg], "conversation_id": cid}) as resp:
            body = b""
            for chunk in resp.iter_bytes():
                body += chunk

    # 断言:生成开始时(skip_title=True)落了一次,且 messages 含本次 user
    start_calls = [c for c in snapshot_calls if c["skip_title"]]
    assert len(start_calls) >= 1, f"生成开始时未落库 snapshot: {snapshot_calls}"
    start_msg_contents = [m.get("content") for c in start_calls for m in c["messages"]]
    assert "这是本次新消息" in start_msg_contents, f"本次 user 未在生成开始时落库: {start_msg_contents}"

    # 且生成完成后 getConversation 能拿到完整历史(含 user + assistant 回复)
    detail = client.get(f"/api/v1/agent/conversations/{cid}").json()
    contents = [m["content"] for m in detail["messages"]]
    assert "这是本次新消息" in contents
    assert any("回复" in c for c in contents)
