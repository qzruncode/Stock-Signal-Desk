"""Canonical history, native checkpoints and durable admission must agree."""

import asyncio
import json
from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from langchain_core.messages import AIMessage

from api.v1.endpoints.agent import approvals, chat_recovery, chat_route_start, conversations
from api.v1.endpoints.agent.chat_background_runner import _graph_history_mode
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.agent.resource_scheduler import agent_conversation_lease
from src.agent.run_registry import ActiveRun, ActiveRunRegistry, RunBroadcaster
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager
from tests.test_langgraph_agent_runtime import (
    BlockingAtomicExecutor,
    ScriptedChatModel,
    _registry,
    _search_operation,
    _tool_call,
)


CONVERSATION_ID = "history-lifecycle"


@pytest.fixture
def database(tmp_path):
    DatabaseManager.reset_instance()
    database = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'history.db'}")
    database.create_chat_conversation(CONVERSATION_ID)
    try:
        yield database
    finally:
        DatabaseManager.reset_instance()


@pytest.fixture
def registry(database, monkeypatch):
    registry = ActiveRunRegistry(database, worker_id="request-worker")
    for module in (chat_route_start, conversations, approvals, chat_recovery):
        monkeypatch.setattr(module, "active_run_registry", registry)
    monkeypatch.setattr(
        chat_route_start.agent_request_rate_limiter, "check_and_record", lambda *_args, **_kwargs: 0,
    )
    return registry


def _request(body=None):
    async def receive():
        return {"type": "http.request", "body": json.dumps(body or {}).encode(), "more_body": False}

    return Request({
        "type": "http", "method": "POST", "path": "/api/v1/agent/chat",
        "headers": [], "client": ("127.0.0.1", 8080),
    }, receive)


async def _invoke_graph(manager, model, messages, *, run_id, history_mode="auto", executor=None):
    return await manager.run_new(
        messages=messages,
        user_text=next(message["content"] for message in reversed(messages) if message["role"] == "user"),
        system_prompt="",
        llm_config={}, database=None, controller=None,
        run_id=run_id, conversation_id=CONVERSATION_ID, run_attempt=1,
        tenant_id="local", owner_id="admin", model=model, executor=executor,
        history_mode=history_mode,
    )


