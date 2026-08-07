from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping

import pytest

from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.tools.base import ToolSpec, object_schema
from src.tools.registry import ToolRegistry


class ScriptedModel:
    def __init__(self, responses: Mapping[str, list[dict[str, Any]]], texts: list[str]) -> None:
        self.responses = {name: [dict(item) for item in values] for name, values in responses.items()}
        self.texts = list(texts)

    async def structured(self, contract: type, *, function_name: str, validator=None, **_kwargs: Any) -> Any:
        queue = self.responses.get(function_name) or []
        if not queue:
            raise AssertionError(f"no scripted response for {function_name}")
        result = contract.model_validate(queue.pop(0))
        if validator is not None:
            validator(result)
        return result

    async def text(self, **_kwargs: Any) -> str:
        if not self.texts:
            raise AssertionError("no scripted text response")
        return self.texts.pop(0)


def _side_effect_registry(calls: list[dict[str, Any]]) -> ToolRegistry:
    def notify(*, message: str, confirmed: bool = False) -> dict[str, Any]:
        assert confirmed is True
        calls.append({"message": message, "confirmed": confirmed})
        return {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "data_time": "2026-08-06T10:00:00+08:00",
            "source": "https://notification.test/receipt/1",
        }

    spec = ToolSpec(
        name="notify_user",
        description="向用户发送一次外部通知",
        retrieval_text="发送 通知 外部操作",
        parameters=object_schema(
            {
                "message": {"type": "string"},
                "confirmed": {"type": "boolean", "default": False},
            },
            required=("message",),
        ),
        executor=notify,
        effect="side_effect",
        max_attempts=3,
        idempotent=True,
    )
    registry = object.__new__(ToolRegistry)
    registry._tools = OrderedDict([(spec.name, spec)])
    return registry


def _before_interrupt_model() -> ScriptedModel:
    return ScriptedModel(
        {
            "understand_agent_goal": [
                {
                    "objective": "发送通知",
                    "constraints": [],
                    "deliverable": "告知执行结果",
                    "search_queries": ["发送通知"],
                    "needs_tools": True,
                    "needs_clarification": False,
                    "clarification_question": None,
                }
            ],
            "rank_atomic_tools": [
                {
                    "selected_tools": ["notify_user"],
                    "supplemental_queries": [],
                    "rationale": "唯一匹配的原子外部操作",
                }
            ],
            "create_dynamic_action_plan": [
                {
                    "actions": [
                        {
                            "action_id": "notify",
                            "objective": "发送测试通知",
                            "tool_name": "notify_user",
                            "arguments": {"message": "测试通知"},
                            "depends_on": [],
                            "expected_evidence": ["发送回执"],
                        }
                    ],
                    "finalize_without_tools": False,
                    "clarification_question": None,
                    "rationale": "等待服务端审批",
                }
            ],
        },
        [],
    )


def _after_decision_model(*, rejected: bool = False) -> ScriptedModel:
    claim = {
        "claim": "通知服务返回成功回执",
        "material": True,
        "evidence_ids": ["ev_ignored"],
        "supported": False,
        "issue": "拒绝时没有回执",
    }
    verification = {
        "accepted": True,
        "instruction_adherent": True,
        "instruction_issues": [],
        "claims": [] if rejected else [claim | {"material": False}],
        "missing_evidence_queries": [],
        "revised_answer": None,
        "summary": "拒绝已明确记录" if rejected else "执行结果已陈述",
    }
    return ScriptedModel(
        {
            "reflect_agent_progress": [
                {
                    "decision": "finalize",
                    "reason": "用户拒绝，零执行" if rejected else "操作已完成",
                    "search_queries": [],
                }
            ],
            "verify_claim_evidence": [verification],
        },
        ["用户已拒绝该操作，未执行通知。" if rejected else "通知已按批准执行一次。"],
    )


async def _start_interrupt(
    manager: LangGraphRuntimeManager,
    *,
    model: ScriptedModel,
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
        model=model,
    )


