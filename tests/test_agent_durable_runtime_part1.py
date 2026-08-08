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
from src.agent.model_runtime import GuardedModelRuntime
from src.agent.terminal_publisher import AgentTerminalPublisher
from src.storage import DatabaseManager
from src.storage.models import AgentRun, AgentStepExecution



"""Focused test slice 1; shared fixtures remain local to this slice."""

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
def test_cross_thread_admission_enforces_one_global_slot(database):
    first = _conversation(database, "capacity-a")
    second = _conversation(database, "capacity-b")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: _claim(
                    database,
                    item[0],
                    run_id=item[1],
                    max_active_runs=1,
                ),
                [(first, "run-a"), (second, "run-b")],
            )
        )

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

def test_run_event_batches_commit_cursor_and_state_together(database):
    conversation_id = _conversation(database, "event-batch")
    assert _claim(
        database,
        conversation_id,
        run_id="run-event-batch",
    )["claimed"]

    events = [
        {
            "event_type": "text-delta",
            "payload": {"type": "text-delta", "text_delta": value},
        }
        for value in ("a", "b", "c")
    ]
    assert database.append_agent_run_events(
        run_id="run-event-batch",
        start_sequence=0,
        events=events,
    )
    # A complete replay is idempotent; a gap is still rejected.
    assert database.append_agent_run_events(
        run_id="run-event-batch",
        start_sequence=0,
        events=events,
    )
    with pytest.raises(RuntimeError, match="event sequence gap"):
        database.append_agent_run_events(
            run_id="run-event-batch",
            start_sequence=4,
            events=events[:1],
        )

    batch = database.read_agent_run_event_batch(
        "run-event-batch",
        after_sequence=1,
    )
    assert batch is not None
    assert batch["run"]["event_cursor"] == 3
    assert [event["payload"]["text_delta"] for event in batch["events"]] == ["b", "c"]

def test_compiled_checkpoint_is_fenced_by_owner_and_attempt(database):
    conversation_id = _conversation(database, "checkpoint")
    assert _claim(
        database,
        conversation_id,
        run_id="run-checkpoint",
    )["claimed"]
    checkpoint = {
        "checkpoint_version": "compiled-v1",
        "stage": "compiled",
    }

    assert database.save_agent_run_checkpoint(
        "run-checkpoint",
        worker_id="worker-a",
        attempt=1,
        checkpoint=checkpoint,
    )
    assert database.get_agent_run(run_id="run-checkpoint")["context_snapshot"] == checkpoint
    assert not database.save_agent_run_checkpoint(
        "run-checkpoint",
        worker_id="worker-b",
        attempt=1,
        checkpoint={"stage": "stale-owner"},
    )
    assert not database.save_agent_run_checkpoint(
        "run-checkpoint",
        worker_id="worker-a",
        attempt=2,
        checkpoint={"stage": "stale-attempt"},
    )
    assert database.finish_agent_run(
        "run-checkpoint",
        status="failed",
        error_code="test",
    )
    assert database.get_agent_run(run_id="run-checkpoint")["context_snapshot"] is None


def test_interrupt_decision_slot_can_be_consumed_only_once(database):
    conversation_id = _conversation(database, "approval-once")
    assert _claim(
        database,
        conversation_id,
        run_id="run-approval-once",
    )["claimed"]
    checkpoint = {
        "engine": "langgraph_agent_loop",
        "pending_interrupt": {
            "interrupt_id": "interrupt-1",
            "fingerprint": "f" * 64,
        },
    }

    assert database.interrupt_agent_run(
        "run-approval-once",
        worker_id="worker-a",
        attempt=1,
        checkpoint=checkpoint,
    )
    first = database.resume_interrupted_agent_run(
        "run-approval-once",
        worker_id="worker-b",
    )
    duplicate = database.resume_interrupted_agent_run(
        "run-approval-once",
        worker_id="worker-c",
    )

    assert first is not None
    assert first["status"] == "running"
    assert first["worker_id"] == "worker-b"
    assert duplicate is None

def test_terminal_publisher_flushes_events_before_terminal_commit():
    order: list[str] = []

    class Controller:
        async def drain(self):
            order.append("events")

    class Database:
        def commit_agent_run_terminal(self, **_kwargs):
            order.append("terminal")
            return True

    class Sessions:
        @staticmethod
        def normalize_messages(messages):
            return messages

        @staticmethod
        def generate_title(_text):
            return "title"

    publisher = AgentTerminalPublisher(
        controller=Controller(),
        run=SimpleNamespace(run_id="run", attempt=1),
        messages=[{"role": "user", "content": "hello"}],
        request_body={},
        conversation_id="conversation",
        database=Database(),
        session_service=Sessions(),
        worker_id="worker",
    )
    asyncio.run(
        publisher.commit(
            status="completed",
            final_text="done",
        )
    )

    assert order == ["events", "terminal"]