@pytest.mark.parametrize("action", ["reload", "edit", "parent_branch", "follow_up"])
def test_server_request_resolves_the_same_branch_in_storage_and_native_graph(database, registry, action):
    async def scenario():
        service = ChatSessionService(database)
        original = [
            {"id": "u1", "role": "user", "content": "ORIGINAL_QUESTION"},
            {"id": "a1", "role": "assistant", "content": "ORIGINAL_ANSWER"},
            {"id": "u2", "role": "user", "content": "LATER_QUESTION"},
            {"id": "a2", "role": "assistant", "content": "LATER_ANSWER"},
        ]
        service.save_conversation_snapshot(CONVERSATION_ID, original)
        # This is the real metadata-only contract that the former helper test missed.
        assert "messages" not in service.ensure_conversation(CONVERSATION_ID)
        manager = LangGraphRuntimeManager(registry=_registry(), response_format=None)
        await manager.start(testing=True)
        try:
            await _invoke_graph(
                manager, ScriptedChatModel(responses=[AIMessage(content="LATER_ANSWER", id="a2")]),
                original[:-1], run_id="original-run",
            )
            incoming = {"id": "u3", "role": "user", "content": "NEW_QUESTION"}
            body = {"conversation_id": CONVERSATION_ID, "history_mode": "server"}
            expected_ids = ["u3"]
            if action == "reload":
                incoming = original[0]
                expected_ids = ["u1"]
            elif action == "edit":
                body["runConfig"] = {"custom": {"editMessageId": "u1"}}
            elif action == "parent_branch":
                body["history_parent_id"] = "a1"
                expected_ids = ["u1", "a1", "u3"]
            else:
                body["history_parent_id"] = "a2"
                expected_ids = ["u1", "a1", "u2", "a2", "u3"]
            body["messages"] = [incoming]
            model = ScriptedChatModel(responses=[AIMessage(content="NEW_ANSWER")])
            modes = []

            async def background_runner(**kwargs):
                mode = _graph_history_mode(kwargs["body"])
                modes.append(mode)
                result = await _invoke_graph(
                    manager, model, kwargs["messages"], run_id=kwargs["run"].run_id, history_mode=mode,
                )
                await registry.mark_done(CONVERSATION_ID, result.status, final_text=result.final_text)

            response = await chat_route_start.agent_chat_impl(
                _request(body), database, background_runner=background_runner,
                config_loader=lambda: {"model": "test"},
            )
            assert response.status_code == 200
            await asyncio.wait_for(registry.get(CONVERSATION_ID).task, timeout=5)
            assert modes == ["continue" if action == "follow_up" else "replace"]
            assert [message["id"] for message in service.get_conversation(CONVERSATION_ID)["messages"]] == expected_ids
            assert [message.id for message in model.calls[0] if message.type != "system"] == expected_ids
            durable = database.get_agent_run(conversation_id=CONVERSATION_ID)
            assert [message["id"] for message in durable["request"]["messages"]] == expected_ids
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("retain_message", [False, True])
def test_snapshot_waits_for_remote_writer_and_excludes_all_admission_paths(
    database, registry, monkeypatch, retain_message,
):
    async def scenario():
        service = ChatSessionService(database)
        original = [{"id": "removed", "role": "user", "content": "REMOVED_QUESTION"}]
        retained = [{"id": "retained", "role": "user", "content": "RETAINED_QUESTION"}] if retain_message else []
        service.save_conversation_snapshot(CONVERSATION_ID, original)
        worker = ActiveRunRegistry(database, worker_id="remote-worker")
        run = await worker.try_claim(CONVERSATION_ID)
        # An unrelated retained local terminal handle must not short-circuit waiting.
        registry._runs[CONVERSATION_ID] = ActiveRun(
            conversation_id=CONVERSATION_ID, broadcaster=RunBroadcaster(), status="completed",
        )
        manager = LangGraphRuntimeManager(registry=_registry(_search_operation()), response_format=None)
        executor = BlockingAtomicExecutor()
        cancelled = asyncio.Event()
        allow_terminal = asyncio.Event()
        await manager.start(testing=True)
        monkeypatch.setattr(conversations, "agent_graph_runtime", manager)

        async def remote_writer():
            try:
                await _invoke_graph(
                    manager, ScriptedChatModel(responses=[_tool_call("blocked", "primary")]),
                    original, run_id=run.run_id, executor=executor,
                )
            except asyncio.CancelledError:
                cancelled.set()
                await allow_terminal.wait()
                # The real cancellation path also publishes its transcript before terminal status.
                service.save_conversation_snapshot(CONVERSATION_ID, original)
                await worker.mark_done(CONVERSATION_ID, "cancelled", error="cancelled")
                raise

        async def factory(_broadcaster):
            return asyncio.create_task(remote_writer())

        snapshot_task = None
        try:
            await run.start(factory)
            await asyncio.wait_for(executor.started.wait(), timeout=5)
            snapshot_task = asyncio.create_task(conversations.sync_agent_conversation_snapshot(
                CONVERSATION_ID, _request(), {"messages": retained, "prune_agent_context_to_messages": True}, database,
            ))
            await asyncio.wait_for(cancelled.wait(), timeout=5)
            assert not snapshot_task.done()
            assert service.get_conversation(CONVERSATION_ID)["messages"][0]["id"] == "removed"
            checkpoint = await manager.graph.aget_state(manager.graph_config(CONVERSATION_ID))
            assert checkpoint.values["messages"][0].id == "removed"

            new_runner = AsyncMock()
            with pytest.raises(HTTPException) as new_request:
                await chat_route_start.agent_chat_impl(
                    _request({"conversation_id": CONVERSATION_ID, "history_mode": "server", "messages": original}),
                    database, background_runner=new_runner, config_loader=lambda: {"model": "test"},
                )
            assert new_request.value.status_code == 409
            assert new_request.value.detail["error"] == "conversation_transition_in_progress"
            new_runner.assert_not_awaited()

            with pytest.raises(HTTPException) as decision:
                await approvals.decide_agent_interrupt(
                    CONVERSATION_ID, "interrupt",
                    approvals.InterruptDecisionRequest(run_id=run.run_id, fingerprint="a" * 32, decision="approve"),
                    _request(), database,
                )
            assert decision.value.status_code == 409
            assert decision.value.detail["error"] == "conversation_transition_in_progress"

            # Even a stale recovery scan cannot claim while a history mutation owns the slot.
            monkeypatch.setattr(database, "list_recoverable_agent_runs", lambda **_kwargs: [{
                "run_id": run.run_id, "conversation_id": CONVERSATION_ID,
                "request": {"engine": "langgraph_agent_loop"},
            }])
            reclaim = MagicMock()
            monkeypatch.setattr(database, "reclaim_agent_run", reclaim)
            assert await chat_recovery.recover_interrupted_agent_runs(
                database, config_loader=lambda: {"model": "test"}, background_runner=new_runner,
            ) == 0
            reclaim.assert_not_called()

            allow_terminal.set()
            result = await asyncio.wait_for(snapshot_task, timeout=5)
            assert [message["id"] for message in result["messages"]] == [message["id"] for message in retained]
            assert run.task.done()
            checkpoint = await manager.graph.aget_state(manager.graph_config(CONVERSATION_ID))
            assert [message.id for message in checkpoint.values["messages"]] == [message["id"] for message in retained]
            model = ScriptedChatModel(responses=[AIMessage(content="新回答")])
            await _invoke_graph(
                manager, model, [*retained, {"id": "new", "role": "user", "content": "NEW_QUESTION"}],
                run_id="next-run",
            )
            assert all("REMOVED_QUESTION" not in str(message.content) for message in model.calls[0])
        finally:
            allow_terminal.set()
            for task in (snapshot_task, run.task, run.lease_task):
                if task is not None:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())


