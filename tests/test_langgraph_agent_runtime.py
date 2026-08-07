from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict
import json
from typing import Any, Mapping

import pytest

from src.agent.langgraph_runtime.catalog import ToolCatalog
from src.agent.langgraph_runtime.graph import (
    _action_plan_validator,
    _deterministic_verification,
)
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.agent.langgraph_runtime.state import ActionPlan, AnswerVerification
from src.tools.base import ToolSpec, object_schema
from src.tools.registry import ToolRegistry


class ScriptedModel:
    def __init__(
        self,
        *,
        structured: Mapping[str, list[dict[str, Any]]],
        texts: list[str],
    ) -> None:
        self.structured_responses = {
            name: [dict(item) for item in values]
            for name, values in structured.items()
        }
        self.texts = list(texts)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.text_calls: list[dict[str, Any]] = []

    async def structured(
        self,
        contract: type,
        *,
        function_name: str,
        payload: Mapping[str, Any],
        validator=None,
        **_kwargs: Any,
    ) -> Any:
        self.calls.append((function_name, dict(payload)))
        queue = self.structured_responses.get(function_name) or []
        if not queue:
            raise AssertionError(f"no scripted response for {function_name}")
        result = contract.model_validate(queue.pop(0))
        if validator is not None:
            validator(result)
        return result

    async def text(self, **kwargs: Any) -> str:
        self.text_calls.append(dict(kwargs))
        if not self.texts:
            raise AssertionError("no scripted text response")
        return self.texts.pop(0)


