"""Owned, bounded recovery at the native tools/model boundary."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from src.agent.langgraph_runtime.middleware import _source_fallback_requirements
from src.agent.langgraph_runtime.source_recovery import advance_source_recovery
from src.agent.langgraph_runtime.state import merge_records
from tests.test_langgraph_agent_runtime import (
    FakeAtomicExecutor, ScriptedChatModel, _named_tool_call, _registry, _run,
    _search_operation, _tool_call, _web_search_operation, _web_source_operation,
)


def _apply(state, update):
    for key, value in update.items():
        if key in {"source_fallback_attempts", "runtime_errors", "tool_results"}:
            state[key] = merge_records(state.get(key), value)
        elif key == "fallback_repair_count":
            state[key] = state.get(key, 0) + value
        else:
            state[key] = value


def _fixture(*, remaining=10, limit=2):
    events = []
    registry = _registry(_search_operation(category="source_read"), _web_search_operation(), _web_source_operation())
    context = SimpleNamespace(run_id="run", registry=registry,
                              events=SimpleNamespace(stage=lambda *a, **kw: events.append((a, kw))))
    errors = [{"id": f"err{i}", "error_id": f"err{i}", "fallback_status": "pending"} for i in range(2)]
    records = [{"id": f"failed{i}", "action_id": f"failed{i}", "task_id": f"task{i}",
                "tool_name": "search_source", "arguments": {"query": "same"},
                "success": False, "runtime_errors": [errors[i]]} for i in range(2)]
    state = {"tool_results": records, "runtime_errors": errors,
             "model_turn_count": 1, "tool_call_limit": remaining,
             "fallback_repair_limit": limit, "tool_call_count": 0}
    return state, context, events


def test_each_failure_owns_a_separate_recovery_and_updates_only_its_receipt():
    state, context, events = _fixture()
    requirements = _source_fallback_requirements(state, context.registry)
    _apply(state, advance_source_recovery(state, context, requirements, []))
    first = state["source_fallback_attempts"][0]
    assert first["task_id"] == "task0"
    assert first["fallback_operation"] == "search_web_source"
    assert state["runtime_errors"][1]["fallback_status"] == "pending"
    state["model_turn_count"] += 1
    web = {"id": "web0", "tool_name": "search_web_source", "success": True,
           "result": {"value": "observation"}, "fallback_request_id": first["id"]}
    _apply(state, advance_source_recovery(state, context, requirements, [web]))
    assert [a["status"] for a in state["source_fallback_attempts"]] == ["completed", "started"]
    assert state["runtime_errors"][0]["fallback_call_ids"] == ["web0"]
    assert state["runtime_errors"][1]["fallback_call_ids"] == []
    assert state["runtime_errors"][0]["details"]["coverage_requires_validation"]
    state["model_turn_count"] += 1
    _apply(state, advance_source_recovery(state, context, requirements, []))
    assert [a["status"] for a in state["source_fallback_attempts"]] == ["completed", "failed"]
    assert state["fallback_feedback"] == ""
    assert advance_source_recovery(state, context, requirements, []) == {}
    assert len(events) == 4


@pytest.mark.parametrize("remaining,limit", [(0, 2), (10, 0)])
def test_unavailable_recovery_has_terminal_receipts_without_forcing_empty_tool_request(remaining, limit):
    state, context, _ = _fixture(remaining=remaining, limit=limit)
    _apply(state, advance_source_recovery(state, context, _source_fallback_requirements(state, context.registry), []))
    assert all(item["fallback_status"] == "skipped" for item in state["runtime_errors"])
    assert state["fallback_feedback"] == ""
    assert state["fallback_repair_count"] == 0


def test_url_in_failed_arguments_is_preferred_over_search():
    state, context, _ = _fixture()
    state["tool_results"][0]["arguments"]["url"] = "https://example.test/original"
    _apply(state, advance_source_recovery(state, context, _source_fallback_requirements(state, context.registry), []))
    assert state["source_fallback_attempts"][0]["fallback_operation"] == "read_web_source"


def test_recovery_identity_uses_durable_error_scope_before_worker_handoff():
    state, context, _ = _fixture()
    record = state["tool_results"][0]
    record.pop("task_id")
    state["runtime_errors"][0].update(task_id="task0", agent_id="worker:attempt-1")
    record["runtime_error"] = dict(state["runtime_errors"][0])
    before = _source_fallback_requirements(state, context.registry)[0]
    _apply(state, advance_source_recovery(state, context, [before], []))
    assert state["source_fallback_attempts"][0]["task_id"] == "task0"
    # Handoff adds metadata; it must not turn an observed failure into a new
    # logical request, or lose its completed recovery on the next attempt.
    state["tool_results"][0]["task_id"] = "task0"
    after = _source_fallback_requirements(state, context.registry)[0]
    assert after["fallback_key"] == before["fallback_key"]
    assert after["fallback_attempted"] is True


def test_argument_validation_returns_to_model_without_web_recovery():
    async def scenario():
        model = ScriptedChatModel(responses=[
            _named_tool_call("invalid", "search_source", {}),
            _tool_call("corrected", "primary"),
            AIMessage(content="已核实该事实。【证据 ev_corrected】"),
        ])
        result, executor = await _run(
            model=model,
            registry=_registry(_search_operation(category="source_read"), _web_search_operation()),
            conversation_id="correct-input-not-source",
        )
        assert result.status == "completed"
        assert [item["action_id"] for item in executor.calls] == ["corrected"]
        assert result.state.get("source_fallback_attempts", []) == []
        assert result.state["runtime_errors"][0]["fallback_eligible"] is False
        assert model.call_options[1].get("tool_choice") != "required"
        assert "search_source" in {tool.name for tool in model.call_options[1]["tools"]}

    asyncio.run(scenario())


@pytest.mark.parametrize("result_key", ["items", "results"])
def test_search_then_body_read_are_native_calls_with_one_recovery_identity(result_key):
    async def scenario():
        model = ScriptedChatModel(responses=[
            _tool_call("failed", "primary"),
            _named_tool_call("search", "search_web_source", {"source_id": "auto", "query": "测试问题"}),
            _named_tool_call("body", "read_web_source", {"source_id": "http", "url": "https://example.test/body"}),
            AIMessage(content="已核实该事实。【证据 ev_body】"),
        ])
        result, executor = await _run(
            model=model, registry=_registry(_search_operation(category="source_read"), _web_search_operation(), _web_source_operation()),
            executor=FakeAtomicExecutor({
                "search_source": [{"success": False, "source_refs": ["数据接口"]}],
                "search_web_source": [{"result": {result_key: [{"url": "https://example.test/body", "title": "相关事实"}]}}],
            }), conversation_id="search-read-owned",
        )
        assert result.status == "completed"
        assert [item["tool_name"] for item in executor.calls] == ["search_source", "search_web_source", "read_web_source"]
        attempts = result.state["source_fallback_attempts"]
        assert len(attempts) == 1 and attempts[0]["status"] == "completed"
        assert attempts[0]["fallback_call_ids"] == ["search", "body"]
        assert {tool.name for tool in model.call_options[1]["tools"]} == {"search_web_source"}
        assert {tool.name for tool in model.call_options[2]["tools"]} == {"read_web_source"}
        assert all(record["fallback_request_id"] == attempts[0]["id"] for record in result.state["tool_results"][1:])
        assert result.state["runtime_errors"][0]["fallback_call_ids"] == ["search", "body"]
    asyncio.run(scenario())


def test_plan_waits_for_shared_recovery_instead_of_replanning_the_same_failure():
    from src.agent.langgraph_runtime.graph import DEFAULT_RESPONSE_FORMAT
    from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
    from tests.test_agent_planning import _planner_call, _step, _report
    from tests.test_langgraph_agent_runtime import _structured_output_call

    async def scenario():
        model = ScriptedChatModel(responses=[
            _planner_call("plan", [_step("first", "取得可核验来源")]),
            _tool_call("failed", "primary"),
            _named_tool_call("web", "search_web_source", {"source_id": "auto", "query": "测试问题"}),
            _report("first", ["web"], last=True),
            _structured_output_call("answer", [{"kind": "fact", "content": "已取得可核验来源。", "source_ids": [1]}]),
        ])
        executor = FakeAtomicExecutor({"search_source": [{"success": False}]})
        manager = LangGraphRuntimeManager(
            registry=_registry(_search_operation(category="source_read"), _web_search_operation()),
            response_format=DEFAULT_RESPONSE_FORMAT,
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "测试问题"}], user_text="测试问题",
                system_prompt="", llm_config={}, database=None, controller=None,
                run_id="plan-recovery", conversation_id="plan-recovery", run_attempt=1,
                tenant_id="tenant", owner_id="owner", model=model, executor=executor,
                agent_mode="plan", planning_mode="planned",
            )
        finally:
            await manager.close()
        assert result.status == "completed", result.error_code
        assert result.state["planning_replan_count"] == 0
        assert len(result.state["source_fallback_attempts"]) == 1
        assert [call["tool_name"] for call in executor.calls] == ["search_source", "search_web_source"]
        assert model.call_options[2]["tool_choice"] == "required"
        assert {tool.name for tool in model.call_options[2]["tools"]} == {"search_web_source"}
    asyncio.run(scenario())