async def _resume(
    manager: LangGraphRuntimeManager,
    *,
    pending: Mapping[str, Any],
    decision: str,
    model: ScriptedModel,
    conversation_id: str,
) -> Any:
    return await manager.resume(
        interrupt_id=str(pending["interrupt_id"]),
        decision={"decision": decision, "fingerprint": pending["fingerprint"]},
        llm_config={},
        database=None,
        controller=None,
        run_id=f"run-{conversation_id}",
        conversation_id=conversation_id,
        run_attempt=1,
        tenant_id="tenant",
        owner_id="owner",
        model=model,
    )


def test_model_cannot_forge_confirmation_and_direct_side_effect_is_blocked() -> None:
    calls: list[dict[str, Any]] = []
    registry = _side_effect_registry(calls)

    with pytest.raises(ValueError, match="model cannot set server-controlled fields"):
        registry.validate_model_arguments(
            "notify_user",
            {"message": "x", "confirmed": True},
            approved=False,
        )
    with pytest.raises(PermissionError, match="server-approved interrupt"):
        registry.execute("notify_user", {"message": "x", "confirmed": True})
    assert calls == []


def test_approval_executes_side_effect_exactly_once() -> None:
    async def scenario() -> None:
        calls: list[dict[str, Any]] = []
        manager = LangGraphRuntimeManager(registry=_side_effect_registry(calls))
        await manager.start(testing=True)
        try:
            interrupted = await _start_interrupt(
                manager,
                model=_before_interrupt_model(),
                conversation_id="approve-once",
            )
            assert interrupted.status == "interrupted"
            assert interrupted.pending_interrupt is not None
            assert interrupted.pending_interrupt["arguments"] == {"message": "测试通知"}
            assert "confirmed" not in interrupted.pending_interrupt["arguments"]

            completed = await _resume(
                manager,
                pending=interrupted.pending_interrupt,
                decision="approve",
                model=_after_decision_model(),
                conversation_id="approve-once",
            )
            assert completed.status == "completed"
            assert calls == [{"message": "测试通知", "confirmed": True}]
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_rejection_records_observation_and_executes_nothing() -> None:
    async def scenario() -> None:
        calls: list[dict[str, Any]] = []
        manager = LangGraphRuntimeManager(registry=_side_effect_registry(calls))
        await manager.start(testing=True)
        try:
            interrupted = await _start_interrupt(
                manager,
                model=_before_interrupt_model(),
                conversation_id="reject-zero",
            )
            rejected = await _resume(
                manager,
                pending=interrupted.pending_interrupt or {},
                decision="reject",
                model=ScriptedModel({}, []),
                conversation_id="reject-zero",
            )
            assert rejected.status == "completed"
            assert rejected.final_text == "你已拒绝审批，操作未执行：发送测试通知。"
            assert calls == []
            observation = rejected.state["tool_results"][0]
            assert observation["error_code"] == "approval_rejected"
            assert observation["success"] is False
            assert observation["objective"] == "发送测试通知"
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_pending_interrupt_survives_sqlite_checkpointer_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        checkpoint_path = tmp_path / "agent-checkpoints.sqlite3"
        monkeypatch.setenv("AGENT_CHECKPOINT_DATABASE_URL", str(checkpoint_path))
        calls: list[dict[str, Any]] = []
        registry = _side_effect_registry(calls)
        first = LangGraphRuntimeManager(registry=registry)
        await first.start()
        try:
            interrupted = await _start_interrupt(
                first,
                model=_before_interrupt_model(),
                conversation_id="restart",
            )
            pending = interrupted.pending_interrupt
            assert pending is not None
        finally:
            await first.close()

        second = LangGraphRuntimeManager(registry=registry)
        await second.start()
        try:
            restored = await second.pending_interrupt("restart")
            assert restored is not None
            assert restored["interrupt_id"] == pending["interrupt_id"]
            completed = await _resume(
                second,
                pending=restored,
                decision="approve",
                model=_after_decision_model(),
                conversation_id="restart",
            )
            assert completed.status == "completed"
            assert calls == [{"message": "测试通知", "confirmed": True}]
        finally:
            await second.close()

    asyncio.run(scenario())