def test_terminal_publisher_persists_safe_structured_execution_trace():
    captured: dict = {}

    class Controller:
        async def drain(self):
            return None

        @staticmethod
        def stage_history_snapshot():
            return [
                {
                    "event": "agent_stage",
                    "run_id": "run-trace",
                    "stage": "execute",
                    "status": "completed",
                    "summary": "原子工具已返回结果",
                    "details": {"tool_name": "read_quote", "success": True},
                }
            ]

    class Database:
        def commit_agent_run_terminal(self, **kwargs):
            captured.update(kwargs)
            return True

    class Sessions:
        @staticmethod
        def normalize_messages(messages):
            return messages

        @staticmethod
        def generate_title(_text):
            return "title"

    publisher = AgentTerminalPublisher(
        controller=Controller(),
        run=SimpleNamespace(run_id="run-trace", attempt=1),
        messages=[{"role": "user", "content": "查询行情"}],
        request_body={},
        conversation_id="conversation-trace",
        database=Database(),
        session_service=Sessions(),
        worker_id="worker",
    )
    asyncio.run(
        publisher.commit(
            status="completed",
            final_text="答案",
            graph_state={
                "tool_results": [
                    {
                        "action_id": "a1",
                        "tool_call_id": "call-a1",
                        "model_tool_call_id": "call-a1",
                        "tool_name": "read_quote",
                        "effect": "read",
                        "success": True,
                        "data_time": "2026-08-07",
                        "source_refs": ["source-1"],
                    }
                ],
                "evidence": [
                    {
                        "evidence_id": "ev-a1",
                        "action_id": "a1",
                        "tool_call_id": "call-a1",
                        "tool_name": "read_quote",
                        "effect": "read",
                        "success": True,
                        "data_time": "2026-08-07",
                        "source_refs": ["source-1"],
                    }
                ],
                "claim_evidence": [
                    {
                        "claim_id": "claim_1",
                        "text": "报价已返回【证据 ev-a1】",
                        "kind": "fact",
                        "evidence_ids": ["ev-a1"],
                        "entity_fields": ["symbol"],
                        "time_references": ["2026-08-07"],
                        "checks": {
                            "tool_success": True,
                            "source": True,
                            "entity_scope": True,
                            "time": True,
                        },
                        "evidence": [
                            {
                                "evidence_id": "ev-a1",
                                "tool_name": "read_quote",
                                "data_time": "2026-08-07",
                                "source_refs": ["source-1"],
                            }
                        ],
                    }
                ],
                "completed_tool_call_ids": ["call-a1"],
                "model_turn_count": 2,
                "tool_call_count": 1,
                "tool_call_limit": 8,
                "evidence_repair_count": 0,
                "evidence_repair_limit": 1,
                "work_budget_exhausted": False,
            },
        )
    )

    trace = captured["trace"]["quality_projection"]["execution_trace"]
    assert trace["stages"][0]["stage"] == "execute"
    assert "actions" not in trace
    assert trace["tool_results"][0]["tool_call_id"] == "call-a1"
    assert trace["tool_results"][0]["data_time"] == "2026-08-07"
    assert trace["evidence"][0]["evidence_id"] == "ev-a1"
    assert trace["evidence"][0]["tool_call_id"] == "call-a1"
    assert trace["claim_evidence"][0]["evidence_ids"] == ["ev-a1"]
    assert trace["claim_evidence"][0]["checks"]["time"] is True
    assert trace["loop"]["tool_call_count"] == 1
    assert trace["completed_tool_call_ids"] == ["call-a1"]

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

def test_running_step_lease_can_be_renewed_without_ending_the_step(database):
    conversation_id = _conversation(database, "step-renew")
    _claim(database, conversation_id, run_id="run-step-renew")
    claim = database.claim_agent_step(
        idempotency_key="step-renew-key",
        run_id="run-step-renew",
        conversation_id=conversation_id,
        task_id="task-renew",
        step_id="slow-analysis",
        tool_name="get_financial_analysis",
        effect="read",
        arguments={"symbol": "300850"},
        worker_id="worker-a",
        lease_seconds=5,
        max_attempts=2,
    )
    assert claim["action"] == "execute"
    assert (
        database.renew_agent_step_lease(
            "step-renew-key",
            worker_id="worker-b",
            attempt=1,
            lease_seconds=3_600,
        )
        is False
    )
    assert database.renew_agent_step_lease(
        "step-renew-key",
        worker_id="worker-a",
        attempt=1,
        lease_seconds=3_600,
    )
    with database.session_scope() as session:
        record = session.get(AgentStepExecution, "step-renew-key")
        assert record.status == "running"
        assert record.lease_expires_at > datetime.now() + timedelta(minutes=50)

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
    assert (
        database.finish_agent_step(
            "fenced-step-key",
            result={"success": True},
            worker_id="worker-b",
            attempt=1,
        )
        is False
    )
    assert (
        database.finish_agent_step(
            "fenced-step-key",
            result={"success": True},
            worker_id="worker-a",
            attempt=2,
        )
        is False
    )
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
    assert (
        database.finish_agent_step(
            "fenced-step-key",
            result={"success": True},
            worker_id="worker-a",
            attempt=1,
        )
        is True
    )

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
