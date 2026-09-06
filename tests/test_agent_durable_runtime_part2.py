# -*- coding: utf-8 -*-
"""Durable Agent runtime reliability and isolation contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.chat_session_service import ChatSessionService
from src.agent.langgraph_runtime.executor import _should_trip_tool_circuit
from src.agent.model_runtime import GuardedModelRuntime
from src.agent.terminal_publisher import AgentTerminalPublisher
from src.storage import DatabaseManager
from src.storage.models import AgentCircuitBreaker, AgentRun



"""Focused test slice 2; shared fixtures remain local to this slice."""

@pytest.fixture
def database(tmp_path: Path):
    DatabaseManager.reset_instance()
    manager = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'agent-runtime.db'}")
    try:
        yield manager
    finally:
        DatabaseManager.reset_instance()

def _conversation(database: DatabaseManager, suffix: str = "1") -> str:
    conversation_id = f"conversation-{suffix}"
    database.create_chat_conversation(conversation_id)
    return conversation_id

def _claim(
    database: DatabaseManager,
    conversation_id: str,
    *,
    run_id: str,
    max_active_runs: int = 4,
):
    return database.claim_agent_run(
        run_id=run_id,
        conversation_id=conversation_id,
        request_payload={"messages": [{"role": "user", "content": "test"}]},
        worker_id="worker-a",
        lease_seconds=30,
        max_active_runs=max_active_runs,
    )
def test_terminal_commit_rolls_back_all_published_state_on_error(
    database,
):
    conversation_id = _conversation(database, "terminal")
    _claim(database, conversation_id, run_id="run-terminal")
    with pytest.raises(ValueError, match="quality_projection must be a mapping"):
        database.commit_agent_run_terminal(
            run_id="run-terminal",
            conversation_id=conversation_id,
            status="completed",
            messages=[
                {"id": "u1", "role": "user", "content": "问题"},
                {"id": "a1", "role": "assistant", "content": "答案"},
            ],
            final_text="答案",
            agent_context={"version": "3"},
            trace={"quality_projection": ["invalid"]},
        )

    assert database.get_chat_messages(conversation_id) == []
    assert database.get_agent_run(run_id="run-terminal")["status"] == "running"

    assert database.commit_agent_run_terminal(
        run_id="run-terminal",
        conversation_id=conversation_id,
        status="completed",
        messages=[
            {"id": "u1", "role": "user", "content": "问题"},
            {"id": "a1", "role": "assistant", "content": "答案"},
        ],
        final_text="答案",
        agent_context={"version": "3"},
        trace={
            "quality_projection": {
                "engine": "langgraph_agent_loop",
                "tool_results": [],
                "evidence": [],
            },
            "latest_stage": {"status": "succeeded"},
        },
    )
    assert [message.content for message in database.get_chat_messages(conversation_id)] == ["问题", "答案"]
    assert database.get_agent_run(run_id="run-terminal")["status"] == "completed"

def test_terminal_commit_rejects_a_stale_recovered_worker(database):
    conversation_id = _conversation(database, "terminal-fence")
    _claim(database, conversation_id, run_id="run-terminal-fence")
    with database.session_scope() as session:
        record = session.get(AgentRun, "run-terminal-fence")
        record.lease_expires_at = datetime.now() - timedelta(seconds=1)
    reclaimed = database.reclaim_agent_run(
        "run-terminal-fence",
        worker_id="worker-b",
        lease_seconds=30,
    )
    assert reclaimed["attempt"] == 2

    stale_commit = database.commit_agent_run_terminal(
        run_id="run-terminal-fence",
        conversation_id=conversation_id,
        status="completed",
        messages=[{"id": "a1", "role": "assistant", "content": "stale"}],
        final_text="stale",
        agent_context=None,
        worker_id="worker-a",
        attempt=1,
    )
    current_commit = database.commit_agent_run_terminal(
        run_id="run-terminal-fence",
        conversation_id=conversation_id,
        status="completed",
        messages=[{"id": "a2", "role": "assistant", "content": "current"}],
        final_text="current",
        agent_context=None,
        worker_id="worker-b",
        attempt=2,
    )

    assert stale_commit is False
    assert current_commit is True
    assert [message.content for message in database.get_chat_messages(conversation_id)] == ["current"]

def test_resource_slots_rate_limit_and_budget_are_shared(database):
    conversation_id = _conversation(database, "resources")
    _claim(database, conversation_id, run_id="run-resources")
    first_lease = database.try_acquire_agent_resource(
        resource_name="provider:test",
        lease_owner="owner-a",
        slots=1,
        lease_seconds=30,
    )
    assert first_lease
    assert (
        database.try_acquire_agent_resource(
            resource_name="provider:test",
            lease_owner="owner-b",
            slots=1,
            lease_seconds=30,
        )
        is None
    )
    assert database.release_agent_resource(
        first_lease,
        lease_owner="owner-a",
    )
    assert database.check_agent_rate_limit("tenant:user", limit=1) == 0
    assert database.check_agent_rate_limit("tenant:user", limit=1) >= 1
    allowed = database.reserve_agent_run_budget(
        "run-resources",
        provider_calls=1,
        max_provider_calls=1,
    )
    denied = database.reserve_agent_run_budget(
        "run-resources",
        provider_calls=1,
        max_provider_calls=1,
    )
    assert allowed["allowed"] is True
    assert denied["allowed"] is False
    assert denied["reason"] == "provider_call_count"


def test_releasing_one_cancelled_run_frees_only_its_resource_slots(database):
    first = database.try_acquire_agent_resource(
        resource_name="provider:cleanup",
        lease_owner="owner-a",
        slots=3,
        lease_seconds=30,
        run_id="run-cancelled",
        step_id="model:1",
    )
    second = database.try_acquire_agent_resource(
        resource_name="provider:cleanup",
        lease_owner="owner-b",
        slots=3,
        lease_seconds=30,
        run_id="run-cancelled",
        step_id="model:2",
    )
    other = database.try_acquire_agent_resource(
        resource_name="provider:cleanup",
        lease_owner="owner-c",
        slots=3,
        lease_seconds=30,
        run_id="run-still-active",
        step_id="model:1",
    )
    assert first and second and other

    assert database.release_agent_resources_for_run("run-cancelled") == 2
    assert database.agent_runtime_metrics()["active_resource_leases"] == 1

    replacement = database.try_acquire_agent_resource(
        resource_name="provider:cleanup",
        lease_owner="owner-d",
        slots=3,
        lease_seconds=30,
        run_id="run-next",
        step_id="model:1",
    )
    assert replacement in {first, second}


def test_runtime_metrics_separate_active_half_open_and_expired_circuits(database):
    active = "tool:active-circuit"
    expired = "tool:expired-circuit"
    half_open = "tool:half-open-circuit"
    database.record_agent_circuit_failure(
        active,
        error="temporary provider timeout",
        failure_threshold=1,
        cooldown_seconds=60,
    )
    database.record_agent_circuit_failure(
        expired,
        error="temporary provider timeout",
        failure_threshold=1,
        cooldown_seconds=60,
    )
    database.record_agent_circuit_failure(
        half_open,
        error="temporary provider timeout",
        failure_threshold=1,
        cooldown_seconds=60,
    )
    with database.session_scope() as session:
        for resource_name in (expired, half_open):
            record = session.get(AgentCircuitBreaker, resource_name)
            assert record is not None
            record.opened_until = datetime.now() - timedelta(seconds=1)

    before_probe = database.agent_runtime_metrics()
    assert before_probe["open_circuits"] == 1
    assert before_probe["half_open_circuits"] == 0
    assert before_probe["expired_circuits"] == 2

    assert database.agent_circuit_before_request(half_open, worker_id="worker-a")["state"] == "half_open"
    after_probe = database.agent_runtime_metrics()
    assert after_probe["open_circuits"] == 1
    assert after_probe["half_open_circuits"] == 1
    assert after_probe["expired_circuits"] == 1


def test_tool_circuit_only_trips_for_transient_source_failures() -> None:
    assert _should_trip_tool_circuit(TimeoutError("source timeout")) is True
    assert _should_trip_tool_circuit(ConnectionError("source disconnected")) is True
    assert _should_trip_tool_circuit(ValueError("invalid arguments")) is False
    assert _should_trip_tool_circuit(PermissionError("approval required")) is False

def test_chat_service_enforces_owner_and_tenant_boundary(database):
    owner_a = ChatSessionService(
        database,
        tenant_id="tenant-a",
        owner_id="alice",
    )
    owner_b = ChatSessionService(
        database,
        tenant_id="tenant-a",
        owner_id="bob",
    )
    tenant_b = ChatSessionService(
        database,
        tenant_id="tenant-b",
        owner_id="alice",
    )
    conversation = owner_a.create_conversation()

    assert owner_a.get_conversation(conversation["id"]) is not None
    assert owner_b.get_conversation(conversation["id"]) is None
    assert tenant_b.get_conversation(conversation["id"]) is None
    assert owner_b.delete_conversation(conversation["id"]) == 0
    assert owner_a.get_conversation(conversation["id"]) is not None

def test_model_runtime_retries_only_transient_start_failures(
    database,
    monkeypatch,
):
    conversation_id = _conversation(database, "model-retry")
    _claim(database, conversation_id, run_id="run-model-retry")
    monkeypatch.setenv("AGENT_PROVIDER_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("AGENT_PROVIDER_RETRY_BACKOFF_SECONDS", "0")
    calls = 0

    async def completion(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary provider timeout")
        return {"choices": [{"message": {"content": "ok"}}]}

    runtime = GuardedModelRuntime(
        database=database,
        run_id="run-model-retry",
        worker_id="worker-a",
        model="test-model",
        token_estimator=lambda messages, model: 10,
    )
    result = asyncio.run(
        runtime.complete(
            completion,
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=20,
        )
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert calls == 2
    assert database.get_agent_run(run_id="run-model-retry")["provider_call_count"] == 2


def test_model_runtime_does_not_open_a_circuit_for_deterministic_provider_errors(
    database,
    monkeypatch,
):
    conversation_id = _conversation(database, "model-deterministic-error")
    _claim(database, conversation_id, run_id="run-model-deterministic-error")
    monkeypatch.setenv("AGENT_PROVIDER_MAX_ATTEMPTS", "3")

    async def completion(**_kwargs):
        raise ValueError("provider rejected an invalid request")

    runtime = GuardedModelRuntime(
        database=database,
        run_id="run-model-deterministic-error",
        worker_id="worker-a",
        model="configured-model",
        token_estimator=lambda messages, model: 10,
    )
    with pytest.raises(ValueError, match="invalid request"):
        asyncio.run(
            runtime.complete(
                completion,
                messages=[{"role": "user", "content": "hello"}],
                max_tokens=20,
            )
        )

    assert database.agent_runtime_metrics()["open_circuits"] == 0


def test_model_runtime_does_not_cancel_slow_provider_reasoning(database, monkeypatch) -> None:
    conversation_id = _conversation(database, "model-unbounded")
    _claim(database, conversation_id, run_id="run-model-unbounded")
    monkeypatch.setenv("AGENT_PROVIDER_MAX_ATTEMPTS", "1")
    calls = 0
    completed = False

    async def completion(**_kwargs):
        nonlocal calls, completed
        calls += 1
        await asyncio.sleep(0.03)
        completed = True
        return {"choices": [{"message": {"content": "ok"}}]}

    runtime = GuardedModelRuntime(
        database=database,
        run_id="run-model-unbounded",
        worker_id="worker-a",
        model="test-model",
        token_estimator=lambda messages, model: 10,
    )
    result = asyncio.run(
        runtime.complete(
            completion,
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=20,
        )
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert completed is True
    assert calls == 1
    assert database.get_agent_run(run_id="run-model-unbounded")["provider_call_count"] == 1
    assert database.agent_runtime_metrics()["active_resource_leases"] == 0


def test_provider_token_estimate_is_telemetry_not_a_completion_gate(database, monkeypatch) -> None:
    conversation_id = _conversation(database, "model-accounting-only")
    _claim(database, conversation_id, run_id="run-model-accounting-only")
    monkeypatch.setenv("AGENT_PROVIDER_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AGENT_MAX_ESTIMATED_TOKENS", "1")
    monkeypatch.setenv("AGENT_MAX_ESTIMATED_COST_MICROS", "1")
    reservation_kwargs = []
    original_reserve = database.reserve_agent_run_budget

    def reserve(*args, **kwargs):
        reservation_kwargs.append(dict(kwargs))
        return original_reserve(*args, **kwargs)

    monkeypatch.setattr(database, "reserve_agent_run_budget", reserve)

    async def completion(**_kwargs):
        return {"choices": [{"message": {"content": "ok"}}]}

    runtime = GuardedModelRuntime(
        database=database,
        run_id="run-model-accounting-only",
        worker_id="worker-a",
        model="test-model",
        token_estimator=lambda messages, model: 10_000,
    )
    result = asyncio.run(
        runtime.complete(
            completion,
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=20,
        )
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert reservation_kwargs == [{
        "provider_calls": 1,
        "estimated_tokens": 10_020,
        "estimated_cost_micros": 50_100,
    }]
    assert database.get_agent_run(run_id="run-model-accounting-only")["estimated_token_count"] == 10_020


def test_model_stream_close_releases_the_shared_provider_slot(
    database,
    monkeypatch,
):
    conversation_id = _conversation(database, "model-stream-close")
    _claim(database, conversation_id, run_id="run-model-stream-close")
    monkeypatch.setenv("AGENT_PROVIDER_MAX_ATTEMPTS", "1")

    class Stream:
        closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self):
            self.closed = True

    source = Stream()

    async def completion(**_kwargs):
        return source

    async def run():
        runtime = GuardedModelRuntime(
            database=database,
            run_id="run-model-stream-close",
            worker_id="worker-a",
            model="test-model",
            token_estimator=lambda messages, model: 10,
        )
        managed = await runtime.complete(
            completion,
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=20,
            stream=True,
        )
        await managed.aclose()

    asyncio.run(run())

    assert source.closed is True
    assert database.agent_runtime_metrics()["active_resource_leases"] == 0
