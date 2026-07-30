# -*- coding: utf-8 -*-
"""Durable Agent runtime reliability and isolation contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.services.chat_session_service import ChatSessionService
from src.agent.model_runtime import GuardedModelRuntime
from src.storage import DatabaseManager
from src.storage.models import AgentRun


@pytest.fixture
def database(tmp_path: Path):
    DatabaseManager.reset_instance()
    manager = DatabaseManager(
        db_url=f"sqlite:///{tmp_path / 'agent-runtime.db'}"
    )
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


def test_cross_thread_admission_enforces_one_global_slot(database):
    first = _conversation(database, "capacity-a")
    second = _conversation(database, "capacity-b")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda item: _claim(
                database,
                item[0],
                run_id=item[1],
                max_active_runs=1,
            ),
            [(first, "run-a"), (second, "run-b")],
        ))

    assert sum(result["claimed"] is True for result in results) == 1
    rejected = next(result for result in results if not result["claimed"])
    assert rejected["reason"] == "capacity"


def test_admission_enforces_owner_fairness_and_preserves_resume_conflicts(
    database,
):
    alice_first = _conversation(database, "alice-a")
    alice_second = _conversation(database, "alice-b")
    bob_first = _conversation(database, "bob-a")

    first = database.claim_agent_run(
        run_id="run-alice-a",
        conversation_id=alice_first,
        request_payload={},
        worker_id="worker-a",
        tenant_id="tenant-a",
        owner_id="alice",
        max_active_runs=4,
        max_owner_active_runs=1,
    )
    duplicate = database.claim_agent_run(
        run_id="run-alice-duplicate",
        conversation_id=alice_first,
        request_payload={},
        worker_id="worker-b",
        tenant_id="tenant-a",
        owner_id="alice",
        max_active_runs=1,
        max_owner_active_runs=1,
    )
    owner_rejected = database.claim_agent_run(
        run_id="run-alice-b",
        conversation_id=alice_second,
        request_payload={},
        worker_id="worker-b",
        tenant_id="tenant-a",
        owner_id="alice",
        max_active_runs=4,
        max_owner_active_runs=1,
    )
    other_owner = database.claim_agent_run(
        run_id="run-bob-a",
        conversation_id=bob_first,
        request_payload={},
        worker_id="worker-c",
        tenant_id="tenant-a",
        owner_id="bob",
        max_active_runs=4,
        max_owner_active_runs=1,
    )

    assert first["claimed"] is True
    assert duplicate["reason"] == "active"
    assert duplicate["run"]["run_id"] == "run-alice-a"
    assert owner_rejected == {
        "claimed": False,
        "reason": "owner_capacity",
        "active_count": 1,
    }
    assert other_owner["claimed"] is True


def test_run_events_are_ordered_durable_and_idempotent(database):
    conversation_id = _conversation(database, "events")
    assert _claim(database, conversation_id, run_id="run-events")["claimed"]

    assert database.append_agent_run_event(
        run_id="run-events",
        sequence=0,
        event_type="text-delta",
        payload={"type": "text-delta", "delta": "你"},
    )
    assert database.append_agent_run_event(
        run_id="run-events",
        sequence=1,
        event_type="text-delta",
        payload={"type": "text-delta", "delta": "好"},
    )
    assert database.append_agent_run_event(
        run_id="run-events",
        sequence=1,
        event_type="text-delta",
        payload={"type": "text-delta", "delta": "ignored duplicate"},
    )
    with pytest.raises(RuntimeError, match="event sequence gap"):
        database.append_agent_run_event(
            run_id="run-events",
            sequence=3,
            event_type="text-delta",
            payload={"type": "text-delta", "delta": "gap"},
        )

    events = database.list_agent_run_events(
        "run-events",
        after_sequence=0,
    )
    assert [event["sequence"] for event in events] == [0, 1]
    assert events[1]["payload"]["delta"] == "好"
    assert database.get_agent_run(run_id="run-events")["event_cursor"] == 2


def test_expired_run_lease_can_be_reclaimed_without_losing_cursor(database):
    conversation_id = _conversation(database, "recovery")
    _claim(database, conversation_id, run_id="run-recovery")
    database.append_agent_run_event(
        run_id="run-recovery",
        sequence=0,
        event_type="text-delta",
        payload={"delta": "partial"},
    )
    with database.session_scope() as session:
        record = session.get(AgentRun, "run-recovery")
        record.lease_expires_at = datetime.now() - timedelta(seconds=1)

    recoverable = database.list_recoverable_agent_runs()
    assert [item["run_id"] for item in recoverable] == ["run-recovery"]
    reclaimed = database.reclaim_agent_run(
        "run-recovery",
        worker_id="worker-b",
        lease_seconds=30,
    )
    assert reclaimed["status"] == "recovering"
    assert reclaimed["worker_id"] == "worker-b"
    assert reclaimed["attempt"] == 2
    assert reclaimed["event_cursor"] == 1


def test_step_ledger_reuses_completed_result_and_bounds_retries(database):
    conversation_id = _conversation(database, "step")
    _claim(database, conversation_id, run_id="run-step")
    arguments = {"symbol": "600519"}
    claim = database.claim_agent_step(
        idempotency_key="step-key",
        run_id="run-step",
        conversation_id=conversation_id,
        task_id="task-1",
        step_id="quote",
        tool_name="get_realtime_quotes",
        effect="read",
        arguments=arguments,
        worker_id="worker-a",
        lease_seconds=30,
        max_attempts=2,
    )
    assert claim == {"action": "execute", "attempt": 1, "max_attempts": 2}
    assert database.fail_agent_step(
        "step-key",
        error_code="timeout",
        error_detail="temporary",
        retryable=True,
    )
    retry = database.claim_agent_step(
        idempotency_key="step-key",
        run_id="run-step",
        conversation_id=conversation_id,
        task_id="task-1",
        step_id="quote",
        tool_name="get_realtime_quotes",
        effect="read",
        arguments=arguments,
        worker_id="worker-b",
        lease_seconds=30,
        max_attempts=2,
    )
    assert retry["action"] == "execute"
    assert retry["attempt"] == 2
    result = {"success": True, "price": 1500}
    assert database.finish_agent_step("step-key", result=result)
    reused = database.claim_agent_step(
        idempotency_key="step-key",
        run_id="run-step",
        conversation_id=conversation_id,
        task_id="task-1",
        step_id="quote",
        tool_name="get_realtime_quotes",
        effect="read",
        arguments=arguments,
        worker_id="worker-c",
        lease_seconds=30,
        max_attempts=2,
    )
    assert reused["action"] == "reuse"
    assert reused["result"] == result


def test_step_ledger_fences_stale_workers_and_key_collisions(database):
    conversation_id = _conversation(database, "step-fence")
    _claim(database, conversation_id, run_id="run-step-fence")
    claim = database.claim_agent_step(
        idempotency_key="fenced-step-key",
        run_id="run-step-fence",
        conversation_id=conversation_id,
        task_id="task-1",
        step_id="quote",
        tool_name="get_realtime_quotes",
        effect="read",
        arguments={"symbol": "600519"},
        worker_id="worker-a",
        lease_seconds=30,
        max_attempts=2,
    )

    assert claim["attempt"] == 1
    assert database.finish_agent_step(
        "fenced-step-key",
        result={"success": True},
        worker_id="worker-b",
        attempt=1,
    ) is False
    assert database.finish_agent_step(
        "fenced-step-key",
        result={"success": True},
        worker_id="worker-a",
        attempt=2,
    ) is False
    with pytest.raises(RuntimeError, match="different execution"):
        database.claim_agent_step(
            idempotency_key="fenced-step-key",
            run_id="run-step-fence",
            conversation_id=conversation_id,
            task_id="task-1",
            step_id="quote",
            tool_name="get_realtime_quotes",
            effect="read",
            arguments={"symbol": "000001"},
            worker_id="worker-a",
            lease_seconds=30,
            max_attempts=2,
        )
    assert database.finish_agent_step(
        "fenced-step-key",
        result={"success": True},
        worker_id="worker-a",
        attempt=1,
    ) is True


def test_effect_outbox_is_stable_across_replay(database):
    conversation_id = _conversation(database, "outbox")
    _claim(database, conversation_id, run_id="run-outbox")
    pending = database.upsert_effect_outbox(
        idempotency_key="effect-key",
        run_id="run-outbox",
        tool_name="send_notification",
        payload={"body": "hello"},
    )
    assert pending["status"] == "pending"
    database.complete_effect_outbox(
        "effect-key",
        result={"success": True},
        provider_reference="provider-42",
    )
    replay = database.upsert_effect_outbox(
        idempotency_key="effect-key",
        run_id="run-outbox",
        tool_name="send_notification",
        payload={"body": "hello"},
    )
    assert replay["status"] == "completed"
    assert replay["result"] == {"success": True}
    assert replay["provider_reference"] == "provider-42"


def test_terminal_commit_rolls_back_all_published_state_on_error(
    database,
    monkeypatch,
):
    conversation_id = _conversation(database, "terminal")
    _claim(database, conversation_id, run_id="run-terminal")
    monkeypatch.setenv("AGENT_TRACE_ENCRYPTION_KEY", "invalid")

    with pytest.raises(RuntimeError):
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
            trace={"compiled_plan": {"token": "must-redact"}},
        )

    assert database.get_chat_messages(conversation_id) == []
    assert database.get_agent_run(run_id="run-terminal")["status"] == "running"

    monkeypatch.delenv("AGENT_TRACE_ENCRYPTION_KEY")
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
            "compiled_plan": {"capability": "general_qa"},
            "latest_stage": {"status": "succeeded"},
        },
    )
    assert [message.content for message in database.get_chat_messages(
        conversation_id
    )] == ["问题", "答案"]
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
    assert [
        message.content
        for message in database.get_chat_messages(conversation_id)
    ] == ["current"]


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
    assert database.try_acquire_agent_resource(
        resource_name="provider:test",
        lease_owner="owner-b",
        slots=1,
        lease_seconds=30,
    ) is None
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
    result = asyncio.run(runtime.complete(
        completion,
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=20,
    ))

    assert result["choices"][0]["message"]["content"] == "ok"
    assert calls == 2
    assert database.get_agent_run(
        run_id="run-model-retry"
    )["provider_call_count"] == 2


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