def test_snapshot_timeout_keeps_history_and_checkpoint_intact(database, registry, monkeypatch):
    async def scenario():
        service = ChatSessionService(database)
        messages = [{"id": "original", "role": "user", "content": "KEEP_HISTORY"}]
        service.save_conversation_snapshot(CONVERSATION_ID, messages)
        database.claim_agent_run(
            run_id="remote-run", conversation_id=CONVERSATION_ID, request_payload={},
            worker_id="unresponsive-worker", lease_seconds=30, max_active_runs=4,
        )
        stop = conversations._cancel_conversation_run_before_history_change
        monkeypatch.setattr(conversations, "_cancel_conversation_run_before_history_change", partial(stop, timeout_seconds=0))
        replace_checkpoint = AsyncMock()
        monkeypatch.setattr(conversations.agent_graph_runtime, "replace_checkpoint_messages", replace_checkpoint)
        with pytest.raises(HTTPException) as error:
            await conversations.sync_agent_conversation_snapshot(
                CONVERSATION_ID, _request(), {"messages": [], "prune_agent_context_to_messages": True}, database,
            )
        assert error.value.status_code == 409
        assert service.get_conversation(CONVERSATION_ID)["messages"][0]["id"] == "original"
        replace_checkpoint.assert_not_awaited()
        # Error paths release the same lease, so retries do not deadlock.
        async with agent_conversation_lease(database, CONVERSATION_ID):
            pass

    asyncio.run(scenario())


def test_checkpoint_failure_does_not_publish_a_successful_history_deletion(database, registry, monkeypatch):
    async def scenario():
        service = ChatSessionService(database)
        original = [{"id": "original", "role": "user", "content": "KEEP_HISTORY"}]
        service.save_conversation_snapshot(CONVERSATION_ID, original)
        manager = LangGraphRuntimeManager(registry=_registry(), response_format=None)
        await manager.start(testing=True)
        monkeypatch.setattr(conversations, "agent_graph_runtime", manager)
        monkeypatch.setattr(manager, "replace_checkpoint_messages", AsyncMock(side_effect=RuntimeError("checkpoint unavailable")))
        try:
            with pytest.raises(RuntimeError, match="checkpoint unavailable"):
                await conversations.sync_agent_conversation_snapshot(
                    CONVERSATION_ID, _request(), {"messages": [], "prune_agent_context_to_messages": True}, database,
                )
            assert service.get_conversation(CONVERSATION_ID)["messages"][0]["id"] == "original"
            async with agent_conversation_lease(database, CONVERSATION_ID):
                pass
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_stage", ["snapshot", "start"])
def test_failed_admission_releases_the_transition_and_allows_retry(database, registry, failure_stage):
    async def scenario():
        body = {
            "conversation_id": CONVERSATION_ID, "history_mode": "server",
            "messages": [{"id": "new", "role": "user", "content": "NEW_QUESTION"}],
        }
        async def runner(**_kwargs):
            await registry.mark_done(CONVERSATION_ID, "completed", final_text="done")

        target = (
            patch.object(ChatSessionService, "save_conversation_snapshot", side_effect=RuntimeError("storage unavailable"))
            if failure_stage == "snapshot"
            else patch.object(ActiveRun, "start", AsyncMock(side_effect=RuntimeError("start unavailable")))
        )
        with target, pytest.raises(HTTPException) as error:
            await chat_route_start.agent_chat_impl(
                _request(body), database, background_runner=runner, config_loader=lambda: {"model": "test"},
            )
        assert error.value.status_code == 500
        assert database.get_agent_run(conversation_id=CONVERSATION_ID)["status"] == "failed"

        response = await chat_route_start.agent_chat_impl(
            _request(body), database, background_runner=runner, config_loader=lambda: {"model": "test"},
        )
        assert response.status_code == 200
        await asyncio.wait_for(registry.get(CONVERSATION_ID).task, timeout=5)
        assert database.get_agent_run(conversation_id=CONVERSATION_ID)["status"] == "completed"

    asyncio.run(scenario())
