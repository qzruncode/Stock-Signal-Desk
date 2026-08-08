# -*- coding: utf-8 -*-
"""Recovery behavior around the first durable LangGraph checkpoint."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from api.v1.endpoints.agent import chat_recovery
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager


class _EmptyCheckpointGraph:
    async def aget_state(self, _config):
        return SimpleNamespace(config={"configurable": {"thread_id": "agent-v2:conversation"}}, values={})


class _CommittedCheckpointGraph:
    async def aget_state(self, _config):
        return SimpleNamespace(
            config={"configurable": {"thread_id": "agent-v2:conversation"}},
            values={"run_id": "run-1"},
        )


def test_has_checkpoint_requires_committed_state_for_the_same_run() -> None:
    manager = LangGraphRuntimeManager()
    manager.graph = _EmptyCheckpointGraph()
    assert asyncio.run(manager.has_checkpoint("conversation", run_id="run-1")) is False

    manager.graph = _CommittedCheckpointGraph()
    assert asyncio.run(manager.has_checkpoint("conversation", run_id="other-run")) is False
    assert asyncio.run(manager.has_checkpoint("conversation", run_id="run-1")) is True


def test_recovery_restarts_a_new_engine_request_when_first_checkpoint_is_missing(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Database:
        def list_recoverable_agent_runs(self, *, limit):
            assert limit == 20
            return [{
                "run_id": "run-1",
                "conversation_id": "conversation-1",
                "request": {
                    "engine": "langgraph_agent_loop",
                    "messages": [{"role": "user", "content": "重新开始"}],
                    "body": {"conversation_id": "conversation-1"},
                },
            }]

        def reclaim_agent_run(self, run_id, *, worker_id):
            assert run_id == "run-1"
            assert worker_id == "worker-test"
            return {"run_id": run_id, "attempt": 2, "tenant_id": "local", "owner_id": "admin"}

        def finish_agent_run(self, *_args, **_kwargs):
            raise AssertionError("an uncheckpointed new-engine run must restart, not fail")

    class Run:
        async def start(self, factory):
            task = await factory(object())
            await task

    class Registry:
        worker_id = "worker-test"

        def configure(self, _database):
            return None

        async def adopt_recovered(self, **kwargs):
            captured["adopt"] = kwargs
            return Run()

    class Runtime:
        async def has_checkpoint(self, conversation_id, *, run_id):
            captured["checkpoint"] = (conversation_id, run_id)
            return False

    async def background_runner(**kwargs):
        captured["recovery"] = kwargs["recovery"]

    monkeypatch.setattr(chat_recovery, "active_run_registry", Registry())
    monkeypatch.setattr(chat_recovery, "agent_graph_runtime", Runtime())

    recovered = asyncio.run(
        chat_recovery.recover_interrupted_agent_runs(
            Database(),
            config_loader=lambda: {"model": "test"},
            background_runner=background_runner,
        )
    )

    assert recovered == 1
    assert captured["checkpoint"] == ("conversation-1", "run-1")
    assert captured["recovery"] is False


def test_recovery_restarts_an_uncheckpointed_retained_request(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Database:
        def list_recoverable_agent_runs(self, *, limit):
            assert limit == 20
            return [{
                "run_id": "retry-run-2",
                "conversation_id": "conversation-1",
                "request": {
                    "engine": "langgraph_agent_loop",
                    "messages": [{"role": "user", "content": "原始问题"}],
                    "body": {"conversation_id": "conversation-1"},
                },
            }]

        def reclaim_agent_run(self, run_id, *, worker_id):
            assert run_id == "retry-run-2"
            assert worker_id == "worker-test"
            return {"run_id": run_id, "attempt": 2, "tenant_id": "local", "owner_id": "admin"}

    class Run:
        async def start(self, factory):
            task = await factory(object())
            await task

    class Registry:
        worker_id = "worker-test"

        def configure(self, _database):
            return None

        async def adopt_recovered(self, **kwargs):
            captured["adopt"] = kwargs
            return Run()

    class Runtime:
        async def has_checkpoint(self, conversation_id, *, run_id):
            captured["checkpoint"] = (conversation_id, run_id)
            return False

    async def background_runner(**kwargs):
        captured["recovery"] = kwargs["recovery"]

    monkeypatch.setattr(chat_recovery, "active_run_registry", Registry())
    monkeypatch.setattr(chat_recovery, "agent_graph_runtime", Runtime())

    recovered = asyncio.run(
        chat_recovery.recover_interrupted_agent_runs(
            Database(),
            config_loader=lambda: {"model": "test"},
            background_runner=background_runner,
        )
    )

    assert recovered == 1
    assert captured["checkpoint"] == ("conversation-1", "retry-run-2")
    assert captured["recovery"] is False
