"""Focused regression tests for the native multi-agent team graph."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Overwrite
from pydantic import Field

from src.agent.langgraph_runtime.answer_contract import STRUCTURED_OUTPUT_TOOL_NAME
from src.agent.langgraph_runtime.events import GraphEventBridge
from src.agent.langgraph_runtime.executor import AtomicToolExecutor
from src.agent.langgraph_runtime.runtime import (
    LangGraphRuntimeManager,
    _reset_turn_state,
)
from src.agent.langgraph_runtime.team.events import TeamWorkerEventBridge
from src.agent.langgraph_runtime.team.evidence import merge_worker_evidence
from src.agent.langgraph_runtime.team import graph as team_graph_module
from src.agent.langgraph_runtime.team.criteria import (
    CriteriaValidationError,
    validate_criteria_assessment,
)
from src.agent.langgraph_runtime.team.graph import (
    _dispatch_team_tasks,
    _fallback_team_plan,
    _materialize_team_plan,
    _normalize_case,
    _normalize_consensus,
    _normalize_conflict,
    _normalize_plan,
    _repair_advisory_budget_mismatch,
    _worker_handoff_status,
    _team_handoff_partitions,
    _team_plan_next,
    _plan_next,
    _send_team_tasks,
    build_team_graph,
    _invoke_streaming_subgraph,
    _run_worker,
    _team_contract_diagnostic,
)
from src.agent.langgraph_runtime.team.contracts import AgentResult, BullCaseReview, TeamPlanDraft, TeamReviewIssue
from src.agent.langgraph_runtime.team.registry import (
    ExpertDefinition,
    ExpertRegistry,
    ScopedToolRegistry,
    TaskScopedExecutor,
    expert_tool_names,
)
from src.agent.langgraph_runtime.team.synthesis import (
    build_team_review_report,
    team_synthesis_contract_issues,
)
from src.agent.langgraph_runtime.team.trace import team_trace
from src.tools.base import ToolSpec, object_schema
from src.tools.registry import ToolRegistry
from tests.test_agent_planning import _planner_call, _report, _step
from tests.test_langgraph_agent_runtime import (
    FakeAtomicExecutor,
    ScriptedChatModel,
    _named_tool_call,
    _registry,
    _search_operation,
    _structured_output_call,
)


def _call(name: str, call_id: str, args: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _probe(name: str, category: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"测试工具 {name}",
        parameters=object_schema(
            {"query": {"type": "string"}},
            required=("query",),
        ),
        executor=lambda **_kwargs: {"success": True},
        category=category,
        max_attempts=1,
    )


def _team_plan() -> dict:
    return {
        "plan_id": "team-plan-test",
        "goal": "完成两个领域的独立核验",
        "completion_criteria": ["两个领域都完成交接"],
        "tasks": [
            {
                "task_id": "market-task",
                "agent_id": "market",
                "agent_node": "MarketAgent",
                "objective": "核验行情观察",
                "input_refs": ["用户问题中的标的"],
                "allowed_tools": ["market_probe"],
                "output_format": "行情观察、时间口径和限制",
                "timeout_seconds": 30,
                "failure_strategy": "partial",
                "required_evidence": ["行情观察"],
                "success_criteria": ["返回领域观察"],
                "max_tool_calls": 1,
                "parallel_group": "research",
                "depends_on": [],
            },
            {
                "task_id": "fundamental-task",
                "agent_id": "fundamental",
                "agent_node": "FundamentalAgent",
                "objective": "核验基本面观察",
                "input_refs": ["用户问题中的标的"],
                "allowed_tools": ["fundamental_probe"],
                "output_format": "基本面观察、时间口径和限制",
                "timeout_seconds": 30,
                "failure_strategy": "replan",
                "required_evidence": ["基本面观察"],
                "success_criteria": ["返回领域观察"],
                "max_tool_calls": 1,
                "parallel_group": "research",
                "depends_on": [],
            },
        ],
        "synthesis_instructions": "综合两个领域交接，不要越过证据边界。",
    }


def _recoverable_team_plan() -> dict:
    plan = _team_plan()
    plan["tasks"][0] = {
        **plan["tasks"][0],
        "allowed_tools": ["market_probe_a", "market_probe_b"],
        "max_tool_calls": 2,
    }
    return plan


def _team_plan_draft(plan: dict) -> dict:
    """Convert the canonical fixture into the provider-facing small draft."""
    return {
        "progress_text": "模型已按独立证据维度拆分核验任务。",
        "goal": plan.get("goal", "完成多领域核验"),
        "completion_criteria": list(plan.get("completion_criteria") or []),
        "tasks": [
            {
                "task_id": task.get("task_id") or "",
                "agent_id": task.get("agent_id"),
                "objective": task.get("objective") or "完成领域核验",
                "input_refs": list(task.get("input_refs") or []),
                "depends_on": list(task.get("depends_on") or []),
                "success_criteria": list(task.get("success_criteria") or ["返回领域观察"]),
                "activation_reason": task.get("activation_reason") or "测试任务",
                "tool_hints": list(task.get("allowed_tools") or []),
                "failure_strategy": task.get("failure_strategy") or "partial",
                "max_attempts": int(task.get("max_attempts") or 2),
            }
            for task in plan.get("tasks") or []
        ],
        "synthesis_instructions": plan.get("synthesis_instructions") or "综合领域交接。",
    }


def test_worker_handoff_requires_completed_status_not_just_a_report_id() -> None:
    returned, missing, incomplete = _team_handoff_partitions(
        ["market-task", "fundamental-task", "news-task"],
        {
            "market-task": {"task_id": "market-task", "status": "partial"},
            "fundamental-task": {"task_id": "fundamental-task", "status": "completed"},
        },
    )

    assert returned == ["market-task", "fundamental-task"]
    assert missing == ["news-task"]
    assert incomplete == ["market-task"]


def test_worker_child_timeout_keeps_streamed_state_and_skips_empty_contract_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        registry = ToolRegistry.from_tools([_probe("market_probe", "market")])
        manager = LangGraphRuntimeManager(registry=registry)
        model = RoleAwareTeamModel()
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        original_wait_for = team_graph_module.asyncio.wait_for
        try:
            context = manager._context(
                llm_config={},
                database=None,
                controller=None,
                run_id="team-timeout-state",
                conversation_id="team-timeout-state",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
            )
            task = _team_plan()["tasks"][0]
            partial_state = {
                "status": "running",
                "tool_results": [
                    {
                        "id": "market-tool-1",
                        "tool_name": "market_probe",
                        "success": True,
                        "result": {"price": 11.35},
                    }
                ],
                "evidence": [
                    {
                        "evidence_id": "ev-market-1",
                        "source": "market_probe",
                        "content": "已取得行情快照",
                        "success": True,
                    }
                ],
                "tool_call_count": 1,
                "model_turn_count": 1,
            }

            async def fake_streaming_subgraph(*_args, progress_sink=None, **_kwargs):
                assert progress_sink is not None
                progress_sink.update(partial_state)
                raise asyncio.TimeoutError()

            async def unexpected_contract(*_args, **_kwargs):
                raise AssertionError("worker timeout must not start empty handoff contracts")

            monkeypatch.setattr(team_graph_module, "_invoke_streaming_subgraph", fake_streaming_subgraph)
            monkeypatch.setattr(team_graph_module, "_invoke_contract", unexpected_contract)

            result = await _run_worker(
                {
                    "team_id": "team-timeout-state",
                    "team_current_task": task,
                    "team_current_task_attempt": 1,
                    "user_text": "请核验行情",
                    "system_prompt": "",
                },
                context,
                response_format=None,
                agent_node="MarketAgent",
                                expected_agent_id="market",
            )
        finally:
            await manager.close()

        report = result["team_results"][0]
        assert report["status"] == "partial"
        assert report["error_code"] == "team_worker_child_timeout"
        assert report["criteria_status"] == "blocked"
        assert result["team_contract_call_count"] == 0
        assert len(result["tool_results"]) == 1
        assert result["evidence"][0]["evidence_id"] == "ev-market-1"
        assert "本 worker 尚未开始执行" not in report["summary"]
        assert any(item.get("error_code") == "team_worker_child_timeout" for item in result["runtime_errors"])

    asyncio.run(scenario())


def test_active_worker_is_not_wrapped_in_task_wall_clock_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        registry = ToolRegistry.from_tools([_probe("market_probe", "market")])
        manager = LangGraphRuntimeManager(registry=registry)
        model = RoleAwareTeamModel()
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        try:
            original_wait_for = team_graph_module.asyncio.wait_for
            context = manager._context(
                llm_config={},
                database=None,
                controller=None,
                run_id="team-no-wall-clock-timeout",
                conversation_id="team-no-wall-clock-timeout",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
            )
            task = _team_plan()["tasks"][0]
            child_state = {
                "status": "completed",
                "answer_final": "行情取证已完成。",
                "tool_results": [{
                    "id": "market-tool-1",
                    "tool_name": "market_probe",
                    "success": True,
                    "result": {"price": 11.35},
                }],
                "evidence": [{
                    "evidence_id": "ev-market-1",
                    "source": "market_probe",
                    "content": "已取得行情快照",
                    "success": True,
                }],
                "tool_call_count": 1,
                "model_turn_count": 2,
            }

            async def active_stream(*_args, progress_sink=None, **_kwargs):
                assert progress_sink is not None
                progress_sink.update(child_state)
                await asyncio.sleep(0)
                return child_state

            async def contract_result(_context, schema, *_args, **_kwargs):
                if schema.__name__ == "WorkerAssessment":
                    return ({
                        "summary": "行情取证已完成。",
                        "findings": [],
                        "limitations": [],
                        "open_questions": [],
                        "confidence": "medium",
                    }, 1)
                return ({
                    "checks": [{
                        "criterion_index": 1,
                        "criterion": "返回领域观察",
                        "verdict": "pass",
                        "explanation": "已取得有效行情证据。",
                        "source_ids": [1],
                    }],
                }, 1)

            async def reject_worker_deadline(awaitable, *, timeout):
                if timeout == task["timeout_seconds"]:
                    close = getattr(awaitable, "close", None)
                    if callable(close):
                        close()
                    raise AssertionError("active worker must not use task timeout as a wall-clock deadline")
                return await original_wait_for(awaitable, timeout=timeout)

            monkeypatch.setattr(team_graph_module, "_invoke_streaming_subgraph", active_stream)
            monkeypatch.setattr(team_graph_module, "_invoke_contract", contract_result)
            monkeypatch.setattr(team_graph_module.asyncio, "wait_for", reject_worker_deadline)

            result = await _run_worker(
                {
                    "team_id": "team-no-wall-clock-timeout",
                    "team_current_task": task,
                    "team_current_task_attempt": 1,
                    "user_text": "请核验行情",
                    "system_prompt": "",
                },
                context,
                response_format=None,
                agent_node="MarketAgent",
                                expected_agent_id="market",
            )
        finally:
            monkeypatch.setattr(team_graph_module.asyncio, "wait_for", original_wait_for)
            await manager.close()

        report = result["team_results"][0]
        assert report["status"] == "completed"
        assert report["error_code"] is None

    asyncio.run(scenario())


def test_team_plan_fans_out_to_three_named_agent_nodes() -> None:
    sends = _plan_next(
        {
            "team_status": "planned",
            "team_id": "named-team",
            "team_plan": {
                "tasks": [
                    {"agent_id": "market", "agent_node": "MarketAgent", "task_id": "market-task"},
                    {"agent_id": "fundamental", "agent_node": "FundamentalAgent", "task_id": "fundamental-task"},
                    {"agent_id": "news", "agent_node": "NewsResearchAgent", "task_id": "news-task"},
                ]
            },
        }
    )

    assert [item.node for item in sends] == [
        "MarketAgent",
        "FundamentalAgent",
        "NewsResearchAgent",
    ]
    assert [item.arg["team_current_task"]["agent_id"] for item in sends] == [
        "market",
        "fundamental",
        "news",
    ]


def test_team_dispatch_honors_dependencies_and_only_fans_out_ready_tasks() -> None:
    plan = {
        "tasks": [
            {"task_id": "market", "agent_id": "market", "agent_node": "MarketAgent", "depends_on": []},
            {"task_id": "fundamental", "agent_id": "fundamental", "agent_node": "FundamentalAgent", "depends_on": ["market"]},
            {"task_id": "news", "agent_id": "news", "agent_node": "NewsResearchAgent", "depends_on": []},
        ]
    }
    initial = {
        "team_status": "planned",
        "team_id": "dependency-team",
        "team_plan": plan,
        "team_results": [],
        "team_dispatched_task_ids": [],
    }
    first_update = _dispatch_team_tasks(initial)
    assert first_update["team_ready_task_ids"] == ["market", "news"]
    first_sends = _plan_next({**initial, **first_update})
    assert [item.arg["team_current_task"]["task_id"] for item in first_sends] == ["market", "news"]

    second = {
        **initial,
        **first_update,
        "team_results": [
            {"task_id": "market", "status": "completed"},
            {"task_id": "news", "status": "completed"},
        ],
    }
    second_update = _dispatch_team_tasks(second)
    assert second_update["team_ready_task_ids"] == ["fundamental"]
    second_sends = _plan_next({**second, **second_update})
    assert [item.arg["team_current_task"]["task_id"] for item in second_sends] == ["fundamental"]

    finished = {
        **second,
        **second_update,
        "team_results": [
            {"task_id": "market", "status": "completed"},
            {"task_id": "news", "status": "completed"},
            {"task_id": "fundamental", "status": "completed"},
        ],
    }
    assert _plan_next(finished) == "worker_failure_policy"


def test_team_dispatch_honors_abort_failure_strategy() -> None:
    plan = _team_plan()
    plan["tasks"][0]["failure_strategy"] = "abort"
    state = {
        "team_plan": plan,
        "team_results": [{"task_id": plan["tasks"][0]["task_id"], "status": "partial"}],
        "team_dispatched_task_ids": [plan["tasks"][0]["task_id"]],
        "team_dispatch_round": 1,
    }

    update = _dispatch_team_tasks(state)

    assert update["team_status"] == "dispatching"
    assert _plan_next({**state, **update}) == "worker_failure_policy"


def test_fresh_team_turn_clears_reducer_state_from_previous_checkpoint() -> None:
    """A new user turn must not inherit retry counters or worker reports."""
    defaults = _reset_turn_state()

    assert isinstance(defaults["team_task_attempts"], Overwrite)
    assert defaults["team_task_attempts"].value == {}
    assert isinstance(defaults["collaboration"], Overwrite)
    assert defaults["collaboration"].value["reports"] == {}
    assert defaults["collaboration"].value["tasks"] == []


def test_team_plan_contract_keeps_execution_metadata_and_validates_dependency_dag() -> None:
    registry = ToolRegistry.from_tools(
        [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
    )
    normalized = _normalize_plan(_team_plan(), registry)
    assert normalized["tasks"][0]["input_refs"] == ["用户问题中的标的"]
    assert normalized["tasks"][0]["output_format"] == "行情观察、时间口径和限制"
    assert normalized["tasks"][1]["failure_strategy"] == "replan"

    dependent = _team_plan()
    dependent["tasks"][1]["depends_on"] = ["market-task"]
    normalized_dependent = _normalize_plan(dependent, registry)
    assert normalized_dependent["tasks"][1]["depends_on"] == ["market-task"]

    cyclic = _team_plan()
    cyclic["tasks"][0]["depends_on"] = ["fundamental-task"]
    cyclic["tasks"][1]["depends_on"] = ["market-task"]
    with pytest.raises(ValueError, match="cycle"):
        _normalize_plan(cyclic, registry)


def test_team_plan_draft_is_materialized_with_server_owned_tools_and_limits() -> None:
    registry = ToolRegistry.from_tools(
        [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
    )
    draft = {
        "progress_text": "模型按两个独立领域拆分核验。",
        "goal": "完成行情与基本面核验",
        "completion_criteria": ["两个领域都完成交接"],
        "tasks": [
            {
                "agent_id": "market",
                "objective": "核验行情",
                "success_criteria": ["返回行情观察"],
            },
            {
                "agent_id": "fundamental",
                "objective": "核验基本面",
                "success_criteria": ["返回基本面观察"],
            },
        ],
    }

    materialized = _materialize_team_plan(
        draft,
        {"team_id": "draft-team", "user_text": "请综合行情与基本面"},
        registry,
    )
    normalized = _normalize_plan(
        TeamPlanDraft.model_validate(draft),
        registry,
        state={"team_id": "draft-team", "user_text": "请综合行情与基本面"},
    )

    assert materialized["plan_id"] == "model-team-draft-team"
    assert [task["task_id"] for task in materialized["tasks"]] == [
        "market-task-1",
        "fundamental-task-2",
    ]
    assert all(task["allowed_tools"] for task in materialized["tasks"])
    # Tool identities are a permission list, not an invocation budget. The
    # same indicator/source tool may be required several times by one task.
    assert all(task["max_tool_calls"] == 12 for task in materialized["tasks"])
    assert all(task["max_tool_calls"] > len(task["allowed_tools"]) for task in materialized["tasks"])
    assert normalized["tasks"][0]["agent_node"] == "MarketAgent"
    assert normalized["tasks"][1]["agent_node"] == "FundamentalAgent"


def test_team_contract_diagnostic_keeps_parser_reason_and_call_shape() -> None:
    raw = AIMessage(
        content="{}",
        tool_calls=[
            {
                "name": "TeamPlanDraft",
                "args": {"goal": "完成核验"},
                "id": "plan-call",
                "type": "tool_call",
            }
        ],
    )
    parser_error = ValueError("tasks.0.success_criteria: Field required")
    diagnostic = _team_contract_diagnostic(
        TeamPlanDraft,
        ValueError("模型没有返回 TeamPlanDraft 结构化对象"),
        {"raw": raw, "parsed": None, "parsing_error": parser_error},
    )

    assert diagnostic["code"] == "team_structured_output_parse_failed"
    assert diagnostic["tool_call_count"] == 1
    assert diagnostic["tool_names"] == ["TeamPlanDraft"]
    assert diagnostic["parser_error_type"] == "ValueError"
    assert "success_criteria" in diagnostic["parser_error"]
    assert diagnostic["argument_keys"] == ["goal"]


def test_planner_tool_hints_do_not_remove_an_experts_registered_research_capabilities() -> None:
    registry = ToolRegistry.from_tools([
        _probe("quote", "financials"), _probe("valuation_history", "financials"),
        _probe("unrelated_news", "news_source"),
    ])
    experts = ExpertRegistry([ExpertDefinition(
        agent_id="fundamental", display_name="基本面", tool_scope=("quote", "valuation_history"),
    )])
    task = {"agent_id": "fundamental", "objective": "当前和历史估值", "tool_hints": ["quote"]}
    plan = _materialize_team_plan({"tasks": [task]}, {}, registry, experts)
    assert plan["tasks"][0]["allowed_tools"] == ["quote", "valuation_history"]
    # A server-provided executable allowlist is still authoritative. The
    # coordinator's optional hints cannot add a capability from another role.
    restricted = _materialize_team_plan({"tasks": [{**task, "allowed_tools": ["quote"]}]}, {}, registry, experts)
    assert restricted["tasks"][0]["allowed_tools"] == ["quote"]
    escaped = _materialize_team_plan({"tasks": [{**task, "tool_hints": ["unrelated_news"]}]}, {}, registry, experts)
    # Keep invalid hints visible for strict plan validation; never authorize
    # another role merely because its tool exists in the global registry.
    assert "unrelated_news" in escaped["tasks"][0]["allowed_tools"]


def test_completion_gate_evaluates_readiness_before_final_answer_exists() -> None:
    messages = team_graph_module._team_criteria_messages({
        "user_text": "现在能买吗", "team_plan": {"completion_criteria": ["给出有依据的建议"]},
        "team_draft": {"summary": "证据已汇合"},
    })
    payload = json.loads(messages[1].content)
    assert payload["scope"] == "synthesis_readiness"
    assert payload["draft"]["summary"] == "证据已汇合"
    assert "不要求后续 FinalSynthesizer 的成品已经存在" in messages[0].content


def test_production_expert_scopes_fit_the_executable_plan_contract() -> None:
    registry = ToolRegistry()
    draft = _team_plan_draft(_team_plan())
    for task in draft["tasks"]:
        task["tool_hints"] = []
    plan = _normalize_plan(draft, registry)
    for task in plan["tasks"]:
        assert set(task["allowed_tools"]) == set(expert_tool_names(registry, task["agent_id"]))
        assert task["max_tool_calls"] == 12
    assert any(len(task["allowed_tools"]) > 16 for task in plan["tasks"])


def test_active_finalizer_has_no_whole_graph_wall_clock_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        registry = ToolRegistry.from_tools([_probe("market_probe", "market"), _probe("fundamental_probe", "financials")])
        manager = LangGraphRuntimeManager(registry=registry)
        await manager.start(testing=True)
        original = team_graph_module.asyncio.wait_for
        async def check_boundary(awaitable, timeout):
            name = getattr(getattr(awaitable, "cr_code", None), "co_qualname", "")
            if "Pregel.ainvoke" in name or "CompiledStateGraph.ainvoke" in name:
                awaitable.close()
                raise AssertionError("whole finalizer graph must not have a wall-clock deadline")
            return await original(awaitable, timeout)
        monkeypatch.setattr(team_graph_module.asyncio, "wait_for", check_boundary)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请并行核验"}], user_text="请并行核验", system_prompt="",
                llm_config={}, database=None, controller=None, run_id="finalizer-deadline",
                conversation_id="finalizer-deadline", run_attempt=1, tenant_id="tenant", owner_id="owner",
                model=RoleAwareTeamModel(), executor=FakeAtomicExecutor(), agent_mode="team",
            )
            assert result.status == "completed"
            assert result.error_code is None
        finally:
            await manager.close()
    asyncio.run(scenario())


def test_team_plan_validator_enforces_server_owned_budget_and_concurrency() -> None:
    registry = ToolRegistry.from_tools(
        [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
    )

    concurrency_limited = _team_plan()
    concurrency_limited["budget"] = {"max_concurrency": 1}
    with pytest.raises(ValueError, match="max_concurrency"):
        _normalize_plan(concurrency_limited, registry)

    tool_budget_limited = _team_plan()
    tool_budget_limited["budget"] = {"max_tool_calls": 1}
    with pytest.raises(ValueError, match="max_tool_calls"):
        _normalize_plan(tool_budget_limited, registry)

    unsupported_budget = _team_plan()
    unsupported_budget["budget"] = {"unbounded_calls": 1}
    with pytest.raises(ValueError, match="unsupported budget"):
        _normalize_plan(unsupported_budget, registry)


def test_model_budget_mismatch_repair_keeps_selected_tasks_and_drops_only_hint() -> None:
    registry = ToolRegistry.from_tools(
        [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
    )
    plan = _team_plan()
    plan["budget"] = {"max_tool_calls": 3}

    with pytest.raises(ValueError, match="above max_tool_calls"):
        _normalize_plan(plan, registry)

    repaired = _repair_advisory_budget_mismatch(
        plan,
        "team plan may require 4 tool calls, above max_tool_calls=3",
        registry,
    )

    assert repaired is not None
    normalized, repaired_keys = repaired
    assert repaired_keys == ["max_tool_calls"]
    assert normalized["budget"] == {}
    assert [task["task_id"] for task in normalized["tasks"]] == [
        "market-task",
        "fundamental-task",
    ]


def test_model_budget_mismatch_repair_removes_all_conflicting_advisory_hints() -> None:
    registry = ToolRegistry.from_tools(
        [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
    )
    plan = _team_plan()
    plan["budget"] = {"max_tool_calls": 3, "max_duration_seconds": 1}

    with pytest.raises(ValueError, match="above max_tool_calls"):
        _normalize_plan(plan, registry)

    repaired = _repair_advisory_budget_mismatch(
        plan,
        "team plan may require 4 tool calls, above max_tool_calls=3",
        registry,
    )

    assert repaired is not None
    normalized, repaired_keys = repaired
    assert repaired_keys == ["max_tool_calls", "max_duration_seconds"]
    assert normalized["budget"] == {}


def test_rejected_team_plan_is_terminal_and_never_dispatches() -> None:
    """A rejected CollaborationPlan must not cross the Send boundary."""
    assert _team_plan_next({"team_status": "blocked", "team_plan": None}) == "team_fail"
    assert _team_plan_next({"team_status": "failed", "team_plan": None}) == "team_fail"
    assert _team_plan_next({"team_status": "planned", "team_plan": {"tasks": []}}) == "team_fail"
    assert _team_plan_next({"team_status": "planned", "team_plan": {"tasks": [{"task_id": "a"}]}}) == "team_dispatch"

    async def scenario() -> None:
        rejected_plan = _team_plan()
        rejected_plan["tasks"][0]["allowed_tools"] = ["tool_not_in_expert_scope"]
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请执行多专家核验"}],
                user_text="请执行多专家核验",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-plan-rejected",
                conversation_id="team-plan-rejected",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=RoleAwareTeamModel(team_plan_override=rejected_plan),
                executor=executor,
                agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "failed"
        assert result.state["team_status"] == "blocked"
        assert result.state["team_dispatched_task_ids"] == []
        assert result.state["team_dispatch_round"] == 0
        assert executor.calls == []
        projected = team_trace(result.state)
        assert projected is not None
        assert projected["status"] == "blocked"
        assert projected["failure"]["status"] == "blocked"
        assert projected["failure"]["dispatch_status"] == "not_started"
        assert not any(
            str(event.get("details", {}).get("dispatched_task_ids") or "").strip()
            for event in result.stage_history or []
            if isinstance(event, dict)
        )

    asyncio.run(scenario())


def test_server_fallback_team_plan_uses_only_registered_read_capabilities() -> None:
    registry = ToolRegistry.from_tools(
        [
            _probe("market_probe", "market"),
            _probe("fundamental_probe", "financials"),
            _probe("news_probe", "news_source"),
        ]
    )

    plan = _fallback_team_plan(
        {"team_id": "fallback-team", "user_text": "请综合行情、基本面和新闻"},
        registry,
    )

    assert [task["agent_id"] for task in plan["tasks"]] == ["market", "fundamental", "news"]
    assert all(task["allowed_tools"] for task in plan["tasks"])
    assert all(
        set(task["allowed_tools"]).issubset(set(expert_tool_names(registry, task["agent_id"])))
        for task in plan["tasks"]
    )
    assert all(not task["depends_on"] for task in plan["tasks"])


def test_agent_report_accepts_a_bounded_team_reexecution_attempt() -> None:
    report = AgentResult(
        id="market-task",
        task_id="market-task",
        agent_id="team-1:market:market-task:attempt-4",
        agent_node="MarketAgent",
        expert_id="market",
        status="partial",
        attempt=4,
        summary="行情方向部分完成。",
    )

    assert report.attempt == 4


def test_registry_mounts_dynamic_expert_node_and_only_selected_tasks_are_sent() -> None:
    registry = ToolRegistry.from_tools(
        [
            _probe("market_probe", "market"),
            _probe("fundamental_probe", "financials"),
            _probe("valuation_probe", "analysis"),
        ]
    )
    experts = ExpertRegistry.default()
    experts.register(
        ExpertDefinition(
            agent_id="valuation",
            display_name="估值分析",
            capabilities=("估值", "相对估值"),
            tool_scope=("valuation_probe",),
            graph_node="ValuationAgent",
        )
    )
    plan = {
        "plan_id": "dynamic-team",
        "goal": "完成行情和估值核验",
        "completion_criteria": ["两个选中专家完成交接"],
        "tasks": [
            {
                "task_id": "market-task",
                "agent_id": "market",
                "objective": "核验行情",
                "allowed_tools": ["market_probe"],
                "success_criteria": ["返回行情观察"],
            },
            {
                "task_id": "valuation-task",
                "agent_id": "valuation",
                "objective": "核验估值",
                "allowed_tools": ["valuation_probe"],
                "success_criteria": ["返回估值观察"],
            },
        ],
        "synthesis_instructions": "按选中专家结果汇总。",
    }

    normalized = _normalize_plan(plan, registry, experts)
    graph = build_team_graph(
        checkpointer=None,
        registry=registry,
        response_format=None,
        expert_registry=experts,
    )
    sends = _plan_next(
        {
            "team_status": "planned",
            "team_id": "dynamic-team",
            "team_plan": normalized,
            "team_results": [],
            "team_dispatched_task_ids": [],
        }
    )

    assert "ValuationAgent" in graph.nodes
    assert [item.node for item in sends] == ["MarketAgent", "ValuationAgent"]
    assert "FundamentalAgent" not in [item.node for item in sends]


def test_checkpoint_graph_selection_reads_team_state_before_direct_graph() -> None:
    """A Team checkpoint must not be inspected through the direct graph first.

    The direct graph does not register expert Send targets. Probing it during
    recovery makes LangGraph discard pending expert packets before the Team
    graph gets a chance to resume them.
    """

    class RawCheckpoint:
        async def aget_tuple(self, _config):
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "orchestrator_mode": "multi_agent_team",
                        "team_id": "team-recovery",
                        "agent_mode": "team",
                    }
                }
            )

    class Graph:
        async def aget_state(self, _config):
            raise AssertionError("raw Team checkpoint should avoid direct graph probing")

    manager = LangGraphRuntimeManager()
    direct_graph = Graph()
    team_graph = object()
    manager.graph = direct_graph
    manager.team_graph = team_graph
    manager.checkpointer = RawCheckpoint()

    assert asyncio.run(manager._graph_for_checkpoint("recovery")) is team_graph


def test_registered_extension_is_not_auto_activated_by_server_fallback() -> None:
    registry = ToolRegistry.from_tools(
        [
            _probe("market_probe", "market"),
            _probe("fundamental_probe", "financials"),
            _probe("valuation_probe", "analysis"),
        ]
    )
    experts = ExpertRegistry.default()
    experts.register(
        ExpertDefinition(
            agent_id="valuation",
            display_name="估值分析",
            capabilities=("估值",),
            tool_scope=("valuation_probe",),
            graph_node="ValuationAgent",
        )
    )

    plan = _fallback_team_plan(
        {"team_id": "fallback-extension", "user_text": "请综合行情和基本面"},
        registry,
        experts,
    )

    assert "valuation" not in [task["agent_id"] for task in plan["tasks"]]


def _bound_tool_names(tools: object) -> list[str]:
    names: list[str] = []
    for tool in tools if isinstance(tools, list) else []:
        if isinstance(tool, dict):
            function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
            name = function.get("name") or tool.get("name")
        else:
            name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if name:
            names.append(str(name))
    return names


class RoleAwareTeamModel(ScriptedChatModel):
    """Deterministic model double that selects the only scoped worker tool."""

    supports_exact_structured_output: bool = True
    structured_output_options: list[dict[str, object]] = Field(default_factory=list, exclude=True)
    team_plan_override: dict[str, object] | None = Field(default=None, exclude=True)
    force_conflict: bool = False
    force_team_route: bool = False
    force_team_criteria_failure: bool = False
    force_worker_criteria_failure: bool = False
    force_conflict_failure: bool = False
    force_critic_failure: bool = False
    force_case_failure: bool = False
    force_consensus_failure: bool = False
    final_answer_override: dict[str, object] | None = Field(default=None, exclude=True)

    def with_structured_output(self, schema, *, include_raw=False, **kwargs):
        self.structured_output_options.append(dict(kwargs))
        # The test double inherits BaseChatModel's generic parser, whose
        # signature does not expose the adapter-only transport options.  Keep
        # those options observable while letting the native parser exercise
        # the same response contract.
        kwargs.pop("tool_choice", None)
        kwargs.pop("stream", None)
        return super().with_structured_output(schema, include_raw=include_raw, **kwargs)

    async def _agenerate(self, messages, **kwargs):
        names = _bound_tool_names(kwargs.get("tools"))
        if "OrchestratorRoute" in names:
            output = _call(
                "OrchestratorRoute",
                "route-call",
                {
                    "mode": "plan",
                    "reason": "测试验证模型选择 Team 执行策略",
                    "progress_text": "模型判断当前问题需要并行核验。",
                    "execution_strategy": "team" if self.force_team_route else "single_agent",
                },
            )
        elif "TeamPlanDraft" in names or "TeamPlan" in names:
            plan = self.team_plan_override or _team_plan()
            schema_name = "TeamPlanDraft" if "TeamPlanDraft" in names else "TeamPlan"
            payload = _team_plan_draft(plan) if schema_name == "TeamPlanDraft" else {
                **plan,
                "progress_text": "模型已按独立证据维度拆分核验任务。",
            }
            output = _call(schema_name, "plan-call", payload)
        elif "PlanningStepReport" in names:
            payload = {}
            content = getattr(messages[-1], "content", "") if messages else ""
            if isinstance(content, str):
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    payload = {}
            active_step = payload.get("active_step") if isinstance(payload.get("active_step"), dict) else {}
            step_id = str(active_step.get("step_id") or "team_research_review")
            output = _report(step_id, ["team-review"], last=True, model_only=True)
        elif "CriteriaAssessment" in names:
            payload = {}
            content = getattr(messages[-1], "content", "") if messages else ""
            if isinstance(content, str):
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    payload = {}
            criteria = [str(item) for item in payload.get("criteria") or []]
            source_ids = [
                int(item.get("source_id"))
                for item in payload.get("eligible_evidence") or []
                if isinstance(item, dict) and item.get("source_id") is not None
            ]
            checks = [
                {
                    "criterion_index": index,
                    "verdict": (
                        "fail"
                        if (
                            self.force_team_criteria_failure and payload.get("scope") == "synthesis_readiness" and index == 1
                        )
                        or (self.force_worker_criteria_failure and payload.get("scope") == "worker_task" and index == 1)
                        else "pass"
                    ),
                    "explanation": "测试模型根据可用证据完成逐项核验",
                    "source_ids": source_ids[:1],
                }
                for index, _criterion in enumerate(criteria, 1)
            ]
            output = _call("CriteriaAssessment", "criteria-call", {"checks": checks})
        elif "CoordinatorHandoffNarration" in names:
            payload = {}
            content = getattr(messages[-1], "content", "") if messages else ""
            if isinstance(content, str):
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    payload = {}
            received = list(payload.get("received_task_ids") or [])
            pending = list(payload.get("pending_task_ids") or [])
            output = _call(
                "CoordinatorHandoffNarration",
                "handoff-call",
                {
                    "progress_text": "主协调器已收到各专家返回的结构化交接，正在继续汇总。",
                    "received_task_ids": [str(item) for item in received[:12]],
                    "pending_task_ids": [str(item) for item in pending[:12]],
                    "next_action": "继续进行证据合并和复核",
                },
            )
        elif "WorkerAssessment" in names:
            output = _call(
                "WorkerAssessment",
                "assessment-call",
                {
                    "summary": "领域观察已完成",
                    "progress_text": "这个领域的可用观察已经整理完成。",
                    "findings": ["观察已返回"],
                    "limitations": [],
                    "open_questions": [],
                    "confidence": "medium",
                },
            )
        elif "ConflictAssessment" in names:
            if self.force_conflict_failure:
                raise ValueError("forced ConflictDetector failure")
            output = _call(
                "ConflictAssessment",
                "conflict-call",
                {
                    "status": "high_risk" if self.force_conflict else "none",
                    "reason": (
                        "测试强制触发高风险门禁" if self.force_conflict else "各领域交接没有发现需要对抗审查的冲突"
                    ),
                    "progress_text": "模型完成了跨领域冲突和风险检查。",
                    "issues": (
                        [{"category": "stance", "severity": "high", "reason": "测试存在多空口径冲突"}]
                        if self.force_conflict
                        else []
                    ),
                    "risk_flags": ["test_conflict"] if self.force_conflict else [],
                    "requires_adversarial_review": self.force_conflict,
                },
            )
        elif "CriticReview" in names:
            if self.force_critic_failure:
                raise ValueError("forced CriticReviewer failure")
            output = _call(
                "CriticReview",
                "critic-call",
                {
                    "verdict": "pass",
                    "summary": "覆盖和证据可以安全综合",
                    "progress_text": "模型复核确认当前证据可以进入综合。",
                    "issues": [],
                },
            )
        elif "BullCaseReview" in names:
            if self.force_case_failure:
                raise ValueError("forced BullCaseReviewer failure")
            output = _call(
                "BullCaseReview",
                "bull-call",
                {
                    "stance": "bull",
                    "summary": "看多审查完成",
                    "progress_text": "模型完成了看多证据与假设的对照。",
                    "arguments": ["存在支撑观察"],
                    "supporting_source_ids": [],
                    "counter_source_ids": [],
                    "assumptions": [],
                    "risks": [],
                    "confidence": "medium",
                },
            )
        elif "BearCaseReview" in names:
            if self.force_case_failure:
                raise ValueError("forced BearCaseReviewer failure")
            output = _call(
                "BearCaseReview",
                "bear-call",
                {
                    "stance": "bear",
                    "summary": "看空审查完成",
                    "progress_text": "模型完成了看空风险与反向证据的对照。",
                    "arguments": ["存在反向风险"],
                    "supporting_source_ids": [],
                    "counter_source_ids": [],
                    "assumptions": [],
                    "risks": [],
                    "confidence": "medium",
                },
            )
        elif "ConsensusResolution" in names:
            if self.force_consensus_failure:
                raise ValueError("forced ConsensusResolver failure")
            output = _call(
                "ConsensusResolution",
                "consensus-call",
                {
                    "verdict": "pass",
                    "conclusion": "双方审查可以形成受限共识",
                    "rationale": "证据范围一致",
                    "progress_text": "模型已标记共识范围和仍需保留的风险。",
                    "source_ids": [],
                    "unresolved_conflicts": [],
                    "confidence": "medium",
                },
            )
        elif STRUCTURED_OUTPUT_TOOL_NAME in names:
            answer_blocks = [
                {
                    "kind": "answer",
                    "content": "综合后的回答：行情观察已完成",
                    "source_ids": [1],
                },
                {
                    "section": "基本面核验",
                    "kind": "answer",
                    "content": "基本面观察已完成",
                    "source_ids": [2],
                },
            ]
            if any(
                isinstance(task, dict) and task.get("agent_id") == "news"
                for task in (self.team_plan_override or {}).get("tasks") or []
            ):
                answer_blocks.append(
                    {
                        "section": "新闻核验",
                        "kind": "answer",
                        "content": "新闻观察已完成",
                        "source_ids": [3],
                    }
                )
            output = _call(
                STRUCTURED_OUTPUT_TOOL_NAME,
                "answer-call",
                self.final_answer_override if self.final_answer_override is not None else {
                    "profile": "general",
                    "title": "协作结果",
                    "blocks": answer_blocks,
                },
            )
        else:
            worker_tools = [name for name in names if name in {"market_probe", "fundamental_probe", "news_probe"}]
            if not worker_tools:
                raise AssertionError(f"unexpected bound tools: {names}")
            tool_name = worker_tools[0]
            if any(isinstance(message, ToolMessage) for message in messages):
                output = AIMessage(content=f"{tool_name} 观察已完成")
            else:
                output = _call(tool_name, f"{tool_name}-call", {"query": tool_name})
        self.responses.append(output)
        return await super()._agenerate(messages, **kwargs)


class RecoverableTeamModel(RoleAwareTeamModel):
    """Model double whose market worker has two durable super-steps."""

    async def _agenerate(self, messages, **kwargs):
        names = _bound_tool_names(kwargs.get("tools"))
        if "TeamPlanDraft" in names or "TeamPlan" in names:
            schema_name = "TeamPlanDraft" if "TeamPlanDraft" in names else "TeamPlan"
            plan = _recoverable_team_plan()
            payload = _team_plan_draft(plan) if schema_name == "TeamPlanDraft" else plan
            output = _call(schema_name, "plan-call", payload)
        elif {"market_probe_a", "market_probe_b"}.issubset(set(names)):
            tool_names = {
                str(getattr(message, "name", ""))
                for message in messages
                if isinstance(message, ToolMessage)
            }
            if "market_probe_b" in tool_names:
                output = AIMessage(content="market 两步观察已完成")
            elif "market_probe_a" in tool_names:
                output = _call("market_probe_b", "market-b-call", {"query": "market_probe_b"})
            else:
                output = _call("market_probe_a", "market-a-call", {"query": "market_probe_a"})
        else:
            return await super()._agenerate(messages, **kwargs)
        self.responses.append(output)
        return await ScriptedChatModel._agenerate(self, messages, **kwargs)


class BlockOnNamedToolExecutor(FakeAtomicExecutor):
    def __init__(self, blocked_tool: str) -> None:
        super().__init__()
        self.blocked_tool = blocked_tool
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, action, *, approved=False):
        if str(action.get("tool_name")) == self.blocked_tool:
            self.started.set()
            await self.release.wait()
        return await super().execute(action, approved=approved)


def test_worker_scope_is_deny_by_default_and_prefixes_action_identity() -> None:
    async def scenario() -> None:
        dynamic_effect = ToolSpec(
            name="dynamic_effect",
            description="参数决定副作用的测试工具",
            parameters=object_schema({"query": {"type": "string"}}, required=("query",)),
            executor=lambda **_kwargs: {"success": True},
            effect_resolver=lambda _arguments: "side_effect",
            category="market",
        )
        registry = ToolRegistry.from_tools(
            [_probe("market_probe", "market"), _probe("fundamental_probe", "financials"), dynamic_effect]
        )
        assert expert_tool_names(registry, "market") == ["market_probe"]
        scope = ScopedToolRegistry(registry, expert_tool_names(registry, "market"))
        assert scope.get_tool_names() == ["market_probe"]
        assert scope.get_tool("fundamental_probe") is None
        assert scope.get_tool("dynamic_effect") is None
        schemas = scope.get_all_schemas(include_server_controlled=True)
        assert [item["function"]["name"] for item in schemas] == ["market_probe"]

        executor = FakeAtomicExecutor()
        scoped = TaskScopedExecutor(
            executor,
            task_id="market-task",
            agent_id="team:market:market-task",
            expert_id="market",
        )
        record, evidence = await scoped.execute(
            {
                "action_id": "call-1",
                "tool_name": "market_probe",
                "arguments": {"query": "行情"},
            }
        )
        assert executor.calls[0]["action_id"] == "team:market:market-task:call-1"
        assert record["task_id"] == "market-task"
        assert evidence is not None and evidence["agent_id"] == "team:market:market-task"

    asyncio.run(scenario())


def test_evidence_merger_rejects_worker_forged_ids() -> None:
    merge, catalog = merge_worker_evidence(
        [
            {"evidence_id": "ev_canonical", "success": True, "has_data": True, "result": {"value": 1}},
            {"evidence_id": "ev_canonical", "success": True, "has_data": True, "result": {"value": 2}},
        ],
        [
            {
                "task_id": "market-task",
                "status": "completed",
                "evidence_ids": ["ev_forged", "ev_canonical"],
            }
        ],
    )

    assert len(catalog) == 1
    assert merge.duplicate_count == 1
    assert merge.status == "partial"
    assert merge.evidence_ids == ["ev_canonical"]
    assert merge.worker_evidence == {"market-task": ["ev_canonical"]}
    assert merge.invalid_evidence_ids == ["ev_forged"]


def test_reviewers_reject_unknown_evidence_instead_of_silently_dropping_it() -> None:
    state = {"team_evidence_merge": {"evidence_ids": ["ev-real"]}}
    with pytest.raises(ValueError, match="unavailable evidence source ids"):
        _normalize_conflict(
            {
                "status": "conflict",
                "reason": "存在冲突",
                "issues": [
                    {
                        "category": "claim",
                        "severity": "high",
                        "reason": "证据引用异常",
                        "source_ids": [99],
                    }
                ],
                "risk_flags": [],
                "requires_adversarial_review": True,
            },
            state,
        )
    with pytest.raises(ValueError, match="unavailable evidence source ids"):
        _normalize_case(
            {
                "stance": "bull",
                "summary": "看多结论",
                "supporting_source_ids": [99],
                "counter_source_ids": [],
                "arguments": [],
                "assumptions": [],
                "risks": [],
                "confidence": "low",
            },
            state,
            BullCaseReview,
        )
    with pytest.raises(ValueError, match="unavailable evidence source ids"):
        _normalize_consensus(
            {
                "verdict": "pass",
                "conclusion": "形成共识",
                "rationale": "证据一致",
                "source_ids": [99],
                "unresolved_conflicts": [],
                "confidence": "low",
            },
            state,
        )


def test_reviewers_resolve_source_slots_to_canonical_evidence_ids() -> None:
    evidence = [
        {"evidence_id": "ev-market", "success": True, "has_data": True, "result": {"value": 1}},
        {"evidence_id": "ev-fundamental", "success": True, "has_data": True, "result": {"value": 2}},
    ]
    state = {
        "team_evidence_catalog": evidence,
        "evidence": evidence,
        "team_evidence_merge": {
            "status": "completed",
            "evidence_ids": ["ev-market", "ev-fundamental"],
        },
    }

    conflict = _normalize_conflict(
        {
            "status": "conflict",
            "reason": "行情与基本面需要对照",
            "issues": [
                {
                    "category": "stance",
                    "severity": "high",
                    "reason": "两条观察口径不同",
                    "source_ids": [2],
                }
            ],
            "risk_flags": [],
            "requires_adversarial_review": True,
        },
        state,
    )
    assert conflict["issues"][0]["source_ids"] == [2]

    case = _normalize_case(
        {
            "stance": "bull",
            "summary": "看多审查",
            "supporting_source_ids": [1],
            "counter_source_ids": [2],
            "arguments": ["存在支持观察"],
            "assumptions": [],
            "risks": [],
            "confidence": "medium",
        },
        state,
        BullCaseReview,
    )
    assert case["supporting_source_ids"] == [1]
    assert case["counter_source_ids"] == [2]

    consensus = _normalize_consensus(
        {
            "verdict": "pass",
            "conclusion": "形成受限共识",
            "rationale": "两侧均有可追溯观察",
            "source_ids": [1, 2],
            "unresolved_conflicts": [],
            "confidence": "medium",
        },
        state,
    )
    assert consensus["source_ids"] == [1, 2]
    assert consensus["allow_final_answer"] is True


def test_worker_handoff_status_allows_usable_evidence_with_optional_tool_failure() -> None:
    assert (
        _worker_handoff_status(
            child_status="partial",
            assessment_status="typed",
            criteria_status="passed",
            evidence_count=6,
        )
        == "completed"
    )
    assert (
        _worker_handoff_status(
            child_status="completed",
            assessment_status="typed",
            criteria_status="blocked",
            evidence_count=6,
        )
        == "partial"
    )
    assert (
        _worker_handoff_status(
            child_status="partial",
            assessment_status="typed",
            criteria_status="passed",
            evidence_count=0,
        )
        == "partial"
    )
    assert (
        _worker_handoff_status(
            child_status="failed",
            assessment_status="typed",
            criteria_status="passed",
            evidence_count=6,
        )
        == "partial"
    )


def test_atomic_executor_routes_worker_events_to_private_bridge() -> None:
    class Handle:
        def append_args_text(self, _value: str) -> None:
            return None

        def set_response(self, _result, *, is_error: bool = False) -> None:
            return None

        def close(self) -> None:
            return None

    class Controller:
        def __init__(self) -> None:
            self.data: list[dict] = []
            self.tool_calls: list[str] = []

        def add_data(self, payload: dict) -> None:
            self.data.append(payload)

        async def add_tool_call(self, tool_name: str, **_kwargs):
            self.tool_calls.append(tool_name)
            return Handle()

    async def scenario() -> None:
        registry = ToolRegistry.from_tools(
            [
                ToolSpec(
                    name="market_probe",
                    description="测试行情工具",
                    parameters=object_schema(
                        {"query": {"type": "string"}},
                        required=("query",),
                    ),
                    executor=lambda **_kwargs: {
                        "success": True,
                        "result": {"value": "observed"},
                        "source_refs": ["https://source.example/market"],
                    },
                    category="market",
                )
            ]
        )
        controller = Controller()
        parent = GraphEventBridge(controller, run_id="atomic-team")
        child = TeamWorkerEventBridge(
            parent,
            team_id="team-atomic",
            task_id="market-task",
            agent_id="team-atomic:market:market-task",
            expert_id="market",
        )
        atomic = AtomicToolExecutor(
            registry,
            database=None,
            run_id="atomic-team",
            conversation_id="atomic-conversation",
            controller=parent,
            events=parent,
            compact_result=lambda _name, value: value,
            attach_fallback=lambda _name, _arguments, value: value,
        )
        scoped = TaskScopedExecutor(
            atomic,
            task_id="market-task",
            agent_id="team-atomic:market:market-task",
            expert_id="market",
            events=child,
            controller=child,
        )
        record, _evidence = await scoped.execute(
            {
                "action_id": "call-1",
                "tool_name": "market_probe",
                "arguments": {"query": "行情"},
            }
        )

        assert record["action_id"] == "team-atomic:market:market-task:call-1"
        assert controller.tool_calls == ["market_probe"]
        tool_stages = [item for item in controller.data if item.get("stage") == "tool"]
        assert {item.get("status") for item in tool_stages} == {"started", "completed"}
        assert all(item.get("action_id") == "team-atomic:market:market-task:call-1" for item in tool_stages)
        assert all(item.get("details", {}).get("expert_id") == "market" for item in tool_stages)

    asyncio.run(scenario())


def test_team_path_runs_workers_review_and_existing_answer_contract() -> None:
    async def scenario() -> None:
        model = RoleAwareTeamModel()
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        await manager.start(testing=True)
        assert manager.team_graph is not None
        assert {
            "MarketAgent",
            "FundamentalAgent",
            "NewsResearchAgent",
        }.issubset(manager.team_graph.nodes)
        assert "team_dispatch" in manager.team_graph.nodes
        assert "worker_handoff" in manager.team_graph.nodes
        assert "worker_failure_policy" in manager.team_graph.nodes
        assert "evidence_merger" in manager.team_graph.nodes
        assert "conflict_detector" in manager.team_graph.nodes
        assert "critic_reviewer" in manager.team_graph.nodes
        assert "bull_case_reviewer" in manager.team_graph.nodes
        assert "bear_case_reviewer" in manager.team_graph.nodes
        assert "consensus_resolver" in manager.team_graph.nodes
        assert "completion_criteria_validator" in manager.team_graph.nodes
        assert "team_synthesizer" in manager.team_graph.nodes
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请综合行情与基本面"}],
                user_text="请综合行情与基本面",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-run",
                conversation_id="team-regression",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=FakeAtomicExecutor(),
                agent_mode="team",
            )
            assert await manager._graph_for_checkpoint("team-regression") is manager.team_graph
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.error_code is None
        assert result.state["orchestrator_mode"] == "multi_agent_team"
        assert result.state["agent_mode"] == "team"
        assert result.state["orchestrator_route"] == "team"
        assert result.state["orchestrator_execution_strategy"] == "team"
        assert result.state["team_status"] == "completed"
        assert result.state["team_failure_policy_action"] == "merge"
        assert result.state["team_worker_handoff_status"] == "completed"
        assert set(result.state["team_worker_handoff_task_ids"]) == {
            item["task_id"] for item in result.state["team_results"]
        }
        assert len(result.state["team_results"]) == 2
        assert {item["agent_node"] for item in result.state["team_results"]} == {
            "MarketAgent",
            "FundamentalAgent",
        }
        assert all(item["agent_id"].startswith("team-team-run:") for item in result.state["team_results"])
        assert result.state["team_review_gate_status"] == "completed"
        assert result.state["team_conflict_status"] == "completed"
        assert result.state["team_conflict_assessment"]["status"] == "none"
        assert result.state["team_critic_status"] == "completed"
        assert result.state["team_criteria_status"] == "passed"
        assert result.state["team_criteria_assessment"]["checks"][0]["criterion"] == "两个领域都完成交接"
        assert result.state["team_bull_case_status"] == "not_started"
        assert result.state["team_bear_case_status"] == "not_started"
        assert result.state["team_consensus_status"] == "not_started"
        assert all(
            options.get("tool_choice") != "required"
            for options in model.structured_output_options
        )
        assert any(
            options.get("tool_choice") == "WorkerAssessment"
            for options in model.structured_output_options
        )
        assert result.final_text.startswith("# 协作结果\n\n综合后的回答")
        assert "证据" in result.final_text
        assert any(
            item.get("stage") == "reflection" and item.get("status") == "completed"
            for item in result.stage_history or []
        )
        assert all(
            "user_message" not in (item.get("details") or {})
            for item in result.stage_history or []
            if item.get("details")
        )
        projected = team_trace(result.state)
        assert projected is not None
        assert projected["worker_count"] == 2
        assert len(projected["results"]) == 2
        assert projected["agent_mode"] == "team"
        assert projected["criteria_status"] == "passed"
        assert projected["worker_handoff"]["status"] == "completed"
        assert projected["failure_policy"]["action"] == "merge"

    asyncio.run(scenario())


def test_team_completion_criteria_gate_publishes_partial_when_a_criterion_is_not_met() -> None:
    async def scenario() -> None:
        model = RoleAwareTeamModel()
        model.force_team_criteria_failure = True
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请综合行情与基本面"}],
                user_text="请综合行情与基本面",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-criteria-failure",
                conversation_id="team-criteria-failure",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=FakeAtomicExecutor(),
                agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "partial"
        assert result.error_code == "team_completion_criteria_not_met"
        assert result.state["team_criteria_status"] == "partial"
        assert result.state["team_criteria_assessment"]["unmet_criteria"] == ["两个领域都完成交接"]
        assert result.state["team_criteria_assessment"]["checks"][0]["verdict"] == "fail"
        projected = team_trace(result.state)
        assert projected is not None
        assert projected["criteria_status"] == "partial"
        assert projected["criteria_assessment"]["checks"][0]["criterion"] == "两个领域都完成交接"

    asyncio.run(scenario())


def test_team_trace_projects_nested_handoff_without_leaking_urls() -> None:
    projected = team_trace(
        {
            "orchestrator_mode": "multi_agent_team",
            "agent_mode": "team",
            "resolved_agent_mode": "team",
            "orchestrator_route": "team",
            "orchestrator_execution_strategy": "team",
            "team_plan": {
                "plan_id": "plan-1",
                "goal": "完成研究",
                "completion_criteria": ["证据完整"],
                "tasks": [{"task_id": "market", "agent_id": "market", "objective": "核验行情"}],
            },
            "team_results": [],
            "team_evidence_merge": {
                "status": "completed",
                "summary": "参考 https://example.test/private",
                "evidence_ids": ["ev-real"],
                "worker_evidence": {"market": ["ev-real"]},
                "finding_evidence": {"market:1": ["ev-real"]},
                "limitations": [],
            },
            "team_critic_review": {
                "verdict": "pass",
                "summary": "参考 https://example.test/critic",
                "issues": [],
            },
            "team_status": "completed",
            "team_worker_count": 1,
            "team_completed_worker_count": 1,
        }
    )

    assert projected is not None
    serialized = json.dumps(projected, ensure_ascii=False)
    assert "https://example.test" not in serialized
    assert projected["evidence_merge"]["worker_evidence"] == {"market": ["ev-real"]}
    assert projected["critic"]["summary"] == "参考 [链接已隐藏]"


def test_team_synthesis_rejects_chart_only_answer_for_required_domains() -> None:
    issues = team_synthesis_contract_issues(
        {
            "blocks": [
                {
                    "section": "行情图表",
                    "kind": "fact",
                    "content": "贵州茅台近期收盘价走势。",
                }
            ]
        },
        required_experts=["market", "fundamental", "news"],
    )

    assert "综合器没有保留基本面方向的实质性区块。" in issues
    assert "综合器没有保留新闻方向的实质性区块。" in issues


def test_team_review_report_keeps_worker_domains_without_becoming_an_answer() -> None:
    state = {
        "team_plan": {
            "tasks": [
                {"task_id": "market", "agent_id": "market"},
                {"task_id": "fundamental", "agent_id": "fundamental"},
                {"task_id": "news", "agent_id": "news"},
            ]
        },
        "team_results": [
            {
                "agent_id": "market",
                "status": "partial",
                "summary": "行情观察已取得。",
                "findings": ["收盘价观察"],
                "evidence_ids": ["ev-market"],
            },
            {
                "agent_id": "fundamental",
                "status": "completed",
                "summary": "基本面观察已取得。",
                "findings": ["财务观察"],
                "evidence_ids": ["ev-fundamental"],
            },
            {
                "agent_id": "news",
                "status": "completed",
                "summary": "新闻观察已取得。",
                "findings": ["公告观察"],
                "evidence_ids": ["ev-news"],
            },
        ],
        "team_evidence_merge": {"status": "partial", "missing_task_ids": ["market"]},
    }
    evidence = [
        {"evidence_id": "ev-market", "success": True, "effect": "read", "result": {"value": 1}},
        {"evidence_id": "ev-fundamental", "success": True, "effect": "read", "result": {"value": 2}},
        {"evidence_id": "ev-news", "success": True, "effect": "read", "result": {"value": 3}},
    ]
    answer = build_team_review_report(state, evidence=evidence)

    sections = [str(block.get("section") or "") for block in answer["blocks"]]
    assert sections[:3] == ["行情核验", "基本面核验", "新闻核验"]
    assert answer["title"] == "研究交接与核验记录"
    assert "profile" not in answer
    assert not any(block.get("chart_refs") for block in answer["blocks"])
    assert all(
        evidence_id in {"ev-market", "ev-fundamental", "ev-news"}
        for block in answer["blocks"]
        for evidence_id in block.get("evidence_ids") or []
    )


def test_team_review_matches_namespaced_reports_to_plan_tasks() -> None:
    answer = build_team_review_report(
        {
            "team_plan": {
                "tasks": [
                    {"task_id": "market-task", "agent_id": "market"},
                    {"task_id": "fundamental-task", "agent_id": "fundamental"},
                ]
            },
            "team_results": [
                {
                    "task_id": "market-task",
                    "agent_id": "team:run-1:market:market-task",
                    "expert_id": "market",
                    "status": "partial",
                    "summary": "行情交接已保留。",
                },
                {
                    "task_id": "fundamental-task",
                    "agent_id": "team:run-1:fundamental:fundamental-task",
                    "expert_id": "fundamental",
                    "status": "completed",
                    "summary": "基本面交接已保留。",
                },
            ],
        }
    )

    blocks = {str(block.get("section")): str(block.get("content")) for block in answer["blocks"]}
    assert "本轮没有收到行情方向的 worker 交接。" not in blocks["行情核验"]
    assert "行情交接已保留。" in blocks["行情核验"]
    assert "基本面交接已保留。" in blocks["基本面核验"]


@pytest.mark.parametrize("invalid_answer", [False, True])
def test_synthesis_gap_never_publishes_internal_review_as_the_answer(invalid_answer: bool) -> None:
    from src.agent.run_registry import RunBroadcaster

    async def scenario() -> None:
        model = RoleAwareTeamModel(final_answer_override={
            "profile": "general",
            "title": "针对用户问题的结论",
            "blocks": [] if invalid_answer else [{
                "section": "结论", "kind": "answer",
                "content": "行情观察已完成", "source_ids": [1],
            }],
        })
        broadcaster = RunBroadcaster()
        manager = LangGraphRuntimeManager(registry=ToolRegistry.from_tools([
            _probe("market_probe", "market"), _probe("fundamental_probe", "financials"),
        ]))
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请综合行情与基本面"}],
                user_text="请综合行情与基本面", system_prompt="", llm_config={},
                database=None, controller=broadcaster, run_id="review-ownership",
                conversation_id="review-ownership", run_attempt=1, tenant_id="tenant", owner_id="owner",
                model=model, executor=FakeAtomicExecutor(), agent_mode="team",
            )
        finally:
            await manager.close()
        assert result.status == "partial"
        answer = result.state["answer_final"]
        final_publish = [event for event in result.stage_history if event.get("stage") == "publish"
                         and str(event.get("action_id") or "").endswith(":synthesis")]
        assert len(final_publish) == 1 and final_publish[0]["status"] == "completed"
        assert not any(event.get("stage") == "publish" and "coverage-check" in str(event.get("action_id"))
                       for event in result.stage_history)
        assert "已交接观察" not in answer
        assert "核验状态" not in answer
        assert "多智能体研究结果" not in answer
        if invalid_answer:
            assert "未能完成对你问题的最终回答" in answer
            assert result.state["structured_answer"] is None
        else:
            assert "针对用户问题的结论" in answer
            assert "行情观察已完成" in answer
            assert len(result.state["structured_answer"]["blocks"]) == 1
        reports = [part for part in broadcaster.display_parts_snapshot() if part.get("name") == "team-review-report"]
        assert len(reports) == 1
        assert reports[0]["data"]["scope"] == "review"
        assert "已交接观察" in str(reports[0]["data"])

    asyncio.run(scenario())


def test_server_criteria_validation_canonicalizes_criteria_and_fails_closed() -> None:
    evidence = [
        {
            "evidence_id": "ev-real",
            "success": True,
            "effect": "read",
            "result": {"value": "observed"},
        }
    ]
    records = [{"success": True, "effect": "read"}]
    passed = validate_criteria_assessment(
        {
            "checks": [
                {
                    "criterion_index": 1,
                    "criterion": "模型伪造的条件文本",
                    "verdict": "pass",
                    "explanation": "真实观察支持",
                    "source_ids": [1],
                }
            ]
        },
        criteria=["服务端计划原文"],
        evidence=evidence,
        records=records,
    )
    assert passed["status"] == "passed"
    assert passed["checks"][0]["criterion"] == "服务端计划原文"

    blocked = validate_criteria_assessment(
        {
            "checks": [
                {
                    "criterion_index": 1,
                    "verdict": "pass",
                    "explanation": "没有给出证据编号",
                    "source_ids": [],
                }
            ]
        },
        criteria=["需要证据"],
        evidence=evidence,
        records=records,
    )
    assert blocked["status"] == "blocked"
    assert blocked["checks"][0]["verdict"] == "unknown"

    with pytest.raises(CriteriaValidationError, match="unavailable evidence"):
        validate_criteria_assessment(
            {
                "checks": [
                    {
                        "criterion_index": 1,
                        "verdict": "pass",
                        "explanation": "伪造编号",
                        "source_ids": [99],
                    }
                ]
            },
            criteria=["需要证据"],
            evidence=evidence,
            records=records,
        )


def test_review_contract_accepts_catalog_sized_evidence_not_provider_annotations() -> None:
    evidence = [{"evidence_id": f"ev-{i}", "success": True, "effect": "read", "result": {"value": i}} for i in range(1, 30)]
    check = {"criterion_index": 1, "verdict": "pass", "explanation": "跨三个领域的证据支持", "source_ids": list(range(1, 30)), "confidence": "high"}
    value = validate_criteria_assessment({"checks": [check]}, criteria=["跨领域综合"], evidence=evidence, records=[])
    assert value["status"] == "passed"
    assert len(value["checks"][0]["source_ids"]) == len(value["checks"][0]["evidence_ids"]) == 29
    assert "confidence" not in value["checks"][0]
    with pytest.raises(CriteriaValidationError, match="unavailable evidence"):
        validate_criteria_assessment({"checks": [{**check, "source_ids": [99]}]}, criteria=["跨领域综合"], evidence=evidence, records=[])
    with pytest.raises(CriteriaValidationError):
        validate_criteria_assessment({"checks": [{**check, "verdict": "invented"}]}, criteria=["跨领域综合"], evidence=evidence, records=[])
    issue = TeamReviewIssue.model_validate({"id": "provider-note", "confidence": "medium", "reason": "来源未披露", "resolution": "qualify"})
    assert "id" not in issue.model_dump() and "confidence" not in issue.model_dump()
    with pytest.raises(ValueError):
        TeamReviewIssue.model_validate({"resolution": "一段没有分类的文字", "confidence": "medium"})


def test_empty_criterion_cannot_be_replaced_by_model_text() -> None:
    with pytest.raises(CriteriaValidationError, match="only non-empty"):
        validate_criteria_assessment(
            {
                "checks": [
                    {
                        "criterion_index": 1,
                        "criterion": "模型伪造的条件",
                        "verdict": "pass",
                        "explanation": "看起来完成了",
                        "source_ids": [1],
                    },
                    {
                        "criterion_index": 2,
                        "criterion": "模型伪造的空条件",
                        "verdict": "pass",
                        "explanation": "看起来完成了",
                        "source_ids": [1],
                    },
                ]
            },
            criteria=["真实完成条件", ""],
            evidence=[],
            records=[],
        )


def test_worker_success_criteria_gate_marks_worker_partial_before_team_merge() -> None:
    async def scenario() -> None:
        model = RoleAwareTeamModel()
        model.force_worker_criteria_failure = True
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请综合行情与基本面"}],
                user_text="请综合行情与基本面",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-worker-criteria-failure",
                conversation_id="team-worker-criteria-failure",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=FakeAtomicExecutor(),
                agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "partial"
        assert result.error_code == "team_worker_incomplete"
        assert {item["criteria_status"] for item in result.state["team_results"]} == {"partial"}
        assert all(item["status"] == "partial" for item in result.state["team_results"])
        assert result.state["team_failure_policy_action"] == "replan"
        assert result.state["team_worker_handoff_status"] == "partial"
        assert set(result.state["team_worker_handoff_incomplete_task_ids"]) == {
            item["task_id"] for item in result.state["team_results"]
        }
        assert "未完成领域结果" in result.state["team_worker_handoff_error"]
        assert result.state["planning_status"] == "not_started"
        assert result.state["team_evidence_merge_status"] == "partial"
        assert result.state["team_criteria_status"] == "partial"
        # Overall incompleteness cannot rewrite individually verified checks,
        # nor replace a valid synthesis with raw worker handoffs.
        assert all(check["verdict"] == "pass" for check in result.state["team_criteria_assessment"]["checks"])
        assert "综合后的回答" in result.state["structured_answer"]["blocks"][0]["content"]
        assert result.state["team_reexecution_status"] == "blocked"
        # The second review observes that the replan-only task has already
        # reached its own max_attempts; no second reexecution round is sent.
        assert result.state["team_reexecution_round"] == 1

    asyncio.run(scenario())


def test_team_child_checkpoint_survives_sqlite_restart_and_resumes_only_pending_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("AGENT_CHECKPOINT_DATABASE_URL", str(tmp_path / "team-recovery.sqlite3"))
        registry = ToolRegistry.from_tools(
            [
                _probe("market_probe_a", "market"),
                _probe("market_probe_b", "market"),
                _probe("fundamental_probe", "financials"),
            ]
        )
        first = LangGraphRuntimeManager(registry=registry)
        await first.start()
        first_model = RecoverableTeamModel()
        first_executor = BlockOnNamedToolExecutor("market_probe_b")
        run = asyncio.create_task(
            first.run_new(
                messages=[{"role": "user", "content": "请综合行情与基本面"}],
                user_text="请综合行情与基本面",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="sqlite-team-run",
                conversation_id="sqlite-team-recovery",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=first_model,
                executor=first_executor,
                agent_mode="team",
            )
        )
        try:
            await asyncio.wait_for(first_executor.started.wait(), timeout=10)
            assert {item["tool_name"] for item in first_executor.calls} == {
                "market_probe_a",
                "fundamental_probe",
            }
            run.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run
        finally:
            if not run.done():
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
            await first.close()

        second = LangGraphRuntimeManager(registry=registry)
        await second.start()
        second_executor = FakeAtomicExecutor()
        try:
            recovered = await second.recover(
                llm_config={},
                database=None,
                controller=None,
                run_id="sqlite-team-recovery-attempt",
                conversation_id="sqlite-team-recovery",
                run_attempt=2,
                tenant_id="tenant",
                owner_id="owner",
                model=RecoverableTeamModel(),
                executor=second_executor,
            )
        finally:
            await second.close()

        assert recovered.status == "completed"
        assert recovered.error_code is None
        assert {item["status"] for item in recovered.state["team_results"]} == {"completed"}
        assert [item["tool_name"] for item in second_executor.calls] == ["market_probe_b"]

    asyncio.run(scenario())


def test_explicit_direct_mode_bypasses_auto_router_and_planning() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(responses=[AIMessage(content="Direct 模式回答")])
        manager = LangGraphRuntimeManager(registry=ToolRegistry.from_tools([]), response_format=None)
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "解释一个概念"}],
                user_text="解释一个概念",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="explicit-direct",
                conversation_id="explicit-direct",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                agent_mode="direct",
                planning_mode="planned",
            )
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.final_text == "Direct 模式回答"
        assert result.state["agent_mode"] == "direct"
        assert result.state["orchestrator_mode"] == "direct_agent_loop"
        assert result.state["planning_enabled"] is False
        assert result.state["planning_mode"] == "direct"
        assert result.state["team_plan"] is None
        assert len(model.calls) == 1

    asyncio.run(scenario())


def test_explicit_plan_mode_uses_planning_coordinator_without_team_route() -> None:
    async def scenario() -> None:
        user_text = "请按步骤核验研究事实"
        model = ScriptedChatModel(
            responses=[
                _planner_call("explicit-plan-call", [_step("first", "核验研究事实")]),
                _named_tool_call(
                    "read",
                    "search_source",
                    {"source_id": "primary", "query": "研究事实"},
                ),
                _report("first", ["read"], last=True),
                _structured_output_call(
                    "final",
                    [{"kind": "fact", "content": "计划模式结论", "source_ids": [1]}],
                    profile="research",
                ),
            ]
        )
        manager = LangGraphRuntimeManager(registry=_registry(_search_operation()))
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": user_text}],
                user_text=user_text,
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="explicit-plan",
                conversation_id="explicit-plan",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                agent_mode="plan",
                planning_mode="direct",
            )
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.state["agent_mode"] == "plan"
        assert result.state["orchestrator_mode"] == "direct_agent_loop"
        assert result.state["planning_enabled"] is True
        assert result.state["planning_mode"] == "planned"
        assert result.state["planning_status"] == "completed"
        assert result.state["team_plan"] is None
        assert result.state["team_status"] == "not_started"
        assert len(executor.calls) == 1
        assert team_trace(result.state) is None

    asyncio.run(scenario())


def test_team_path_executes_scoped_tools_in_parallel_and_keeps_evidence_identity() -> None:
    async def scenario() -> None:
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        await manager.start(testing=True)
        executor = FakeAtomicExecutor(delay_seconds=0.01)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请并行核验"}],
                user_text="请并行核验",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-tools",
                conversation_id="team-tools-regression",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=RoleAwareTeamModel(),
                executor=executor,
        agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.error_code is None
        assert executor.max_active == 2
        assert {item["tool_name"] for item in executor.calls} == {"market_probe", "fundamental_probe"}
        assert all(item["action_id"].startswith("team-team-tools:") for item in executor.calls)
        assert all(item["agent_id"].startswith("team-team-tools:") for item in result.state["team_results"])
        assert {item["agent_node"] for item in result.state["team_results"]} == {
            "MarketAgent",
            "FundamentalAgent",
        }
        projected = team_trace(result.state)
        assert projected is not None
        evidence_ids = {evidence_id for item in projected["results"] for evidence_id in item["evidence_ids"]}
        assert all(evidence_id.startswith("ev_team-team-tools:") for evidence_id in evidence_ids)
        assert not any(
            item.get("stage") == "publish" and item.get("details", {}).get("expert_id") in {"market", "fundamental"}
            for item in result.stage_history or []
        )

    asyncio.run(scenario())


def test_worker_failure_policy_retries_only_the_failed_worker() -> None:
    async def scenario() -> None:
        plan = _team_plan()
        plan["tasks"][0].update({"failure_strategy": "retry", "max_attempts": 2})
        plan["tasks"][1]["failure_strategy"] = "partial"
        model = RoleAwareTeamModel(team_plan_override=plan)
        executor = FakeAtomicExecutor(
            outcomes={
                "market_probe": [
                    {"success": False, "error_code": "temporary_source_error"},
                    {"success": True},
                ]
            }
        )
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请重试失败的行情方向并完成综合核验"}],
                user_text="请重试失败的行情方向并完成综合核验",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-worker-retry",
                conversation_id="team-worker-retry",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.state["team_failure_policy_action"] == "merge"
        assert result.state["team_task_attempts"]["market-task"] == 2
        assert result.state["team_task_attempts"]["fundamental-task"] == 1
        assert [item["tool_name"] for item in executor.calls].count("market_probe") == 2
        assert [item["tool_name"] for item in executor.calls].count("fundamental_probe") == 1
        market_records = [item for item in result.state["tool_results"] if item.get("task_id") == "market-task"]
        assert any(item.get("success") is False and "attempt-1" in item["agent_id"] for item in market_records)
        assert any(item.get("success") is True and "attempt-2" in item["agent_id"] for item in market_records)
        assert {item["attempt"] for item in result.state["team_results"] if item["task_id"] == "market-task"} == {2}
        retry_events = [
            item
            for item in result.stage_history
            if item.get("details", {}).get("policy") == "WorkerFailurePolicy"
            and item.get("details", {}).get("action") == "retry"
        ]
        assert retry_events
        assert retry_events[0]["details"]["retry_task_ids"] == ["market-task"]

    asyncio.run(scenario())


def test_team_send_refuses_to_bypass_task_attempt_limit() -> None:
    plan = _team_plan()
    plan["tasks"][0]["max_attempts"] = 2
    state = {
        "team_id": "attempt-limit",
        "team_plan": plan,
        "team_results": [{"task_id": "market-task", "attempt": 2, "status": "partial"}],
        "team_task_attempts": {"market-task": 2},
    }

    assert _send_team_tasks(state, ["market-task"]) == "evidence_merger"

    pending = _send_team_tasks(
        {
            **state,
            "team_results": [{"task_id": "market-task", "attempt": 1, "status": "partial"}],
            "team_task_attempts": {"market-task": 1},
        },
        ["market-task"],
    )
    assert isinstance(pending, list)
    assert pending[0].arg["team_current_task_attempt"] == 2


def test_targeted_reexecution_carries_only_its_own_observations_and_repair_context() -> None:
    own = {"task_id": "market-task", "agent_id": "run:market:attempt-1", "success": True}
    other = {"task_id": "fundamental-task", "agent_id": "run:fundamental:attempt-1", "success": True}
    sends = _send_team_tasks({
        "team_id": "repair-context", "team_plan": _team_plan(),
        "run_id": "run", "conversation_id": "conversation", "user_text": "原始问题", "system_prompt": "系统约束",
        "content_access_repair_limit": 2,
        "messages": [{"role": "assistant", "content": "不应复制给专家的主流程完整历史"}],
        "team_results": [{"task_id": "market-task", "attempt": 1, "status": "partial", "unmet_criteria": ["板块资金流"]}],
        "tool_results": [own, other], "evidence": [own, other],
        "team_reexecution": {"repair_instructions": {"market-task": ["补取板块资金流，不重算已核验指标"]}},
    }, ["market-task"])
    assert isinstance(sends, list) and len(sends) == 1
    payload = sends[0].arg
    assert payload["user_text"] == "原始问题"
    assert payload["system_prompt"] == "系统约束"
    assert payload["content_access_repair_limit"] == 2
    assert payload["tool_results"] == [own] and payload["evidence"] == [own]
    assert payload["team_previous_result"]["attempt"] == 1
    assert payload["team_repair_instructions"] == ["板块资金流", "补取板块资金流，不重算已核验指标"]
    assert "messages" not in payload


def test_review_can_reopen_one_completed_expert_without_restarting_its_sibling() -> None:
    async def scenario() -> None:
        registry = ToolRegistry.from_tools([_probe("market_probe", "market"), _probe("fundamental_probe", "financials")])
        graph = build_team_graph(checkpointer=None, registry=registry, response_format=None)
        state = {
            "team_plan": _team_plan(), "team_id": "review-repair",
            "team_results": [{"task_id": name, "status": "completed", "attempt": 1} for name in ("market-task", "fundamental-task")],
            "team_criteria_status": "partial",
            "team_critic_review": {"issues": [{"task_ids": ["market-task"], "repair_instruction": "补取板块资金流；复用行情和均线证据"}]},
        }
        runtime = SimpleNamespace(context=SimpleNamespace(events=SimpleNamespace(stage=lambda *args, **kwargs: None)))
        update = await graph.nodes["reexecution_planner"].bound.afunc(state, runtime)
        assert update["team_reexecution_task_ids"] == ["market-task"]
        assert update["team_reexecution"]["repair_instructions"] == {"market-task": ["补取板块资金流；复用行情和均线证据"]}
        limited = {**state, "team_task_attempts": {"market-task": 2}}
        exhausted = await graph.nodes["reexecution_planner"].bound.afunc(limited, runtime)
        assert exhausted["team_reexecution_task_ids"] == []
        assert exhausted["team_reexecution_status"] == "blocked"

    asyncio.run(scenario())


def test_disclosure_only_review_does_not_reexecute_completed_experts() -> None:
    async def scenario() -> None:
        registry = ToolRegistry.from_tools([_probe("market_probe", "market"), _probe("fundamental_probe", "financials")])
        graph = build_team_graph(checkpointer=None, registry=registry, response_format=None)
        issue = {"resolution": "qualify", "task_ids": ["market-task"], "repair_instruction": "最终回答注明资金流只有单日快照"}
        review = {"verdict": "revise", "issues": [issue]}
        state = {
            "team_plan": _team_plan(), "team_id": "disclosure-only",
            "team_results": [{"task_id": name, "status": "completed", "attempt": 1} for name in ("market-task", "fundamental-task")],
            "team_criteria_status": "passed", "team_critic_review": review,
        }
        runtime = SimpleNamespace(context=SimpleNamespace(events=SimpleNamespace(stage=lambda *args, **kwargs: None)))
        update = await graph.nodes["reexecution_planner"].bound.afunc(state, runtime)
        assert update["team_reexecution_task_ids"] == []
        assert update["team_reexecution_status"] == "not_needed"
        assert team_graph_module._critic_allows_synthesis(review)
        assert not team_graph_module._critic_allows_synthesis({**review, "verdict": "block"})
        assert not team_graph_module._critic_allows_synthesis({**review, "issues": [{**issue, "resolution": "research"}]})
    asyncio.run(scenario())


@pytest.mark.parametrize("verdict,resolution", [("revise", "research"), ("block", "block")])
def test_passed_coverage_cannot_clear_an_exhausted_review_requirement(verdict, resolution) -> None:
    async def scenario():
        graph = build_team_graph(checkpointer=None, registry=ToolRegistry.from_tools([
            _probe("market_probe", "market"), _probe("fundamental_probe", "financials"),
        ]), response_format=None)
        events = []
        runtime = SimpleNamespace(context=SimpleNamespace(events=SimpleNamespace(stage=lambda *a, **kw: events.append((a, kw)))))
        state = {
            "team_plan": _team_plan(), "team_id": "exhausted-review",
            "team_results": [{"task_id": name, "status": "completed", "attempt": 2}
                             for name in ("market-task", "fundamental-task")],
            "team_criteria_status": "passed",
            "team_critic_review": {"verdict": verdict, "issues": [{
                "resolution": resolution, "task_ids": ["market-task"], "repair_instruction": "补齐指数数据",
            }]},
            "team_consensus": {"verdict": "revise", "needs_replan": True, "allow_final_answer": False},
        }
        update = await graph.nodes["reexecution_planner"].bound.afunc(state, runtime)
        assert update["team_reexecution_status"] == "blocked"
        assert update["team_reexecution_task_ids"] == []
        assert events[-1][0][1] == "completed"  # planner executed; its business verdict is blocked
        assert events[-1][1]["details"]["decision_status"] == "blocked"
        assert "复核通过" not in events[-1][0][2]
    asyncio.run(scenario())


def test_zero_reexecution_round_budget_is_honored() -> None:
    async def scenario():
        graph = build_team_graph(checkpointer=None, registry=ToolRegistry.from_tools([
            _probe("market_probe", "market"), _probe("fundamental_probe", "financials"),
        ]), response_format=None)
        runtime = SimpleNamespace(context=SimpleNamespace(events=SimpleNamespace(stage=lambda *a, **kw: None)))
        plan = _team_plan()
        plan["max_reexecution_rounds"] = 0
        state = {"team_plan": plan, "team_criteria_status": "partial", "team_critic_review": {
            "verdict": "revise", "issues": [{"resolution": "research", "task_ids": ["market-task"]}],
        }}
        update = await graph.nodes["reexecution_planner"].bound.afunc(state, runtime)
        assert update["team_reexecution_status"] == "blocked"
        assert update["team_reexecution_task_ids"] == []
    asyncio.run(scenario())


def test_resolved_consensus_does_not_redispatch_original_conflicts() -> None:
    async def scenario() -> None:
        registry = ToolRegistry.from_tools([_probe("market_probe", "market"), _probe("fundamental_probe", "financials")])
        graph = build_team_graph(checkpointer=None, registry=registry, response_format=None)
        state = {
            "team_plan": _team_plan(), "team_id": "resolved-conflict",
            "team_results": [{"task_id": name, "status": "completed", "attempt": 1} for name in ("market-task", "fundamental-task")],
            "team_criteria_status": "passed", "team_critic_review": {"verdict": "pass", "issues": []},
            "team_conflict_assessment": {"issues": [{"task_ids": ["market-task"], "reason": "技术和长期价值方向不同，需多空审查"}]},
            "team_consensus_status": "completed",
            "team_consensus": {"verdict": "pass", "needs_replan": False, "allow_final_answer": True},
        }
        runtime = SimpleNamespace(context=SimpleNamespace(events=SimpleNamespace(stage=lambda *args, **kwargs: None)))
        update = await graph.nodes["reexecution_planner"].bound.afunc(state, runtime)
        assert update["team_reexecution_status"] == "not_needed"
        assert update["team_reexecution_task_ids"] == []
        unresolved = {**state, "team_consensus": {"verdict": "revise", "needs_replan": True}}
        repair = await graph.nodes["reexecution_planner"].bound.afunc(unresolved, runtime)
        assert repair["team_reexecution_task_ids"] == ["market-task"]
    asyncio.run(scenario())


class _CheckpointStateGraph:
    def __init__(self, values: dict) -> None:
        self.values = dict(values)
        self.updates: list[dict] = []

    async def aget_state(self, _config):
        return SimpleNamespace(values=dict(self.values))

    async def aupdate_state(self, _config, update):
        self.updates.append(dict(update))
        self.values.update(update)
        return None


def test_team_terminal_cleanup_closes_checkpoint_lifecycle_and_keeps_cancelled_terminal() -> None:
    async def scenario() -> None:
        values = {
            "status": "running",
            "orchestrator_mode": "multi_agent_team",
            "team_id": "team-cancelled",
            "agent_mode": "team",
            "team_status": "reviewing",
            "team_review_dispatch_status": "completed",
            "team_review_gate_status": "running",
            "team_conflict_status": "running",
            "team_critic_status": "not_started",
            "team_criteria_status": "not_started",
            "team_reexecution_status": "scheduled",
            "collaboration": {
                "schema_version": "team.v1",
                "phase": "reviewing",
                "revision": 4,
                "reports": {"market-task": {"status": "completed"}},
            },
        }
        root = _CheckpointStateGraph(values)
        team = _CheckpointStateGraph(values)
        manager = LangGraphRuntimeManager()
        manager.graph = root
        manager.team_graph = team

        result = await manager.finalize_checkpoint(
            "team-cancelled",
            status="cancelled",
            error_code="cancelled",
            terminal_detail="用户已停止本轮任务",
        )

        assert result["status"] == "cancelled"
        assert result["team_status"] == "cancelled"
        assert result["team_review_gate_status"] == "cancelled"
        assert result["team_conflict_status"] == "cancelled"
        assert result["team_criteria_status"] == "cancelled"
        assert result["team_reexecution_status"] == "cancelled"
        assert result["collaboration"]["phase"] == "cancelled"
        assert result["collaboration"]["failure"]["error_code"] == "cancelled"
        assert len(team.updates) == 1
        assert not root.updates

    asyncio.run(scenario())


def test_retry_defers_ready_sibling_until_failed_worker_recovers() -> None:
    async def scenario() -> None:
        plan = _team_plan()
        plan["tasks"][0].update({"failure_strategy": "retry", "max_attempts": 2})
        plan["tasks"][1]["failure_strategy"] = "partial"
        plan["tasks"].append(
            {
                "task_id": "news-task",
                "agent_id": "news",
                "objective": "在基本面交接完成后核验新闻观察",
                "input_refs": ["用户问题中的标的"],
                "allowed_tools": ["news_probe"],
                "output_format": "新闻观察、时间口径和限制",
                "timeout_seconds": 30,
                "failure_strategy": "partial",
                "required_evidence": ["新闻观察"],
                "success_criteria": ["返回领域观察"],
                "max_tool_calls": 1,
                "parallel_group": "research",
                "depends_on": ["fundamental-task"],
            }
        )
        model = RoleAwareTeamModel(team_plan_override=plan)
        executor = FakeAtomicExecutor(
            outcomes={
                "market_probe": [
                    {"success": False, "error_code": "temporary_source_error"},
                    {"success": True},
                ]
            }
        )
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [
                    _probe("market_probe", "market"),
                    _probe("fundamental_probe", "financials"),
                    _probe("news_probe", "news_source"),
                ]
            )
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请先修复行情方向，再继续核验新闻"}],
                user_text="请先修复行情方向，再继续核验新闻",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-worker-retry-deferred",
                conversation_id="team-worker-retry-deferred",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.state["team_task_attempts"] == {
            "market-task": 2,
            "fundamental-task": 1,
            "news-task": 1,
        }
        assert [item["tool_name"] for item in executor.calls].count("market_probe") == 2
        assert [item["tool_name"] for item in executor.calls].count("fundamental_probe") == 1
        assert [item["tool_name"] for item in executor.calls].count("news_probe") == 1
        retry_events = [
            item
            for item in result.stage_history
            if item.get("details", {}).get("policy") == "WorkerFailurePolicy"
            and item.get("details", {}).get("action") == "retry"
        ]
        assert retry_events
        retry_details = retry_events[0]["details"]
        assert retry_details["retry_task_ids"] == ["market-task"]
        assert retry_details["ready_task_ids"] == ["news-task"]
        assert retry_details["dispatch_task_ids"] == ["market-task"]

    asyncio.run(scenario())


def test_worker_failure_policy_keeps_partial_evidence_and_continues_review() -> None:
    async def scenario() -> None:
        plan = _team_plan()
        plan["tasks"][0]["failure_strategy"] = "partial"
        plan["tasks"][1]["failure_strategy"] = "partial"
        model = RoleAwareTeamModel(team_plan_override=plan)
        executor = FakeAtomicExecutor(
            outcomes={"market_probe": [{"success": False, "error_code": "source_unavailable"}]}
        )
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请保留已有证据并继续综合"}],
                user_text="请保留已有证据并继续综合",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-worker-partial",
                conversation_id="team-worker-partial",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "partial"
        assert result.state["team_failure_policy_action"] == "partial"
        assert result.state["team_evidence_merge_status"] == "partial"
        assert result.state["team_evidence_merge"]["missing_task_ids"] == ["market-task"]
        assert result.state["team_evidence_merge"]["evidence_ids"]
        assert result.state["team_conflict_status"] == "completed"
        assert any(
            item.get("details", {}).get("policy") == "WorkerFailurePolicy"
            and item.get("details", {}).get("action") == "partial"
            for item in result.stage_history
        )

    asyncio.run(scenario())


def test_worker_failure_policy_aborts_without_entering_evidence_merge() -> None:
    async def scenario() -> None:
        plan = _team_plan()
        plan["tasks"][0]["failure_strategy"] = "abort"
        plan["tasks"][1]["failure_strategy"] = "partial"
        model = RoleAwareTeamModel(team_plan_override=plan)
        executor = FakeAtomicExecutor(
            outcomes={"market_probe": [{"success": False, "error_code": "source_unavailable"}]}
        )
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "行情失败时停止本轮协作"}],
                user_text="行情失败时停止本轮协作",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-worker-abort",
                conversation_id="team-worker-abort",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "failed"
        assert result.error_code == "team_worker_abort"
        assert result.state["team_failure_policy_action"] == "abort"
        assert result.state["team_evidence_merge_status"] == "not_started"
        assert any(
            item.get("details", {}).get("policy") == "WorkerFailurePolicy"
            and item.get("details", {}).get("action") == "abort"
            for item in result.stage_history
        )

    asyncio.run(scenario())


def test_abort_policy_precedes_ready_dependent_tasks() -> None:
    async def scenario() -> None:
        plan = _team_plan()
        plan["tasks"] = [
            {
                **plan["tasks"][0],
                "failure_strategy": "abort",
            },
            {
                **plan["tasks"][1],
                "failure_strategy": "partial",
            },
            {
                "task_id": "news-task",
                "agent_id": "news",
                "objective": "核验新闻观察",
                "input_refs": ["用户问题中的标的"],
                "allowed_tools": ["news_probe"],
                "output_format": "新闻观察、时间口径和限制",
                "timeout_seconds": 30,
                "failure_strategy": "partial",
                "required_evidence": ["新闻观察"],
                "success_criteria": ["返回领域观察"],
                "max_tool_calls": 1,
                "parallel_group": "research",
                "depends_on": ["fundamental-task"],
            },
        ]
        model = RoleAwareTeamModel(team_plan_override=plan)
        executor = FakeAtomicExecutor(
            outcomes={"market_probe": [{"success": False, "error_code": "source_unavailable"}]}
        )
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [
                    _probe("market_probe", "market"),
                    _probe("fundamental_probe", "financials"),
                    _probe("news_probe", "news_source"),
                ]
            )
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "行情失败时不要继续调度新闻任务"}],
                user_text="行情失败时不要继续调度新闻任务",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-worker-abort-priority",
                conversation_id="team-worker-abort-priority",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "failed"
        assert result.state["team_failure_policy_action"] == "abort"
        assert result.state["team_failure_policy_task_ids"] == ["market-task"]
        assert result.state["team_evidence_merge_status"] == "not_started"
        assert [item["tool_name"] for item in executor.calls].count("news_probe") == 0

    asyncio.run(scenario())


def test_high_risk_team_path_runs_adversarial_review_and_consensus() -> None:
    async def scenario() -> None:
        model = RoleAwareTeamModel()
        model.force_conflict = True
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请核验行情与基本面并处理冲突"}],
                user_text="请核验行情与基本面并处理冲突",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-conflict",
                conversation_id="team-conflict-regression",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=FakeAtomicExecutor(),
                agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.state["team_conflict_assessment"]["status"] == "high_risk"
        assert result.state["team_bull_case_status"] == "completed"
        assert result.state["team_bear_case_status"] == "completed"
        assert result.state["team_consensus_status"] == "completed"
        assert result.state["team_consensus"]["verdict"] == "pass"
        assert any(
            str(item.get("action_id") or "").endswith(":bull-case-reviewer") for item in result.stage_history or []
        )
        assert any(
            str(item.get("action_id") or "").endswith(":bear-case-reviewer") for item in result.stage_history or []
        )
        assert any(
            str(item.get("action_id") or "").endswith(":consensus-resolver") for item in result.stage_history or []
        )
        assert all(
            "user_message" not in (item.get("details") or {})
            for item in result.stage_history or []
            if item.get("details")
        )
        projected = team_trace(result.state)
        assert projected is not None
        assert projected["conflict"]["status"] == "high_risk"
        assert projected["bull_case"]["stance"] == "bull"
        assert projected["bear_case"]["stance"] == "bear"
        assert projected["bull_case_status"] == "completed"
        assert projected["bear_case_status"] == "completed"
        assert projected["consensus"]["verdict"] == "pass"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("flag", "status_key"),
    [
        ("force_conflict_failure", "team_conflict_status"),
        ("force_critic_failure", "team_critic_status"),
        ("force_case_failure", "team_bull_case_status"),
        ("force_consensus_failure", "team_consensus_status"),
    ],
)
def test_team_reviewer_failures_are_visible_and_fail_closed(flag: str, status_key: str) -> None:
    async def scenario() -> None:
        model = RoleAwareTeamModel()
        if flag in {"force_case_failure", "force_consensus_failure"}:
            model.force_conflict = True
        setattr(model, flag, True)
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请核验行情与基本面并处理冲突"}],
                user_text="请核验行情与基本面并处理冲突",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id=f"reviewer-failure-{flag}",
                conversation_id=f"reviewer-failure-{flag}",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=FakeAtomicExecutor(),
                agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "partial"
        assert result.state[status_key] == "failed"
        assert result.state["planning_status"] == "not_started"
        assert result.state["team_review_gate_status"] == "completed"
        assert result.error_code is not None
        if flag == "force_consensus_failure":
            assert result.error_code == "team_consensus_failed"
        elif flag == "force_case_failure":
            assert result.error_code == "team_adversarial_review_failed"
        assert any(item.get("error_code") for item in result.stage_history if item.get("stage") == "reflection")

    asyncio.run(scenario())


def test_team_repair_stays_inside_team_and_does_not_rejoin_planning() -> None:
    async def scenario() -> None:
        model = RoleAwareTeamModel()
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请综合行情与基本面并补足缺口"}],
                user_text="请综合行情与基本面并补足缺口",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="team-repair-no-rejoin",
                conversation_id="team-repair-no-rejoin",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                agent_mode="team",
            )
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.state["planning_status"] == "not_started"
        assert result.state["planning_replan_count"] == 0
        assert result.state["team_reexecution_status"] == "not_needed"
        assert result.state["team_review_gate_status"] == "completed"
        assert not any(
            "planning-rejoin" in str(item.get("action_id") or "")
            for item in result.stage_history
        )
        assert any(
            ":reexecution-planner:" in str(item.get("action_id") or "")
            and item.get("status") == "completed"
            for item in result.stage_history
        )

    asyncio.run(scenario())


def test_auto_route_can_keep_simple_request_on_direct_agent_path() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _call(
                    "OrchestratorRoute",
                    "route-call",
                    {"mode": "direct", "reason": "不需要多领域外部取证"},
                ),
                AIMessage(content="这是直接 Agent 的回答。"),
            ]
        )
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools([]),
            response_format=None,
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "解释一个概念"}],
                user_text="解释一个概念",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="direct-run",
                conversation_id="team-direct-regression",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
        agent_mode="auto",
            )
            assert await manager._graph_for_checkpoint("team-direct-regression") is manager.graph
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.final_text == "这是直接 Agent 的回答。"
        assert result.state["agent_mode"] == "auto"
        assert result.state["resolved_agent_mode"] == "direct"
        assert result.state["orchestrator_route"] == "direct"
        assert result.state["team_status"] == "not_started"
        assert result.state.get("team_plan") is None

    asyncio.run(scenario())


def test_auto_route_failure_stops_before_any_product_graph_executes() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(responses=[])
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools([]),
            response_format=None,
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "需要自动判断执行模式"}],
                user_text="需要自动判断执行模式",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="auto-route-failure",
                conversation_id="auto-route-failure",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
        agent_mode="auto",
            )
            assert await manager._graph_for_checkpoint("auto-route-failure") is manager.graph
        finally:
            await manager.close()

        assert result.status == "failed"
        assert result.error_code == "orchestrator_route_failed"
        assert result.state["resolved_agent_mode"] == ""
        # The model was only used for the bounded route contract repair; the
        # generic Direct/Plan loop never started after route failure.
        assert len(model.calls) == 2

    asyncio.run(scenario())


def test_auto_route_selects_the_plan_product_path() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _call(
                    "OrchestratorRoute",
                    "team-route-call",
                    {"mode": "plan", "reason": "需要按依赖顺序逐步核验"},
                ),
                _planner_call("planning-plan-call", [_step("first", "核验研究事实")]),
                _named_tool_call(
                    "read",
                    "search_source",
                    {"source_id": "primary", "query": "研究事实"},
                ),
                _report("first", ["read"], last=True),
                _structured_output_call(
                    "final",
                    [{"kind": "fact", "content": "计划路径结论", "source_ids": [1]}],
                    profile="research",
                ),
            ]
        )
        manager = LangGraphRuntimeManager(
            registry=_registry(_search_operation()),
        )
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请按步骤核验研究事实"}],
                user_text="请按步骤核验研究事实",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="planned-team-route",
                conversation_id="planned-team-route",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
        agent_mode="auto",
            )
            assert await manager._graph_for_checkpoint("planned-team-route") is manager.graph
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.state["orchestrator_route"] == "plan"
        assert result.state["agent_mode"] == "auto"
        assert result.state["resolved_agent_mode"] == "plan"
        assert result.state["planning_mode"] == "planned"
        assert result.state["planning_status"] == "completed"
        assert result.state["team_status"] == "not_started"
        assert len(executor.calls) == 1
        assert team_trace(result.state) is None

    asyncio.run(scenario())


def test_auto_route_can_select_team_strategy_under_planned_mode() -> None:
    async def scenario() -> None:
        model = RoleAwareTeamModel(force_team_route=True)
        manager = LangGraphRuntimeManager(
            registry=ToolRegistry.from_tools(
                [_probe("market_probe", "market"), _probe("fundamental_probe", "financials")]
            )
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "请对比行情和基本面并给出风险结论"}],
                user_text="请对比行情和基本面并给出风险结论",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="auto-team-run",
                conversation_id="auto-team-regression",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=FakeAtomicExecutor(),
        agent_mode="auto",
            )
            assert await manager._graph_for_checkpoint("auto-team-regression") is manager.team_graph
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.state["orchestrator_route"] == "team"
        assert result.state["orchestrator_execution_strategy"] == "team"
        assert result.state["agent_mode"] == "auto"
        assert result.state["resolved_agent_mode"] == "team"
        assert result.state["agent_mode"] == "auto"
        assert result.state["team_status"] == "completed"
        assert len(result.state["team_results"]) == 2

    asyncio.run(scenario())
