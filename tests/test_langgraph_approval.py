"""Approval boundaries for the generic message-and-tool Agent loop."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.tools.base import ToolSpec, object_schema
from tests.test_langgraph_agent_runtime import FakeAtomicExecutor, ScriptedChatModel, _registry


def _side_effect_operation() -> ToolSpec:
    return ToolSpec(
        name="send_message",
        description="向用户指定的目标发送一条消息",
        parameters=object_schema(
            {
                "message": {"type": "string"},
                "confirmed": {"type": "boolean", "default": False},
            },
            required=("message",),
        ),
        executor=lambda **_kwargs: {"success": True},
        effect="side_effect",
        max_attempts=1,
    )


def _request_action() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "send_message",
                "args": {"message": "测试通知"},
                "id": "send-once",
                "type": "tool_call",
            }
        ],
    )


async def _start_interrupt(
    manager: LangGraphRuntimeManager,
    *,
    executor: FakeAtomicExecutor,
    conversation_id: str,
) -> Any:
    return await manager.run_new(
        messages=[{"role": "user", "content": "请发送测试通知"}],
        user_text="请发送测试通知",
        system_prompt="",
        llm_config={},
        database=None,
        controller=None,
        run_id=f"run-{conversation_id}",
        conversation_id=conversation_id,
        run_attempt=1,
        tenant_id="tenant",
        owner_id="owner",
        model=ScriptedChatModel(responses=[_request_action()]),
        executor=executor,
    )


def test_history_reset_clears_the_native_pending_interrupt() -> None:
    async def scenario() -> None:
        manager = LangGraphRuntimeManager(registry=_registry(_side_effect_operation()), response_format=None)
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        try:
            await _start_interrupt(manager, executor=executor, conversation_id="history-reset")
            assert await manager.pending_interrupt("history-reset") is not None
            await manager.replace_checkpoint_messages("history-reset", [])
            assert await manager.pending_interrupt("history-reset") is None
            state = await manager.get_state("history-reset")
            assert state["messages"] == []
            assert state["conversation_context"] is None
            assert not executor.calls
        finally:
            await manager.close()

    asyncio.run(scenario())


async def _resume(
    manager: LangGraphRuntimeManager,
    *,
    pending: dict[str, Any],
    decision: str,
    executor: FakeAtomicExecutor,
    conversation_id: str,
    model: ScriptedChatModel | None = None,
) -> Any:
    return await manager.resume(
        interrupt_id=str(pending["interrupt_id"]),
        decision={"decision": decision, "fingerprint": str(pending["fingerprint"])},
        llm_config={},
        database=None,
        controller=None,
        run_id=f"run-{conversation_id}",
        conversation_id=conversation_id,
        run_attempt=1,
        tenant_id="tenant",
        owner_id="owner",
        model=model or ScriptedChatModel(responses=[AIMessage(content="操作结果已说明。")]),
        executor=executor,
    )


def test_model_cannot_self_authorize_a_side_effect() -> None:
    registry = _registry(_side_effect_operation())
    with pytest.raises(ValueError, match="model cannot set server-controlled fields"):
        registry.validate_model_arguments(
            "send_message",
            {"message": "测试通知", "confirmed": True},
            approved=False,
        )
    with pytest.raises(PermissionError, match="server-approved interrupt"):
        registry.execute("send_message", {"message": "测试通知", "confirmed": True})


def test_approval_executes_exactly_once_even_if_the_resume_is_replayed() -> None:
    async def scenario() -> None:
        executor = FakeAtomicExecutor()
        manager = LangGraphRuntimeManager(
            registry=_registry(_side_effect_operation()),
            response_format=None,
        )
        await manager.start(testing=True)
        try:
            interrupted = await _start_interrupt(
                manager,
                executor=executor,
                conversation_id="approve-once",
            )
            assert interrupted.status == "interrupted"
            assert interrupted.pending_interrupt is not None
            assert interrupted.pending_interrupt["arguments"] == {"message": "测试通知"}
            assert executor.calls == []

            completed = await _resume(
                manager,
                pending=interrupted.pending_interrupt,
                decision="approve",
                executor=executor,
                conversation_id="approve-once",
                model=ScriptedChatModel(responses=[AIMessage(content="通知已发送。")]),
            )
            assert completed.status == "completed"
            assert len(executor.calls) == 1
            assert executor.calls[0]["approved"] is True

            replay = await _resume(
                manager,
                pending=interrupted.pending_interrupt,
                decision="approve",
                executor=executor,
                conversation_id="approve-once",
                model=ScriptedChatModel(),
            )
            assert replay.status == "completed"
            assert len(executor.calls) == 1
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_rejection_records_an_observation_and_executes_nothing() -> None:
    async def scenario() -> None:
        executor = FakeAtomicExecutor()
        manager = LangGraphRuntimeManager(
            registry=_registry(_side_effect_operation()),
            response_format=None,
        )
        await manager.start(testing=True)
        try:
            interrupted = await _start_interrupt(
                manager,
                executor=executor,
                conversation_id="reject-zero",
            )
            rejected = await _resume(
                manager,
                pending=interrupted.pending_interrupt or {},
                decision="reject",
                executor=executor,
                conversation_id="reject-zero",
                model=ScriptedChatModel(responses=[AIMessage(content="你拒绝了操作，未执行。")]),
            )
            assert rejected.status == "completed"
            assert executor.calls == []
            observation = rejected.state["tool_results"][0]
            assert observation["error_code"] == "approval_rejected"
            assert observation["success"] is False
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_pending_interrupt_survives_a_native_sqlite_checkpointer_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        checkpoint_path = tmp_path / "agent-loop.sqlite3"
        monkeypatch.setenv("AGENT_CHECKPOINT_DATABASE_URL", str(checkpoint_path))
        registry = _registry(_side_effect_operation())
        executor = FakeAtomicExecutor()

        first = LangGraphRuntimeManager(registry=registry, response_format=None)
        await first.start()
        try:
            interrupted = await _start_interrupt(
                first,
                executor=executor,
                conversation_id="sqlite-restart",
            )
            assert interrupted.pending_interrupt is not None
        finally:
            await first.close()

        second = LangGraphRuntimeManager(registry=registry, response_format=None)
        await second.start()
        try:
            pending = await second.pending_interrupt("sqlite-restart")
            assert pending is not None
            assert pending["fingerprint"] == interrupted.pending_interrupt["fingerprint"]
            completed = await _resume(
                second,
                pending=pending,
                decision="approve",
                executor=executor,
                conversation_id="sqlite-restart",
                model=ScriptedChatModel(responses=[AIMessage(content="恢复后已执行。")]),
            )
            assert completed.status == "completed"
            assert len(executor.calls) == 1
        finally:
            await second.close()

    asyncio.run(scenario())