class FakeAtomicExecutor:
    def __init__(
        self,
        outcomes: Mapping[str, list[dict[str, Any]]] | None = None,
        *,
        delay: float = 0.0,
    ) -> None:
        self.outcomes = defaultdict(list)
        for name, values in (outcomes or {}).items():
            self.outcomes[name].extend(dict(item) for item in values)
        self.delay = delay
        self.calls: list[tuple[str, str, bool]] = []
        self.active = 0
        self.max_active = 0

    async def execute(
        self,
        action: Mapping[str, Any],
        *,
        approved: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        action_id = str(action["action_id"])
        tool_name = str(action["tool_name"])
        self.calls.append((action_id, tool_name, approved))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            outcome = (
                self.outcomes[tool_name].pop(0)
                if self.outcomes[tool_name]
                else {
                    "success": True,
                    "value": tool_name,
                    "source_refs": [f"https://source.test/{tool_name}"],
                    "data_time": "2026-08-06T10:00:00+08:00",
                }
            )
        finally:
            self.active -= 1
        success = outcome.get("success") is not False
        record = {
            "id": action_id,
            "action_id": action_id,
            "tool_name": tool_name,
            "effect": "side_effect" if approved else "read",
            "arguments": dict(action.get("arguments") or {}),
            "success": success,
            "partial": bool(outcome.get("partial")),
            "result": dict(outcome),
            "errors": list(outcome.get("errors") or []),
            "source_refs": list(outcome.get("source_refs") or []),
            "data_time": outcome.get("data_time"),
        }
        if not success:
            record["error_code"] = str(outcome.get("error_code") or "tool_failed")
            return record, None
        evidence_id = f"ev_{action_id}"
        return record, {
            "id": evidence_id,
            "evidence_id": evidence_id,
            "action_id": action_id,
            "tool_name": tool_name,
            "success": True,
            "partial": bool(outcome.get("partial")),
            "entities": dict(action.get("arguments") or {}),
            "data_time": outcome.get("data_time"),
            "source_refs": list(outcome.get("source_refs") or [f"tool:{tool_name}"]),
            "result": dict(outcome),
        }


def _registry(*specs: ToolSpec) -> ToolRegistry:
    registry = object.__new__(ToolRegistry)
    registry._tools = OrderedDict((spec.name, spec) for spec in specs)
    return registry


def _read_spec(
    name: str,
    description: str,
    *,
    properties: dict[str, Any] | None = None,
    required: tuple[str, ...] = (),
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        retrieval_text=description,
        parameters=object_schema(
            properties or {"query": {"type": "string"}},
            required=required,
        ),
        executor=lambda **_kwargs: {"success": True},
        max_attempts=1,
    )


async def _run(
    *,
    registry: ToolRegistry,
    model: ScriptedModel,
    executor: FakeAtomicExecutor,
    conversation_id: str,
) -> Any:
    manager = LangGraphRuntimeManager(registry=registry)
    await manager.start(testing=True)
    try:
        return await manager.run_new(
            messages=[{"role": "user", "content": "测试请求"}],
            user_text="测试请求",
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
            executor=executor,
        )
    finally:
        await manager.close()


def _intent(*, needs_tools: bool) -> dict[str, Any]:
    return {
        "objective": "完成测试目标",
        "constraints": [],
        "deliverable": "直接回答",
        "search_queries": ["目标数据"],
        "needs_tools": needs_tools,
        "needs_clarification": False,
        "clarification_question": None,
    }


def _accepted_verification(*claims: dict[str, Any]) -> dict[str, Any]:
    return {
        "accepted": True,
        "instruction_adherent": True,
        "instruction_issues": [],
        "claims": list(claims),
        "missing_evidence_queries": [],
        "revised_answer": None,
        "summary": "通过",
    }


def test_catalog_bm25_shortlist_and_schema_loading_are_bounded() -> None:
    specs = [
        _read_spec(f"tool_{index}", f"第 {index} 个通用数据查询")
        for index in range(14)
    ]
    specs.append(
        ToolSpec(
            name="notify_user",
            description="发送一次外部通知",
            retrieval_text="通知 消息 send notification",
            parameters=object_schema(
                {
                    "message": {"type": "string"},
                    "confirmed": {"type": "boolean", "default": False},
                },
                required=("message",),
            ),
            executor=lambda **_kwargs: {"success": True},
            effect="side_effect",
        )
    )
    catalog = ToolCatalog(_registry(*specs))

    candidates = catalog.search("发送通知", limit=12)
    assert len(candidates) == 12
    assert candidates[0]["name"] == "notify_user"
    schemas = catalog.load_schemas([item.name for item in specs])
    assert len(schemas) == 8
    notification_schema = catalog.load_schemas(["notify_user"])[0]
    assert "confirmed" not in notification_schema["function"]["parameters"]["properties"]


def test_general_question_without_tools_uses_the_same_control_loop() -> None:
    model = ScriptedModel(
        structured={
            "understand_agent_goal": [_intent(needs_tools=False)],
            "verify_claim_evidence": [_accepted_verification()],
        },
        texts=["这是一个不依赖外部数据的通用回答。"],
    )
    result = asyncio.run(
        _run(
            registry=_registry(_read_spec("read_alpha", "查询 Alpha 数据")),
            model=model,
            executor=FakeAtomicExecutor(),
            conversation_id="general",
        )
    )

    assert result.status == "completed"
    assert result.final_text == "这是一个不依赖外部数据的通用回答。"
    assert result.state["tool_results"] == []
    assert [name for name, _ in model.calls] == [
        "understand_agent_goal",
        "verify_claim_evidence",
    ]


def test_instruction_scope_is_forwarded_and_nonadherent_draft_is_not_published(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_GRAPH_MAX_VERIFICATION_ROUNDS", "0")
    intent = _intent(needs_tools=False) | {
        "constraints": ["只回答是或否", "不要补充解释"],
        "deliverable": "一个词",
    }
    model = ScriptedModel(
        structured={
            "understand_agent_goal": [intent],
            "verify_claim_evidence": [
                {
                    "accepted": False,
                    "instruction_adherent": False,
                    "instruction_issues": ["草稿增加了用户未要求的解释"],
                    "claims": [],
                    "missing_evidence_queries": [],
                    "revised_answer": "是。",
                    "summary": "答案范围超出要求",
                }
            ],
        },
        texts=["是，因为还有很多可以继续展开的理由。"],
    )

    result = asyncio.run(
        _run(
            registry=_registry(_read_spec("read_alpha", "查询 Alpha 数据")),
            model=model,
            executor=FakeAtomicExecutor(),
            conversation_id="instruction-scope",
        )
    )

    draft_payload = json.loads(model.text_calls[0]["messages"][1]["content"])
    verify_payload = next(
        payload for name, payload in model.calls if name == "verify_claim_evidence"
    )
    assert draft_payload["current_user_request"] == "测试请求"
    assert draft_payload["constraints"] == ["只回答是或否", "不要补充解释"]
    assert draft_payload["deliverable"] == "一个词"
    assert verify_payload["current_user_request"] == "测试请求"
    assert verify_payload["constraints"] == draft_payload["constraints"]
    assert result.status == "partial"
    assert result.error_code == "instruction_adherence_gap"
    assert result.final_text == "是。"
    assert result.final_text != result.state["answer_draft"]


def test_provider_budget_exhaustion_returns_partial_without_publishing_unverified_draft() -> None:
    class BudgetModel:
        async def structured(
            self,
            contract: type,
            *,
            function_name: str,
            **_kwargs: Any,
        ) -> Any:
            if function_name == "understand_agent_goal":
                return contract.model_validate(_intent(needs_tools=False))
            raise RuntimeError("Agent provider budget exceeded: estimated_tokens")

        async def text(self, **_kwargs: Any) -> str:
            return "未经校验的外部事实草稿：当前价格为 100 元。"

    result = asyncio.run(
        _run(
            registry=_registry(_read_spec("read_alpha", "查询 Alpha 数据")),
            model=BudgetModel(),
            executor=FakeAtomicExecutor(),
            conversation_id="provider-budget",
        )
    )

    assert result.status == "partial"
    assert result.error_code == "provider_budget_exceeded"
    assert "未验证结论未发布" in result.final_text
    assert result.state["answer_draft"] == "未经校验的外部事实草稿：当前价格为 100 元。"
    assert result.final_text != result.state["answer_draft"]
    assert result.state["budget_limits"]["max_tool_calls"] >= 1
    assert result.state["budget_limits"]["max_estimated_tokens"] >= 1_000


def test_long_tail_request_can_decline_all_similar_tools_and_answer_freely() -> None:
    model = ScriptedModel(
        structured={
            "understand_agent_goal": [_intent(needs_tools=True)],
            "rank_atomic_tools": [
                {
                    "selected_tools": [],
                    "supplemental_queries": [],
                    "rationale": "目录中没有适合这个长尾问题的工具",
                }
            ],
            "create_dynamic_action_plan": [
                {
                    "actions": [],
                    "finalize_without_tools": True,
                    "clarification_question": None,
                    "rationale": "不硬套相近工具，直接解释可确认范围",
                }
            ],
            "verify_claim_evidence": [_accepted_verification()],
        },
        texts=["现有工具不适合这个问题；下面仅基于用户给定信息作答。"],
    )
    executor = FakeAtomicExecutor()
    result = asyncio.run(
        _run(
            registry=_registry(_read_spec("similar_but_wrong", "语义相近但目标不同")),
            model=model,
            executor=executor,
            conversation_id="long-tail",
        )
    )

    assert result.status == "completed"
    assert result.state["selected_tools"] == []
    assert executor.calls == []
    assert not any(name == "reflect_agent_progress" for name, _ in model.calls)


def test_planner_can_request_clarification_after_loading_tool_schema() -> None:
    model = ScriptedModel(
        structured={
            "understand_agent_goal": [_intent(needs_tools=True)],
            "rank_atomic_tools": [
                {
                    "selected_tools": ["read_alpha"],
                    "supplemental_queries": [],
                    "rationale": "工具合适，但缺少必要实体",
                }
            ],
            "create_dynamic_action_plan": [
                {
                    "actions": [],
                    "finalize_without_tools": False,
                    "clarification_question": "请提供要查询的具体实体名称。",
                    "clarification_requirements": [
                        {
                            "tool_name": "read_alpha",
                            "field_names": ["entity"],
                            "reason": "实体是工具必填参数，当前上下文中没有提供",
                        }
                    ],
                    "rationale": "Schema 的必填参数无法从上下文确定",
                }
            ],
        },
        texts=[],
    )
    executor = FakeAtomicExecutor()
    result = asyncio.run(
        _run(
            registry=_registry(
                _read_spec(
                    "read_alpha",
                    "查询指定实体的 Alpha 数据",
                    properties={"entity": {"type": "string"}},
                    required=("entity",),
                )
            ),
            model=model,
            executor=executor,
            conversation_id="schema-clarification",
        )
    )

    assert result.status == "partial"
    assert result.error_code == "clarification_required"
    assert result.final_text == "请提供要查询的具体实体名称。"
    assert executor.calls == []


def test_planner_cannot_turn_server_approval_into_schema_clarification() -> None:
    spec = ToolSpec(
        name="notify_user",
        description="发送一次外部通知",
        parameters=object_schema(
            {
                "message": {"type": "string"},
                "confirmed": {"type": "boolean", "default": False},
            },
            required=("message",),
        ),
        executor=lambda **_kwargs: {"success": True},
        effect="side_effect",
    )
    registry = _registry(spec)
    plan = ActionPlan.model_validate(
        {
            "actions": [],
            "finalize_without_tools": False,
            "clarification_question": "请先确认是否执行。",
            "clarification_requirements": [
                {
                    "tool_name": "notify_user",
                    "field_names": ["confirmed"],
                    "reason": "等待授权",
                }
            ],
            "rationale": "等待用户确认",
        }
    )

    with pytest.raises(ValueError, match="required model-visible fields"):
        _action_plan_validator(
            plan,
            selected_tools={"notify_user"},
            registry=registry,
        )


def test_independent_read_actions_execute_in_parallel_with_send() -> None:
    registry = _registry(
        _read_spec("read_alpha", "查询 Alpha 数据"),
        _read_spec("read_beta", "查询 Beta 数据"),
    )
    model = ScriptedModel(
        structured={
            "understand_agent_goal": [_intent(needs_tools=True)],
            "rank_atomic_tools": [
                {
                    "selected_tools": ["read_alpha", "read_beta"],
                    "supplemental_queries": [],
                    "rationale": "需要两个独立来源",
                }
            ],
            "create_dynamic_action_plan": [
                {
                    "actions": [
                        {
                            "action_id": "alpha",
                            "objective": "读取 Alpha",
                            "tool_name": "read_alpha",
                            "arguments": {"query": "A"},
                            "depends_on": [],
                            "expected_evidence": ["Alpha"],
                        },
                        {
                            "action_id": "beta",
                            "objective": "读取 Beta",
                            "tool_name": "read_beta",
                            "arguments": {"query": "B"},
                            "depends_on": [],
                            "expected_evidence": ["Beta"],
                        },
                    ],
                    "finalize_without_tools": False,
                    "clarification_question": None,
                    "rationale": "并行取证",
                }
            ],
            "reflect_agent_progress": [
                {"decision": "finalize", "reason": "证据已齐", "search_queries": []}
            ],
            "verify_claim_evidence": [
                _accepted_verification(
                    {
                        "claim": "Alpha 和 Beta 来源均已返回",
                        "material": True,
                        "evidence_ids": ["ev_alpha", "ev_beta"],
                        "supported": True,
                        "issue": None,
                    }
                )
            ],
        },
        texts=["两个来源均已返回。[ev_alpha][ev_beta]"],
    )
    executor = FakeAtomicExecutor(delay=0.03)
    result = asyncio.run(
        _run(
            registry=registry,
            model=model,
            executor=executor,
            conversation_id="parallel",
        )
    )

    assert result.status == "completed"
    assert executor.max_active == 2
    assert {item[1] for item in executor.calls} == {"read_alpha", "read_beta"}
    assert {item["evidence_id"] for item in result.state["evidence"]} == {
        "ev_alpha",
        "ev_beta",
    }


def test_failed_source_causes_whole_plan_replacement() -> None:
    registry = _registry(
        _read_spec("primary_source", "主数据源查询"),
        _read_spec("alternate_source", "备用数据源查询"),
    )
    plans = [
        {
            "actions": [
                {
                    "action_id": "primary",
                    "objective": "查询主来源",
                    "tool_name": "primary_source",
                    "arguments": {"query": "目标"},
                    "depends_on": [],
                    "expected_evidence": ["数据"],
                }
            ],
            "finalize_without_tools": False,
            "clarification_question": None,
            "rationale": "先查主来源",
        },
        {
            "actions": [
                {
                    "action_id": "alternate",
                    "objective": "改查备用来源",
                    "tool_name": "alternate_source",
                    "arguments": {"query": "目标"},
                    "depends_on": [],
                    "expected_evidence": ["数据"],
                }
            ],
            "finalize_without_tools": False,
            "clarification_question": None,
            "rationale": "整体替换失败计划",
        },
    ]
    model = ScriptedModel(
        structured={
            "understand_agent_goal": [_intent(needs_tools=True)],
            "rank_atomic_tools": [
                {
                    "selected_tools": ["primary_source", "alternate_source"],
                    "supplemental_queries": [],
                    "rationale": "保留替代来源",
                }
            ],
            "create_dynamic_action_plan": plans,
            "reflect_agent_progress": [
                {"decision": "replan", "reason": "主来源失败", "search_queries": []},
                {"decision": "finalize", "reason": "备用来源成功", "search_queries": []},
            ],
            "verify_claim_evidence": [
                _accepted_verification(
                    {
                        "claim": "备用来源返回目标数据",
                        "material": True,
                        "evidence_ids": ["ev_alternate"],
                        "supported": True,
                        "issue": None,
                    }
                )
            ],
        },
        texts=["备用来源已成功返回。[ev_alternate]"],
    )
    executor = FakeAtomicExecutor(
        {
            "primary_source": [
                {"success": False, "errors": ["upstream unavailable"], "error_code": "provider_unavailable"}
            ]
        }
    )
    result = asyncio.run(
        _run(
            registry=registry,
            model=model,
            executor=executor,
            conversation_id="replan",
        )
    )

    assert result.status == "completed"
    assert [item[1] for item in executor.calls] == ["primary_source", "alternate_source"]
    assert [item["tool_name"] for item in result.state["plan"]["actions"]] == ["alternate_source"]
    second_plan_payload = [payload for name, payload in model.calls if name == "create_dynamic_action_plan"][1]
    assert second_plan_payload["tool_observations"][0]["success"] is False


def test_plan_round_budget_returns_truthful_partial(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_GRAPH_MAX_PLAN_ROUNDS", "1")
    registry = _registry(_read_spec("read_alpha", "查询 Alpha 数据"))
    model = ScriptedModel(
        structured={
            "understand_agent_goal": [_intent(needs_tools=True)],
            "rank_atomic_tools": [
                {"selected_tools": ["read_alpha"], "supplemental_queries": [], "rationale": "取证"}
            ],
            "create_dynamic_action_plan": [
                {
                    "actions": [
                        {
                            "action_id": "alpha",
                            "objective": "读取 Alpha",
                            "tool_name": "read_alpha",
                            "arguments": {"query": "A"},
                            "depends_on": [],
                            "expected_evidence": ["Alpha"],
                        }
                    ],
                    "finalize_without_tools": False,
                    "clarification_question": None,
                    "rationale": "预算内执行一次",
                }
            ],
            "verify_claim_evidence": [
                _accepted_verification(
                    {
                        "claim": "Alpha 来源已返回",
                        "material": True,
                        "evidence_ids": ["ev_alpha"],
                        "supported": True,
                        "issue": None,
                    }
                )
            ],
        },
        texts=["已取得部分证据，但规划预算已耗尽。[ev_alpha]"],
    )
    result = asyncio.run(
        _run(
            registry=registry,
            model=model,
            executor=FakeAtomicExecutor(),
            conversation_id="budget",
        )
    )

    assert result.status == "partial"
    assert result.error_code == "budget_or_evidence_gap"
    assert result.state["plan_round"] == result.state["max_plan_rounds"] == 1
    assert not any(name == "reflect_agent_progress" for name, _ in model.calls)


def test_claim_evidence_check_requires_real_source_time_and_entity() -> None:
    verification = AnswerVerification.model_validate(
        _accepted_verification(
            {
                "claim": "当前 600519 股价为 100 元",
                "material": True,
                "evidence_ids": ["ev_quote"],
                "supported": True,
                "issue": None,
            }
        )
    )
    accepted, issues = _deterministic_verification(
        verification,
        answer="当前 600519 股价为 100 元。[ev_quote]",
        evidence=[
            {
                "evidence_id": "ev_quote",
                "success": True,
                "entities": {"symbol": "000001"},
                "data_time": None,
                "source_refs": ["tool:get_realtime_quotes"],
            }
        ],
    )

    assert accepted is False
    assert any("no source" in issue for issue in issues)
    assert any("no time basis" in issue for issue in issues)
    assert any("entity does not match" in issue for issue in issues)


def test_external_fact_cannot_pass_with_no_claim_map_or_evidence() -> None:
    verification = AnswerVerification.model_validate(_accepted_verification())

    accepted, issues = _deterministic_verification(
        verification,
        answer="当前 600519 股价为 100 元。",
        evidence=[],
    )

    assert accepted is False
    assert any("no material claims" in issue for issue in issues)


def test_structured_claim_mapping_does_not_require_visible_internal_id() -> None:
    verification = AnswerVerification.model_validate(
        _accepted_verification(
            {
                "claim": "当前 600519 股价为 100 元",
                "material": True,
                "evidence_ids": ["ev_quote"],
                "supported": True,
                "issue": None,
            }
        )
    )

    accepted, issues = _deterministic_verification(
        verification,
        answer="最新价：100 元",
        evidence=[
            {
                "evidence_id": "ev_quote",
                "success": True,
                "entities": {"symbol": "600519"},
                "data_time": "2026-08-07T09:29:15+08:00",
                "source_refs": ["eastmoney_push"],
            }
        ],
    )

    assert accepted is True
    assert issues == []


def test_rejected_verification_never_falls_back_to_unverified_draft(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_GRAPH_MAX_VERIFICATION_ROUNDS", "0")
    draft = "当前 600519 股价为 100 元。"
    model = ScriptedModel(
        structured={
            "understand_agent_goal": [_intent(needs_tools=False)],
            "verify_claim_evidence": [_accepted_verification()],
        },
        texts=[draft],
    )
    result = asyncio.run(
        _run(
            registry=_registry(_read_spec("read_quote", "查询实时价格")),
            model=model,
            executor=FakeAtomicExecutor(),
            conversation_id="no-unverified-fallback",
        )
    )

    assert result.status == "partial"
    assert result.error_code == "claim_evidence_gap"
    assert result.state["answer_draft"] == draft
    assert result.final_text != draft
    assert "不会作为答案发布" in result.final_text


def test_verification_budget_uses_revised_answer_instead_of_unsupported_claim(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_GRAPH_MAX_VERIFICATION_ROUNDS", "0")
    registry = _registry(
        _read_spec(
            "read_quote",
            "查询证券价格",
            properties={"symbol": {"type": "string"}},
        )
    )
    model = ScriptedModel(
        structured={
            "understand_agent_goal": [_intent(needs_tools=True)],
            "rank_atomic_tools": [
                {"selected_tools": ["read_quote"], "supplemental_queries": [], "rationale": "查价"}
            ],
            "create_dynamic_action_plan": [
                {
                    "actions": [
                        {
                            "action_id": "quote",
                            "objective": "查询价格",
                            "tool_name": "read_quote",
                            "arguments": {"symbol": "600519"},
                            "depends_on": [],
                            "expected_evidence": ["价格和时间"],
                        }
                    ],
                    "finalize_without_tools": False,
                    "clarification_question": None,
                    "rationale": "实时取证",
                }
            ],
            "reflect_agent_progress": [
                {"decision": "finalize", "reason": "已取到返回值", "search_queries": []}
            ],
            "verify_claim_evidence": [
                {
                    "accepted": True,
                    "instruction_adherent": True,
                    "instruction_issues": [],
                    "claims": [
                        {
                            "claim": "当前 600519 股价为 100 元",
                            "material": True,
                            "evidence_ids": ["ev_quote"],
                            "supported": True,
                            "issue": None,
                        }
                    ],
                    "missing_evidence_queries": ["600519 实时价格权威来源"],
                    "revised_answer": "工具返回缺少可追溯来源和时间口径，无法可靠报告该价格。",
                    "summary": "来源与时间缺失",
                }
            ],
        },
        texts=["当前 600519 股价为 100 元。[ev_quote]"],
    )
    executor = FakeAtomicExecutor(
        {
            "read_quote": [
                {
                    "success": True,
                    "price": 100,
                    "source_refs": ["tool:read_quote"],
                    "data_time": None,
                }
            ]
        }
    )
    result = asyncio.run(
        _run(
            registry=registry,
            model=model,
            executor=executor,
            conversation_id="verification-gap",
        )
    )

    assert result.status == "partial"
    assert result.error_code == "claim_evidence_gap"
    assert result.final_text == "工具返回缺少可追溯来源和时间口径，无法可靠报告该价格。"
    assert result.state["verification"]["accepted"] is False
    assert result.state["verification"]["deterministic_issues"]
