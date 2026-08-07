"""The sole generic control loop for interactive Agent turns."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal, Mapping, Sequence

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, Overwrite, Send, interrupt

from src.agent.message_normalization import latest_user_text
from src.agent.runtime_safety import get_agent_runtime_limits

from .events import redact_arguments
from .executor import action_fingerprint
from .prompts import ANSWER_SYSTEM_PROMPT, CONTROL_SYSTEM_PROMPT, VERIFY_SYSTEM_PROMPT
from .state import (
    ActionPlan,
    AgentGraphInput,
    AgentState,
    AnswerVerification,
    GraphContext,
    IntentUnderstanding,
    ReflectionDecision,
    ToolRanking,
)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name) or default).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bounded(value: Any, *, depth: int = 0) -> Any:
    """Keep model context useful without checkpoint-dependent hidden objects."""
    if depth > 10:
        return "[depth omitted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 12_000 else value[:12_000] + "…[truncated]"
    if isinstance(value, Mapping):
        items = list(value.items())
        return {
            str(key): _bounded(item, depth=depth + 1)
            for key, item in items[:100]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth=depth + 1) for item in value[:80]]
    return str(value)[:2_000]


def _action_plan_validator(
    plan: ActionPlan,
    *,
    selected_tools: set[str],
    registry: Any,
) -> None:
    if not plan.actions and not plan.finalize_without_tools and not plan.clarification_question:
        raise ValueError("plan must contain actions or explicitly finalize")
    if plan.finalize_without_tools and plan.actions:
        raise ValueError("finalize_without_tools cannot be combined with actions")
    if plan.clarification_question and (plan.actions or plan.finalize_without_tools):
        raise ValueError("clarification_question must be the only plan outcome")
    for requirement in plan.clarification_requirements:
        if requirement.tool_name not in selected_tools:
            raise ValueError(
                f"clarification references unselected tool: {requirement.tool_name}"
            )
        spec = registry.get_tool(requirement.tool_name)
        if spec is None:
            raise ValueError(f"unknown clarification tool: {requirement.tool_name}")
        required_fields = set(spec.model_parameters().get("required") or [])
        invalid_fields = sorted(set(requirement.field_names) - required_fields)
        if invalid_fields:
            raise ValueError(
                "clarification may only reference required model-visible fields for "
                f"{requirement.tool_name}: {invalid_fields}"
            )
    action_ids = [action.action_id for action in plan.actions]
    if len(action_ids) != len(set(action_ids)):
        raise ValueError("action_id values must be unique")
    all_ids = set(action_ids)
    position = {action_id: index for index, action_id in enumerate(action_ids)}
    for action in plan.actions:
        if action.tool_name not in selected_tools:
            raise ValueError(f"unselected tool in plan: {action.tool_name}")
        registry.validate_model_arguments(action.tool_name, action.arguments, approved=False)
        for dependency in action.depends_on:
            if dependency not in all_ids:
                raise ValueError(f"unknown dependency {dependency}")
            if position[dependency] >= position[action.action_id]:
                raise ValueError("dependencies must refer to an earlier action")


def _result_for_rejection(action: Mapping[str, Any], fingerprint: str) -> dict[str, Any]:
    action_id = str(action.get("action_id") or "")
    return {
        "id": action_id,
        "action_id": action_id,
        "tool_name": str(action.get("tool_name") or ""),
        "effect": "side_effect",
        "fingerprint": fingerprint,
        "arguments": dict(action.get("arguments") or {}),
        "objective": str(action.get("objective") or "该操作"),
        "success": False,
        "partial": False,
        "result": {
            "success": False,
            "partial": False,
            "errors": ["用户拒绝了该外部操作"],
            "error_code": "approval_rejected",
        },
        "errors": ["用户拒绝了该外部操作"],
        "error_code": "approval_rejected",
        "source_refs": [],
    }


def _result_error_code(result: Mapping[str, Any]) -> str:
    nested = result.get("result")
    nested_code = nested.get("error_code") if isinstance(nested, Mapping) else None
    return str(result.get("error_code") or nested_code or "")


def _is_rejection_only_outcome(state: Mapping[str, Any]) -> bool:
    """Identify a pure server-owned rejection outcome that needs no model call."""
    results = [item for item in state.get("tool_results") or [] if isinstance(item, Mapping)]
    if not results or state.get("evidence"):
        return False
    if any(_result_error_code(item) != "approval_rejected" for item in results):
        return False
    try:
        plan = ActionPlan.model_validate(state.get("plan") or {})
    except Exception:
        return False
    action_ids = {action.action_id for action in plan.actions}
    rejected_ids = {str(item.get("action_id") or "") for item in results}
    return bool(action_ids) and action_ids == rejected_ids and not state.get("deferred_actions")


def _is_current_plan_fully_satisfied(state: Mapping[str, Any]) -> bool:
    """Return whether every current action completed successfully without gaps."""
    try:
        plan = ActionPlan.model_validate(state.get("plan") or {})
    except Exception:
        return False
    action_ids = {action.action_id for action in plan.actions}
    if not action_ids or state.get("deferred_actions"):
        return False
    if not action_ids.issubset(set(state.get("completed_action_ids") or [])):
        return False
    by_action = {
        str(item.get("action_id") or ""): item
        for item in state.get("tool_results") or []
        if isinstance(item, Mapping)
    }
    return all(
        action_id in by_action
        and by_action[action_id].get("success") is True
        and not by_action[action_id].get("partial")
        for action_id in action_ids
    )


def _rejection_answer(state: Mapping[str, Any]) -> str:
    results = [item for item in state.get("tool_results") or [] if isinstance(item, Mapping)]
    objectives = list(
        dict.fromkeys(
            str(item.get("objective") or "").strip()
            for item in results
            if str(item.get("objective") or "").strip()
        )
    )
    target = "；".join(objectives) or "该操作"
    return f"你已拒绝审批，操作未执行：{target}。"


async def begin_turn(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    messages = [dict(item) for item in state.get("input_messages") or []]
    user_text = str(state.get("input_user_text") or "").strip() or latest_user_text(messages)
    run_id = str(state.get("input_run_id") or runtime.context.run_id)
    conversation_id = str(state.get("input_conversation_id") or runtime.context.conversation_id)
    limits = get_agent_runtime_limits()
    runtime.context.events.stage("understand", "started", "读取本轮目标与约束")
    return {
        "engine": "langgraph",
        "run_id": run_id,
        "conversation_id": conversation_id,
        "messages": messages,
        "user_text": user_text,
        "system_prompt": str(state.get("input_system_prompt") or ""),
        "intent": {},
        "search_queries": [],
        "tool_candidates": [],
        "selected_tools": [],
        "selected_tool_schemas": [],
        "plan": {},
        "ready_read_actions": [],
        "pending_action": None,
        "deferred_actions": [],
        "dispatch_action": {},
        "tool_results": Overwrite([]),
        "evidence": Overwrite([]),
        "completed_action_ids": Overwrite([]),
        "reflection": {},
        "verification": {},
        "answer_draft": "",
        "answer_final": "",
        "status": "running",
        "error_code": None,
        "plan_round": 0,
        "search_expansions": 0,
        "verification_round": 0,
        "max_plan_rounds": _env_int("AGENT_GRAPH_MAX_PLAN_ROUNDS", 6, minimum=1, maximum=20),
        "max_search_expansions": _env_int("AGENT_GRAPH_MAX_SEARCH_EXPANSIONS", 2, minimum=0, maximum=5),
        "max_verification_rounds": _env_int("AGENT_GRAPH_MAX_VERIFICATION_ROUNDS", 2, minimum=0, maximum=5),
        "max_elapsed_seconds": _env_int("AGENT_GRAPH_MAX_ELAPSED_SECONDS", 900, minimum=5, maximum=7_200),
        "budget_limits": {
            "max_tool_calls": limits.max_plan_tool_calls,
            "max_provider_calls": limits.max_provider_calls,
            "max_estimated_tokens": limits.max_estimated_tokens,
            "max_estimated_cost_micros": limits.max_estimated_cost_micros,
        },
    }


async def understand_goal(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    intent = await runtime.context.model.structured(
        IntentUnderstanding,
        function_name="understand_agent_goal",
        description="理解用户本轮目标、约束、交付物以及是否需要外部工具取证",
        system_prompt=CONTROL_SYSTEM_PROMPT,
        payload={
            "conversation": _bounded(state.get("messages") or []),
            "current_user_request": state.get("user_text"),
            "rules": [
                "只有信息确实缺失且不同答案会实质改变结果时才要求澄清。",
                "用户要求执行前审批不是信息缺失；副作用审批由后续服务端 interrupt 负责。",
            ],
        },
        max_tokens=2_500,
    )
    runtime.context.events.stage(
        "understand",
        "completed",
        "已理解目标，准备动态决定取证方式" if intent.needs_tools else "该问题可以直接作答",
    )
    return {
        "intent": intent.model_dump(mode="json"),
        "search_queries": intent.search_queries or [intent.objective],
    }


def route_after_understanding(state: AgentState) -> Literal["clarify", "discover", "draft"]:
    intent = IntentUnderstanding.model_validate(state.get("intent") or {})
    if intent.needs_clarification:
        return "clarify"
    return "discover" if intent.needs_tools else "draft"


async def clarification_answer(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    intent = IntentUnderstanding.model_validate(state.get("intent") or {})
    plan_question = str((state.get("plan") or {}).get("clarification_question") or "").strip()
    answer = plan_question or str(
        intent.clarification_question or "请补充完成这个任务所需的关键信息。"
    )
    runtime.context.events.stage("publish", "completed", "需要用户补充关键信息")
    return {
        "answer_draft": answer,
        "answer_final": answer,
        "status": "partial",
        "error_code": "clarification_required",
    }


async def discover_tools(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    events = runtime.context.events
    events.stage("discover", "started", "按当前目标检索原子工具")
    queries = [str(item) for item in state.get("search_queries") or [] if str(item).strip()]
    if not queries:
        queries = [str((state.get("intent") or {}).get("objective") or state.get("user_text") or "")]
    expansions = int(state.get("search_expansions") or 0)
    if expansions >= int(state.get("max_search_expansions") or 2):
        candidates = runtime.context.catalog.compact_catalog()
    else:
        candidates = runtime.context.catalog.search(queries, limit=12)
    candidate_names = {str(item.get("name") or "") for item in candidates}

    def validate_ranking(ranking: ToolRanking) -> None:
        unknown = [name for name in ranking.selected_tools if name not in candidate_names]
        if unknown:
            raise ValueError(f"ranking selected tools outside candidates: {unknown}")

    ranking = await runtime.context.model.structured(
        ToolRanking,
        function_name="rank_atomic_tools",
        description="从候选中选择最多 8 个真正适合当前目标的原子工具",
        system_prompt=CONTROL_SYSTEM_PROMPT,
        payload={
            "objective": state.get("intent"),
            "queries": queries,
            "candidate_tools": _bounded(candidates),
            "observations": _bounded(state.get("tool_results") or []),
            "instruction": (
                "只从 candidate_tools 选名称；允许一个都不选。不要按相近名称硬套，"
                "若描述不足可给 supplemental_queries。"
            ),
        },
        max_tokens=2_500,
        validator=validate_ranking,
    )
    selected = list(dict.fromkeys(ranking.selected_tools))[:8]
    schemas = runtime.context.catalog.load_schemas(selected)
    events.stage(
        "discover",
        "completed",
        f"已按需加载 {len(schemas)} 个原子工具 Schema",
    )
    return {
        "tool_candidates": candidates,
        "selected_tools": selected,
        "selected_tool_schemas": schemas,
        "search_queries": ranking.supplemental_queries or queries,
    }


async def create_plan(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    events = runtime.context.events
    next_round = int(state.get("plan_round") or 0) + 1
    max_rounds = int(state.get("max_plan_rounds") or 1)
    if next_round > max_rounds:
        exhausted = ActionPlan(
            actions=[],
            finalize_without_tools=True,
            clarification_question=None,
            rationale="规划轮次预算已耗尽，基于现有证据部分收束",
        )
        events.stage(
            "plan",
            "completed",
            "规划轮次预算已耗尽，不再调用模型生成新计划",
            error_code="budget_or_evidence_gap",
        )
        return {
            "plan": exhausted.model_dump(mode="json"),
            "ready_read_actions": [],
            "pending_action": None,
            "deferred_actions": [],
            "status": "partial",
            "error_code": "budget_or_evidence_gap",
        }
    events.stage("plan", "started", f"动态生成第 {next_round} 轮行动计划")
    selected = set(str(item) for item in state.get("selected_tools") or [])

    def validate(plan: ActionPlan) -> None:
        _action_plan_validator(plan, selected_tools=selected, registry=runtime.context.registry)

    plan = await runtime.context.model.structured(
        ActionPlan,
        function_name="create_dynamic_action_plan",
        description="基于当前观察生成可整体替换的行动计划",
        system_prompt=CONTROL_SYSTEM_PROMPT,
        payload={
            "intent": state.get("intent"),
            "selected_tool_schemas": _bounded(state.get("selected_tool_schemas") or []),
            "successful_evidence": _bounded(state.get("evidence") or []),
            "tool_observations": _bounded(state.get("tool_results") or []),
            "previous_plan": _bounded(state.get("plan") or {}),
            "verification_feedback": _bounded(state.get("verification") or {}),
            "budgets": {
                "plan_round": next_round,
                "max_plan_rounds": state.get("max_plan_rounds"),
                **dict(state.get("budget_limits") or {}),
            },
            "approval_contract": {
                "owner": "graph_execution_policy",
                "instruction": (
                    "副作用 Action 必须照常生成；模型不得向用户索要确认、批准或 confirmed 字段，"
                    "图会在执行前统一 interrupt。"
                ),
            },
            "rules": [
                "每个 Action 只能调用一个原子工具，参数必须现在就满足 Schema。",
                "不要生成排名打分、固定八门判断或隐藏多步 SOP。",
                "depends_on 只表达真实数据依赖，并只能指向本计划中更早 Action。",
                "本轮观察不足以填写下游参数时，只计划当前可执行动作，观察后再整体重规划。",
                "不要传 confirmed 或任何服务端控制字段。",
                (
                    "clarification 仅用于缺失 selected_tool_schemas 中模型可见的 required 字段；"
                    "必须在 clarification_requirements 中列出工具名和字段名。"
                ),
            ],
        },
        max_tokens=5_000,
        validator=validate,
    )
    events.stage("plan", "completed", f"本轮计划包含 {len(plan.actions)} 个可变动作")
    return {
        "plan": plan.model_dump(mode="json"),
        "plan_round": next_round,
        "ready_read_actions": [],
        "pending_action": None,
        "deferred_actions": [],
    }


def route_after_plan(state: AgentState) -> Literal["clarify", "policy", "draft"]:
    plan = ActionPlan.model_validate(state.get("plan") or {})
    if plan.clarification_question:
        return "clarify"
    if plan.finalize_without_tools:
        return "draft"
    return "policy"


async def apply_execution_policy(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    events = runtime.context.events
    events.stage("policy", "started", "检查依赖、参数与副作用边界")
    plan = ActionPlan.model_validate(state.get("plan") or {})
    completed = set(state.get("completed_action_ids") or [])
    remaining = [action for action in plan.actions if action.action_id not in completed]
    ready = [action for action in remaining if set(action.depends_on).issubset(completed)]
    read_actions: list[dict[str, Any]] = []
    effect_actions: list[dict[str, Any]] = []
    for action in ready:
        runtime.context.registry.validate_model_arguments(
            action.tool_name,
            action.arguments,
            approved=False,
        )
        serialized = action.model_dump(mode="json")
        if runtime.context.registry.effect_for(action.tool_name, action.arguments) == "side_effect":
            effect_actions.append(serialized)
        else:
            read_actions.append(serialized)

    # Read-only work may fan out.  Effects never share a superstep with any
    # other action and only the first ready effect can enter approval.
    pending = None if read_actions else (effect_actions[0] if effect_actions else None)
    deferred = [
        action.model_dump(mode="json")
        for action in remaining
        if action.action_id not in {item["action_id"] for item in read_actions}
        and (pending is None or action.action_id != pending["action_id"])
    ]
    if read_actions:
        summary = f"{len(read_actions)} 个无依赖只读动作可并行执行"
    elif pending is not None:
        summary = "检测到副作用动作，必须等待用户审批"
    elif remaining:
        summary = "当前计划依赖无法满足，将返回反思节点"
    else:
        summary = "当前计划已无待执行动作"
    events.stage("policy", "completed", summary)
    return {
        "ready_read_actions": read_actions,
        "pending_action": pending,
        "deferred_actions": deferred,
    }


def route_policy(state: AgentState) -> list[Send] | Literal["approval", "reflect"]:
    reads = state.get("ready_read_actions") or []
    if reads:
        return [Send("execute_read_action", {"dispatch_action": dict(action)}) for action in reads]
    if state.get("pending_action"):
        return "approval"
    return "reflect"


async def execute_read_action(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    action = dict(state.get("dispatch_action") or {})
    record, evidence = await runtime.context.executor.execute(action, approved=False)
    return {
        "tool_results": [record],
        "evidence": [evidence] if evidence is not None else [],
        "completed_action_ids": [str(action.get("action_id") or "")],
    }


async def await_approval(
    state: AgentState,
    runtime: Runtime[GraphContext],
) -> Command[Literal["execute_approved_action", "reflect"]]:
    action = dict(state.get("pending_action") or {})
    tool_name = str(action.get("tool_name") or "")
    action_id = str(action.get("action_id") or "")
    arguments = dict(action.get("arguments") or {})
    fingerprint = action_fingerprint(
        run_id=runtime.context.run_id,
        action_id=action_id,
        tool_name=tool_name,
        arguments=arguments,
    )
    spec = runtime.context.registry.get_tool(tool_name)
    if spec is None:
        raise KeyError(f"Tool not found: {tool_name}")
    decision = interrupt(
        {
            "run_id": runtime.context.run_id,
            "conversation_id": runtime.context.conversation_id,
            "fingerprint": fingerprint,
            "action_id": action_id,
            "tool_name": tool_name,
            "summary": str(action.get("objective") or f"执行 {tool_name}"),
            "arguments": redact_arguments(arguments, sensitive_fields=spec.sensitive_fields),
        }
    )
    if not isinstance(decision, Mapping):
        raise ValueError("approval resume payload must be an object")
    if str(decision.get("fingerprint") or "") != fingerprint:
        raise ValueError("approval fingerprint mismatch")
    normalized = str(decision.get("decision") or "").strip().lower()
    if normalized == "approve":
        return Command(
            update={"dispatch_action": action, "pending_action": None},
            goto="execute_approved_action",
        )
    if normalized == "reject":
        runtime.context.events.stage(
            "approval",
            "completed",
            f"用户拒绝执行 {tool_name}",
            action_id=action_id,
        )
        return Command(
            update={
                "pending_action": None,
                "tool_results": [_result_for_rejection(action, fingerprint)],
                "completed_action_ids": [action_id],
            },
            goto="reflect",
        )
    raise ValueError("approval decision must be approve or reject")


async def execute_approved_action(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    action = dict(state.get("dispatch_action") or {})
    runtime.context.events.stage(
        "approval",
        "completed",
        f"审批通过，串行执行 {action.get('tool_name')}",
        action_id=str(action.get("action_id") or ""),
    )
    record, evidence = await runtime.context.executor.execute(action, approved=True)
    return {
        "tool_results": [record],
        "evidence": [evidence] if evidence is not None else [],
        "completed_action_ids": [str(action.get("action_id") or "")],
    }


async def reflect_on_progress(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    events = runtime.context.events
    events.stage("reflect", "started", "根据实际结果判断完成度并决定下一步")
    plan_round = int(state.get("plan_round") or 0)
    max_rounds = int(state.get("max_plan_rounds") or 1)
    if _is_rejection_only_outcome(state):
        reflection = ReflectionDecision(
            decision="finalize",
            reason="用户拒绝了全部待审批操作，服务端已确认零执行",
            search_queries=[],
        )
    elif plan_round >= max_rounds:
        reflection = ReflectionDecision(
            decision="partial",
            reason="规划轮次预算已耗尽，必须基于现有证据收束",
            search_queries=[],
        )
    elif _is_current_plan_fully_satisfied(state):
        reflection = ReflectionDecision(
            decision="finalize",
            reason="当前计划全部动作已成功完成且无剩余依赖",
            search_queries=[],
        )
    else:
        reflection = await runtime.context.model.structured(
            ReflectionDecision,
            function_name="reflect_agent_progress",
            description="判断目标是否完成，或选择扩展检索、整体重规划、完成或部分收束",
            system_prompt=CONTROL_SYSTEM_PROMPT,
            payload={
                "intent": state.get("intent"),
                "current_plan": _bounded(state.get("plan") or {}),
                "completed_action_ids": state.get("completed_action_ids") or [],
                "tool_results": _bounded(state.get("tool_results") or []),
                "evidence": _bounded(state.get("evidence") or []),
                "deferred_actions": _bounded(state.get("deferred_actions") or []),
                "budgets": {
                    "plan_round": plan_round,
                    "max_plan_rounds": max_rounds,
                    "search_expansions": state.get("search_expansions"),
                    "max_search_expansions": state.get("max_search_expansions"),
                    **dict(state.get("budget_limits") or {}),
                },
                "rules": [
                    "工具失败时可换来源或换检索词，不能把失败包装成成功。",
                    "用户拒绝副作用后可规划无副作用替代方案，不得再次偷偷执行。",
                    "目标已满足才 finalize；预算将耗尽或无法补齐时 partial。",
                ],
            },
            max_tokens=2_500,
        )
    decision = reflection.decision
    expansions = int(state.get("search_expansions") or 0)
    max_expansions = int(state.get("max_search_expansions") or 0)
    if decision == "discover" and expansions >= max_expansions:
        decision = "replan"
        reflection = reflection.model_copy(
            update={"decision": "replan", "reason": reflection.reason + "；工具扩展检索预算已耗尽"}
        )
    events.stage("reflect", "completed", reflection.reason or f"反思结论：{decision}")
    updates: dict[str, Any] = {"reflection": reflection.model_dump(mode="json")}
    if reflection.search_queries:
        updates["search_queries"] = reflection.search_queries
    if decision == "discover":
        updates["search_expansions"] = expansions + 1
    if decision == "partial":
        updates["status"] = "partial"
        updates["error_code"] = "budget_or_evidence_gap"
    return updates


def route_reflection(state: AgentState) -> Literal["discover", "plan", "draft"]:
    decision = str((state.get("reflection") or {}).get("decision") or "partial")
    if decision == "discover":
        return "discover"
    if decision == "replan":
        return "plan"
    return "draft"


async def draft_answer(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    runtime.context.events.stage("answer", "started", "仅基于当前上下文和证据生成答案草稿")
    if _is_rejection_only_outcome(state):
        answer = _rejection_answer(state)
        runtime.context.events.stage("answer", "completed", "已生成服务端审批拒绝结果")
        return {"answer_draft": answer}
    evidence = _bounded(state.get("evidence") or [])
    gaps = [
        {
            "tool": item.get("tool_name"),
            "errors": item.get("errors"),
            "error_code": item.get("error_code") or (item.get("result") or {}).get("error_code"),
        }
        for item in state.get("tool_results") or []
        if item.get("success") is False
    ]
    messages = [
        {
            "role": "system",
            "content": "\n\n".join(
                item
                for item in (ANSWER_SYSTEM_PROMPT, str(state.get("system_prompt") or "").strip())
                if item
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "conversation": _bounded(state.get("messages") or []),
                    "current_user_request": state.get("user_text"),
                    "current_goal": state.get("intent"),
                    "constraints": list((state.get("intent") or {}).get("constraints") or []),
                    "deliverable": str((state.get("intent") or {}).get("deliverable") or ""),
                    "successful_evidence": evidence,
                    "known_gaps": _bounded(gaps),
                    "reflection": state.get("reflection"),
                    "instruction": (
                        "生成最终可见答案草稿。严格按当前请求的范围、格式和长度作答；"
                        "不要增加未要求字段，也不要声称执行未成功的动作。"
                    ),
                },
                ensure_ascii=False,
                default=str,
            ),
        },
    ]
    answer = await runtime.context.model.text(
        messages=messages,
        max_tokens=10_000,
    )
    runtime.context.events.stage("answer", "completed", "答案草稿已生成，进入逐条证据校验")
    return {"answer_draft": answer}


_EXTERNAL_FACT_MARKERS = (
    "价格",
    "股价",
    "市值",
    "同比",
    "环比",
    "营收",
    "利润",
    "估值",
    "公告",
    "新闻",
    "截至",
    "今日",
    "当前",
    "最新",
    "%",
)


def _deterministic_verification(
    verification: AnswerVerification,
    *,
    answer: str,
    evidence: Sequence[Mapping[str, Any]],
) -> tuple[bool, list[str]]:
    by_id = {
        str(item.get("evidence_id") or item.get("id") or ""): item
        for item in evidence
        if item.get("success") is True
    }
    issues: list[str] = []
    if not verification.instruction_adherent:
        issues.extend(
            f"instruction violation: {issue}"
            for issue in verification.instruction_issues
        )
    cited_ids = set(re.findall(r"\[(ev_[A-Za-z0-9_-]+)\]", answer))
    for evidence_id in sorted(cited_ids - set(by_id)):
        issues.append(f"answer cites unknown evidence: {evidence_id}")
    for assessment in verification.claims:
        if not assessment.material:
            continue
        mapped = [by_id.get(evidence_id) for evidence_id in assessment.evidence_ids]
        if not assessment.supported or not mapped or any(item is None for item in mapped):
            issues.append(f"unsupported claim: {assessment.claim[:160]}")
            continue
        valid = [item for item in mapped if item is not None]
        # Claim-Evidence traceability is persisted in the structured mapping.
        # Visible IDs are optional because an explicit user format can forbid
        # internal tokens without weakening the underlying evidence binding.
        if any(
            not [
                ref
                for ref in (item.get("source_refs") or [])
                if not str(ref).startswith("tool:")
            ]
            for item in valid
        ):
            issues.append(f"claim has no source: {assessment.claim[:160]}")
        external_time_claim = any(marker in assessment.claim for marker in _EXTERNAL_FACT_MARKERS)
        if external_time_claim and all(item.get("data_time") in (None, "") for item in valid):
            issues.append(f"claim has no time basis: {assessment.claim[:160]}")
        symbols = set(re.findall(r"(?<!\d)\d{6}(?!\d)", assessment.claim))
        if symbols:
            mapped_entities = json.dumps(
                [item.get("entities") or {} for item in valid],
                ensure_ascii=False,
                default=str,
            )
            if any(symbol not in mapped_entities for symbol in symbols):
                issues.append(f"claim entity does not match evidence: {assessment.claim[:160]}")
    answer_looks_external = any(marker in answer for marker in _EXTERNAL_FACT_MARKERS)
    if answer_looks_external and not any(item.material for item in verification.claims):
        issues.append("verifier returned no material claims for an externally sourced answer")
    return verification.accepted and verification.instruction_adherent and not issues, issues


async def verify_answer(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    runtime.context.events.stage("verify", "started", "检查每条实质性结论的实体、时间与来源")
    if _is_rejection_only_outcome(state):
        verification = AnswerVerification(
            accepted=True,
            instruction_adherent=True,
            instruction_issues=[],
            claims=[],
            missing_evidence_queries=[],
            revised_answer=None,
            summary="服务端审批拒绝属于可验证控制结果，不含外部事实",
        )
        runtime.context.events.stage("verify", "completed", "审批拒绝结果校验通过")
        return {
            "verification": verification.model_dump(mode="json"),
            "answer_final": str(state.get("answer_draft") or ""),
            "status": "completed",
            "error_code": None,
        }
    verification = await runtime.context.model.structured(
        AnswerVerification,
        function_name="verify_claim_evidence",
        description="逐条审计答案中的实质性 Claim-Evidence 关联",
        system_prompt=VERIFY_SYSTEM_PROMPT,
        payload={
            "user_goal": state.get("intent"),
            "current_user_request": state.get("user_text"),
            "constraints": list((state.get("intent") or {}).get("constraints") or []),
            "deliverable": str((state.get("intent") or {}).get("deliverable") or ""),
            "answer_draft": state.get("answer_draft"),
            "successful_evidence": _bounded(state.get("evidence") or []),
            "failed_observations": _bounded(
                [item for item in state.get("tool_results") or [] if item.get("success") is False]
            ),
            "rules": [
                "evidence_ids 必须逐字取自 successful_evidence；证据不足时给 revised_answer。",
                "草稿超出用户要求的范围、字段、格式或长度时 instruction_adherent 必须为 false。",
            ],
        },
        max_tokens=6_000,
    )
    accepted, deterministic_issues = _deterministic_verification(
        verification,
        answer=str(state.get("answer_draft") or ""),
        evidence=state.get("evidence") or [],
    )
    verification_payload = verification.model_dump(mode="json")
    if deterministic_issues:
        verification_payload["accepted"] = False
        verification_payload["deterministic_issues"] = deterministic_issues
    if accepted:
        runtime.context.events.stage("verify", "completed", "Claim-Evidence 校验通过")
        return {
            "verification": verification_payload,
            "answer_final": str(state.get("answer_draft") or ""),
            "status": "partial" if state.get("status") == "partial" else "completed",
        }

    evidence_issues = [
        issue
        for issue in deterministic_issues
        if not issue.startswith("instruction violation:")
    ]
    instruction_only_gap = (
        not verification.instruction_adherent
        and not evidence_issues
        and not verification.missing_evidence_queries
        and all(not claim.material or claim.supported for claim in verification.claims)
    )
    current_round = int(state.get("verification_round") or 0)
    max_rounds = int(state.get("max_verification_rounds") or 0)
    if current_round < max_rounds:
        if instruction_only_gap:
            revised = str(verification.revised_answer or "").strip()
            retry_mode = "verify_revised_answer" if revised else "redraft"
            verification_payload["retry_mode"] = retry_mode
            runtime.context.events.stage(
                "verify",
                "failed",
                "答案未遵守用户交付约束，将在预算内修正并重新校验",
                error_code="instruction_adherence_gap",
            )
            return {
                "verification": verification_payload,
                "verification_round": current_round + 1,
                "answer_draft": revised,
                "status": "running",
                "error_code": "instruction_adherence_gap",
            }
        queries = verification.missing_evidence_queries or [
            str((state.get("intent") or {}).get("objective") or state.get("user_text") or "")
        ]
        runtime.context.events.stage(
            "verify",
            "failed",
            "证据校验未通过，将在预算内重新检索或规划",
            error_code="claim_evidence_gap",
        )
        return {
            "verification": verification_payload,
            "verification_round": current_round + 1,
            "search_queries": queries,
            "answer_draft": "",
            "status": "running",
            "error_code": "claim_evidence_gap",
        }

    final_answer = str(verification.revised_answer or "").strip()
    if not final_answer:
        final_answer = (
            "现有答案未遵守用户的交付约束，且本轮修订预算已经耗尽；"
            "为避免发布偏离要求的内容，原草稿不会作为答案发布。"
            if instruction_only_gap
            else (
                "现有答案未通过 Claim-Evidence 校验，且本轮检索预算已经耗尽；"
                "为避免发布无依据结论，未验证草稿已保留但不会作为答案发布。"
            )
        )
    runtime.context.events.stage(
        "verify",
        "completed",
        (
            "答案约束仍有缺口，已使用校验器修订稿"
            if instruction_only_gap
            else "证据仍有缺口，已删除或弱化无支持结论"
        ),
        error_code=(
            "instruction_adherence_gap"
            if instruction_only_gap
            else "claim_evidence_gap"
        ),
    )
    return {
        "verification": verification_payload,
        "answer_final": final_answer,
        "status": "partial",
        "error_code": (
            "instruction_adherence_gap"
            if instruction_only_gap
            else "claim_evidence_gap"
        ),
    }


def route_verification(state: AgentState) -> Literal["discover", "draft", "verify", "publish"]:
    if str(state.get("answer_final") or "").strip():
        return "publish"
    retry_mode = str((state.get("verification") or {}).get("retry_mode") or "")
    if retry_mode == "verify_revised_answer":
        return "verify"
    if retry_mode == "redraft":
        return "draft"
    return "discover"


async def publish_answer(state: AgentState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    answer = str(state.get("answer_final") or state.get("answer_draft") or "").strip()
    runtime.context.events.text(answer)
    runtime.context.events.stage(
        "publish",
        "completed",
        "答案与运行终态已准备原子发布",
        error_code=state.get("error_code"),
    )
    return {"answer_final": answer}


def build_agent_graph(*, checkpointer: Any) -> Any:
    """Compile the application-owned graph once for a shared checkpointer."""
    builder = StateGraph(
        AgentState,
        context_schema=GraphContext,
        input_schema=AgentGraphInput,
    )
    builder.add_node("begin", begin_turn)
    builder.add_node("understand", understand_goal)
    builder.add_node("clarify", clarification_answer)
    builder.add_node("discover", discover_tools)
    builder.add_node("plan", create_plan)
    builder.add_node("policy", apply_execution_policy)
    builder.add_node("execute_read_action", execute_read_action)
    builder.add_node(
        "approval",
        await_approval,
        destinations=("execute_approved_action", "reflect"),
    )
    builder.add_node("execute_approved_action", execute_approved_action)
    builder.add_node("reflect", reflect_on_progress)
    builder.add_node("draft", draft_answer)
    builder.add_node("verify", verify_answer)
    builder.add_node("publish", publish_answer)

    builder.add_edge(START, "begin")
    builder.add_edge("begin", "understand")
    builder.add_conditional_edges("understand", route_after_understanding)
    builder.add_edge("clarify", "publish")
    builder.add_edge("discover", "plan")
    builder.add_conditional_edges("plan", route_after_plan)
    builder.add_conditional_edges("policy", route_policy)
    builder.add_edge("execute_read_action", "reflect")
    builder.add_edge("execute_approved_action", "reflect")
    builder.add_conditional_edges("reflect", route_reflection)
    builder.add_edge("draft", "verify")
    builder.add_conditional_edges("verify", route_verification)
    builder.add_edge("publish", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "ANSWER_SYSTEM_PROMPT",
    "CONTROL_SYSTEM_PROMPT",
    "VERIFY_SYSTEM_PROMPT",
    "build_agent_graph",
]
