"""Independent Goal-mode LangGraph.

The graph owns goal intake, bounded action selection, observation, monitoring,
and terminal publication.  It deliberately does not import the Planning or
Team graphs: those are separate product modes, not Goal dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite, interrupt
from pydantic import BaseModel

from src.agent.runtime_errors import emit_runtime_error
from src.tools.base import citation_scoped_evidence_records, evidence_record_is_eligible, tool_execution_context

from ..answer_contract import finalize_terminal_answer
from ..claim_evidence import build_claim_evidence_ledger
from ..evidence_identity import canonicalize_evidence_markers
from ..knowledge_research import (
    document_catalog_for_model,
    knowledge_research_instructions,
    research_tool_names,
    user_requests_knowledge_base_only,
)
from ..model_projection import StructuredContractProjectionCallback, safe_projection_text
from ..state import merge_records
from .contracts import (
    GoalAction,
    GoalAssessment,
    GoalContract,
    GoalCriterion,
    GoalCriterionAssessment,
    GoalFinalAnswer,
)
from .state import GoalContext, GoalGraphInput, GoalState


GOAL_DEFAULT_ITERATION_LIMIT = 8
GOAL_DEFAULT_REPLAN_LIMIT = 3
GOAL_DEFAULT_MODEL_CALL_LIMIT = 24
GOAL_DEFAULT_ACTION_VALIDATION_REPAIR_LIMIT = 1


def _bounded_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(str(os.getenv(name) or default).strip())))
    except (TypeError, ValueError):
        return default


def goal_runtime_limits(*, tool_call_limit: int | None = None) -> dict[str, int]:
    """Return server-owned Goal limits, never model-authored limits."""

    return {
        "goal_iteration_limit": _bounded_env(
            "AGENT_GOAL_MAX_ITERATIONS",
            GOAL_DEFAULT_ITERATION_LIMIT,
            minimum=1,
            maximum=64,
        ),
        "goal_replan_limit": _bounded_env(
            "AGENT_GOAL_MAX_REPLANS",
            GOAL_DEFAULT_REPLAN_LIMIT,
            minimum=0,
            maximum=16,
        ),
        "goal_tool_call_limit": max(1, int(tool_call_limit or _bounded_env(
            "AGENT_MAX_TOOL_CALLS",
            32,
            minimum=1,
            maximum=256,
        ))),
        "goal_model_call_limit": _bounded_env(
            "AGENT_GOAL_MAX_MODEL_CALLS",
            GOAL_DEFAULT_MODEL_CALL_LIMIT,
            minimum=4,
            maximum=128,
        ),
        "goal_action_validation_repair_limit": _bounded_env(
            "AGENT_GOAL_ACTION_VALIDATION_REPAIR_LIMIT",
            GOAL_DEFAULT_ACTION_VALIDATION_REPAIR_LIMIT,
            minimum=0,
            maximum=3,
        ),
    }


def goal_turn_defaults(*, tool_call_limit: int | None = None) -> dict[str, Any]:
    """Fresh Goal channels for one user-selected Goal turn."""

    limits = goal_runtime_limits(tool_call_limit=tool_call_limit)
    started_at = datetime.now().astimezone().isoformat()
    return {
        "engine": "langgraph_goal_v1",
        "status": "running",
        "error_code": None,
        "terminal_detail": "",
        "answer_draft": "",
        "answer_final": "",
        "agent_mode": "goal",
        "resolved_agent_mode": "goal",
        "orchestrator_mode": "goal_v1",
        "goal_contract": None,
        "goal_contract_confirmed": False,
        "goal_confirmation_status": "not_required",
        "goal_status": "pending",
        "goal_action": None,
        "goal_action_status": "",
        "goal_action_validation_repairs": 0,
        "goal_last_observation": None,
        "goal_assessment": None,
        "goal_progress": "",
        "goal_criteria": [],
        "goal_blocker": "",
        "goal_terminal_reason": "",
        "goal_current_action_id": "",
        "goal_pending_confirmation_criteria": [],
        "goal_started_at": started_at,
        "goal_iterations": 0,
        "goal_replan_count": 0,
        **limits,
        "goal_evidence_ids": Overwrite([]),
        "goal_history": Overwrite([]),
        "tool_results": Overwrite([]),
        "evidence": Overwrite([]),
        "claim_evidence": [],
        "completed_tool_call_ids": Overwrite([]),
        "approved_tool_call_ids": Overwrite([]),
        "rejected_tool_call_ids": Overwrite([]),
        "runtime_errors": Overwrite([]),
        "tool_call_count": Overwrite(0),
        "model_turn_count": Overwrite(0),
        "pending_interrupt": None,
    }


def _safe_json(value: Any, *, limit: int = 24_000) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        encoded = str(value)
    return encoded[:limit]


def _short(value: Any, limit: int = 1_200) -> str:
    return str(value or "").strip()[:limit]


def _safe_validation_detail(error: BaseException) -> str:
    """Keep model-correctable schema errors actionable without echoing inputs."""

    errors = getattr(error, "errors", None)
    if callable(errors):
        try:
            items = errors(include_input=False)
        except TypeError:
            items = errors()
        details = []
        for item in items[:8] if isinstance(items, list) else []:
            if not isinstance(item, Mapping):
                continue
            location = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
            message = _short(item.get("msg") or item.get("type"), 240)
            if message:
                details.append(f"{location}: {message}")
        if details:
            return "; ".join(details)[:1_000]
    # Non-Pydantic validators can include the rejected value in their message.
    # The registered schema is sent separately during repair, so keep this
    # persisted/projection-safe explanation free of model-authored arguments.
    return f"{type(error).__name__}: 参数与已注册的工具 schema 不匹配，请按提供的 schema 修正。"


def _validate_goal_tool_action(
    action: Mapping[str, Any],
    registry: Any,
    *,
    context: GoalContext,
    tool_results: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Preflight a model-selected operation against the existing registry."""

    tool_name = _short(action.get("tool_name"), 128)
    tool = registry.get_tool(tool_name) if tool_name else None
    if tool is None:
        return None, {
            "tool_name": tool_name,
            "error": f"未注册的工具 operation：{tool_name or '空工具名'}。必须选择工具目录中的 operation 名称。",
            "available_operations": list(registry.get_tool_names())[:96],
        }
    arguments = action.get("arguments")
    if not isinstance(arguments, Mapping):
        arguments = {}
    try:
        with tool_execution_context(
            conversation_id=getattr(context, "conversation_id", None), run_id=getattr(context, "run_id", None),
            tenant_id=getattr(context, "tenant_id", None), owner_id=getattr(context, "owner_id", None),
            knowledge_base_ids=getattr(context, "knowledge_base_ids", ()),
        ):
            validated = registry.validate_model_arguments(
                tool_name, dict(arguments), approved=False,
                validation_context={"tool_results": list(tool_results)},
            )
    except Exception as exc:
        try:
            schema = tool.to_openai_schema(include_server_controlled=False)
            function = schema.get("function") if isinstance(schema, Mapping) else None
            parameters = function.get("parameters") if isinstance(function, Mapping) else None
        except Exception:
            parameters = None
        return None, {
            "tool_name": tool_name,
            "error": _safe_validation_detail(exc),
            "parameters_schema": parameters,
        }
    return validated, None


def _model_call_limit_reached(state: Mapping[str, Any]) -> bool:
    return int(state.get("model_turn_count") or 0) >= int(state.get("goal_model_call_limit") or 0)


def _goal_evidence(state: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in citation_scoped_evidence_records(state.get("evidence") or []):
        if not isinstance(item, Mapping):
            continue
        evidence_id = str(item.get("evidence_id") or item.get("id") or "").strip()
        if evidence_id and evidence_record_is_eligible(item):
            result[evidence_id] = item
    return result


def _goal_criteria(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in state.get("goal_criteria") or []
        if isinstance(item, Mapping)
    ]


def _goal_final_answer_evidence_ids(
    criteria: Sequence[Mapping[str, Any]],
    requested_ids: Sequence[Any],
    eligible_evidence_ids: set[str],
) -> list[str]:
    """Keep model-selected citations, or use evidence already bound to satisfied criteria."""

    selected = list(dict.fromkeys(
        str(value)
        for value in requested_ids
        if str(value) in eligible_evidence_ids
    ))
    if selected:
        return selected
    return list(dict.fromkeys(
        str(evidence_id)
        for criterion in criteria
        if str(criterion.get("status") or "") == "satisfied"
        for evidence_id in criterion.get("evidence_ids") or []
        if str(evidence_id) in eligible_evidence_ids
    ))


def _contract(state: Mapping[str, Any]) -> Mapping[str, Any]:
    value = state.get("goal_contract")
    return value if isinstance(value, Mapping) else {}


def _contract_needs_confirmation(contract: Mapping[str, Any]) -> bool:
    criteria = contract.get("success_criteria")
    if (
        str(contract.get("clarification_question") or "").strip()
        or not str(contract.get("scope") or "").strip()
        or not isinstance(contract.get("constraints"), list)
        or not contract.get("constraints")
        or not isinstance(criteria, list)
        or not criteria
    ):
        return True
    return any(
        str(item.get("verification_method") or "unknown") == "unknown"
        for item in criteria
        if isinstance(item, Mapping)
    )


def _normalize_contract(value: GoalContract) -> dict[str, Any]:
    contract = value.model_dump(mode="json")
    criteria: list[dict[str, Any]] = []
    for index, raw in enumerate(contract.get("success_criteria") or [], start=1):
        item = dict(raw)
        item["criterion_id"] = _short(item.get("criterion_id") or f"criterion-{index}", 96)
        item["status"] = "pending"
        item["evidence_ids"] = []
        item["explanation"] = ""
        criteria.append(item)
    contract["success_criteria"] = criteria[:8]
    return contract


def _clarification_question(contract: Mapping[str, Any]) -> str:
    """Return an explicit blocking question, including legacy model output."""

    question = safe_projection_text(contract.get("clarification_question")).strip()
    if question:
        return question
    # Defensive compatibility for a model that asks in progress_text instead
    # of filling the dedicated field: never let the graph ask and then proceed.
    progress = safe_projection_text(contract.get("progress_text")).strip()
    if "?" in progress or "？" in progress:
        return progress
    return ""


def _normalize_action(action: GoalAction, state: Mapping[str, Any]) -> dict[str, Any]:
    value = action.model_dump(mode="json")
    action_id = _short(value.get("action_id"), 128)
    if not action_id:
        action_id = f"goal:{state.get('run_id') or 'run'}:{int(state.get('goal_iterations') or 0) + 1}"
    value["action_id"] = action_id
    value["tool_name"] = _short(value.get("tool_name"), 128)
    value["criterion_ids"] = [
        _short(item, 96)
        for item in value.get("criterion_ids") or []
        if _short(item, 96)
    ][:8]
    return value


def _validate_goal_action_criteria(
    action: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any] | None:
    known_ids = {
        str(item.get("criterion_id") or "")
        for item in _goal_criteria(state)
        if str(item.get("criterion_id") or "").strip()
    }
    requested_ids = [str(value) for value in action.get("criterion_ids") or []]
    if not requested_ids:
        return {
            "error": "每个 Goal 工具动作都必须指定至少一个它要收集证据的当前完成条件。",
        }
    unknown_ids = [value for value in requested_ids if value not in known_ids]
    if not unknown_ids:
        return None
    return {
        "error": "Goal 动作引用了当前合同中不存在的完成条件，请仅使用当前 criteria 中的 criterion_id。",
        "unknown_criterion_ids": unknown_ids,
    }


def _validate_goal_finish_action(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Do not accept a finish action while required criteria remain unverified."""

    pending = [
        {
            "criterion_id": str(item.get("criterion_id") or ""),
            "description": _short(item.get("description"), 240),
            "status": str(item.get("status") or "pending"),
            "evidence_ids": [
                str(value)
                for value in item.get("evidence_ids") or []
                if str(value or "").strip()
            ],
        }
        for item in _goal_criteria(state)
        if bool(item.get("required", True))
        and str(item.get("status") or "pending") != "satisfied"
    ]
    if not pending:
        return None
    return {
        "error": "不能结束 Goal：仍有必需完成条件未被核验为 satisfied。请根据 pending_criteria 中的缺口和已关联证据，选择补充取证工具或 ask_user；不得重复选择 finish。",
        "pending_criteria": pending,
    }


def _link_action_evidence_to_criteria(
    criteria: list[dict[str, Any]],
    action: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Associate an eligible executor receipt only with criteria targeted by its action."""

    if not isinstance(evidence, Mapping) or not evidence_record_is_eligible(evidence):
        return criteria
    action_id = str(action.get("action_id") or "")
    evidence_action_id = str(evidence.get("action_id") or "")
    evidence_id = str(evidence.get("evidence_id") or evidence.get("id") or "")
    if not action_id or evidence_action_id != action_id or not evidence_id:
        return criteria
    target_ids = {str(value) for value in action.get("criterion_ids") or []}
    if not target_ids:
        return criteria

    linked: list[dict[str, Any]] = []
    for criterion in criteria:
        item = dict(criterion)
        if str(item.get("criterion_id") or "") in target_ids:
            evidence_ids = [
                str(value)
                for value in item.get("evidence_ids") or []
                if str(value or "").strip()
            ]
            for citation in citation_scoped_evidence_records([evidence]):
                citation_id = str(citation.get("evidence_id") or citation.get("id") or "")
                if citation_id and citation_id not in evidence_ids:
                    evidence_ids.append(citation_id)
            item["evidence_ids"] = evidence_ids[-24:]
        linked.append(item)
    return linked


def _evidence_summary(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for item in citation_scoped_evidence_records(state.get("evidence") or [])[-40:]:
        if not isinstance(item, Mapping):
            continue
        summary.append(
            {
                "evidence_id": _short(item.get("evidence_id") or item.get("id"), 96),
                "tool_name": _short(item.get("tool_name"), 128),
                "action_id": _short(item.get("action_id"), 128),
                "data_time": _short(item.get("data_time"), 160),
                "source_refs": [
                    _short(ref, 240)
                    for ref in list(item.get("source_refs") or [])[:8]
                ],
                "eligible": evidence_record_is_eligible(item),
                # Retain the observation, not just its identifier. Otherwise
                # the final narrator cannot distinguish missing data from a
                # later control-plane failure.
                "result": _safe_json(item.get("result"), limit=4_000),
            }
        )
    return summary


def _goal_system_prompt(
    state: Mapping[str, Any],
    instruction: str,
) -> str:
    base = _short(state.get("system_prompt"), 8_000)
    knowledge_policy = knowledge_research_instructions(
        selected=bool(state.get("knowledge_base_ids")),
        only_pdf=user_requests_knowledge_base_only(state.get("user_text")),
    )
    return (
        f"{base}\n\n" if base else ""
    ) + (f"{knowledge_policy}\n\n" if knowledge_policy else "") + instruction + (
        "\n面向用户的 progress_text 是聊天正文的一部分，必须由你结合当前请求和实际结果撰写。"
        "像持续与用户沟通一样，简短、具体、自然，承接 previous_update，只补充新信息。"
        "使用用户的语言；不要念流程、列状态、复述合同，不要写阶段标题或固定开场套话。"
        "不要展示内部推理、schema、字段名、工具函数名、证据编号、机器枚举值或原始链接；来源可以用日常名称。"
        "不要把工具调用成功等同于任务完成，也不要声称尚未执行的动作已经成功。"
        "如果当前契约有 progress_text 字段，优先输出该字段；有新信息时填写，没有新信息时允许为空，不为节点凑一句话。"
    )


def _publish_goal_progress(
    context: GoalContext,
    value: BaseModel,
    *,
    phase: str,
    kind: str,
    projection_id: str,
) -> None:
    """Project only the model's explicit Goal progress field.

    Goal lifecycle summaries remain control-plane stage metadata. The chat
    projection must come from the structured model contract itself, just like
    the existing Team projection path, so the UI never turns a stage enum
    into canned conversational copy.
    """

    progress = safe_projection_text(getattr(value, "progress_text", "")).strip()
    _publish_goal_progress_text(
        context,
        progress,
        phase=phase,
        kind=kind,
        projection_id=projection_id,
    )


def _publish_goal_progress_text(
    context: GoalContext,
    progress: str,
    *,
    phase: str,
    kind: str,
    projection_id: str,
) -> None:
    progress = safe_projection_text(progress).strip()
    if not progress:
        return
    publisher = getattr(context.events, "publish_goal_progress_projection", None)
    if not callable(publisher):
        publisher = getattr(context.events, "publish_model_projection", None)
        if not callable(publisher):
            return
        publisher(
            progress,
            display_part_name="agent-model-projection",
            scope="goal",
            phase=phase,
            kind=kind,
            projection_id=projection_id,
        )
        return
    publisher(progress, phase=phase, kind=kind, projection_id=projection_id)


async def _structured_call(
    context: GoalContext,
    schema: type[BaseModel],
    messages: list[Any],
    *,
    projection_phase: str,
    projection_kind: str,
    projection_id: str,
    project_progress: bool = True,
) -> BaseModel:
    structured_kwargs: dict[str, Any] = {"include_raw": True}
    projection_callback: StructuredContractProjectionCallback | None = None
    if getattr(context.model, "supports_exact_structured_output", False):
        # Goal follows the existing structured-contract projection boundary:
        # stream only the explicit progress_text field while keeping the
        # business contract and evidence gate authoritative.
        structured_kwargs.update({"tool_choice": schema.__name__, "stream": True})
    if project_progress and getattr(context.model, "supports_exact_structured_output", False):
        projection_callback = StructuredContractProjectionCallback(
            context,
            projection_id=projection_id,
            scope="goal",
            collaboration_id="",
            agent_id="",
            task_id="",
            phase=projection_phase,
            kind=projection_kind,
            target_tool_name=schema.__name__,
            display_part_name="agent-model-projection",
        )
    runnable = context.model.with_structured_output(schema, **structured_kwargs)
    if projection_callback is not None:
        runnable = runnable.with_config({"callbacks": [projection_callback]})
    result = await runnable.ainvoke(messages)
    if isinstance(result, Mapping):
        parsed = result.get("parsed")
        if isinstance(parsed, BaseModel):
            # The callback covers providers that stream structured tool-call
            # arguments. The fallback also supports deterministic adapters
            # and test models that only expose the completed parsed contract.
            if project_progress:
                _publish_goal_progress(
                    context, parsed, phase=projection_phase,
                    kind=projection_kind, projection_id=projection_id,
                )
            return parsed
        error = result.get("parsing_error")
        raise ValueError(f"{schema.__name__} structured output unavailable: {error or 'missing parsed value'}")
    if isinstance(result, BaseModel):
        if project_progress:
            _publish_goal_progress(
                context, result, phase=projection_phase,
                kind=projection_kind, projection_id=projection_id,
            )
        return result
    raise ValueError(f"{schema.__name__} structured output returned an unsupported value")


def _record_history(
    state: Mapping[str, Any],
    *,
    event: str,
    details: Mapping[str, Any],
) -> list[dict[str, Any]]:
    item = {
        "id": f"{event}:{state.get('run_id') or 'run'}:{len(state.get('goal_history') or []) + 1}",
        "event": event,
        "occurred_at": datetime.now().astimezone().isoformat(),
        **{str(key): value for key, value in details.items()},
    }
    return [item]


def _confirmation_fingerprint(state: Mapping[str, Any], *, kind: str) -> str:
    payload = {
        "run_id": str(state.get("run_id") or ""),
        "kind": kind,
        "contract": state.get("goal_contract"),
        "criteria": state.get("goal_pending_confirmation_criteria") or [],
        "action": state.get("goal_action"),
    }
    return hashlib.sha256(_safe_json(payload).encode("utf-8")).hexdigest()


def _confirmation_payload(state: Mapping[str, Any], *, kind: str, summary: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "engine": "langgraph_goal_v1",
        "run_id": str(state.get("run_id") or ""),
        "conversation_id": str(state.get("conversation_id") or ""),
        "fingerprint": _confirmation_fingerprint(state, kind=kind),
        "summary": _short(summary, 1_600),
        "goal_contract": state.get("goal_contract"),
        "criteria": _goal_criteria(state),
        "pending_criteria": list(state.get("goal_pending_confirmation_criteria") or []),
        "action": state.get("goal_action"),
    }


async def _goal_intake(state: GoalState, runtime: Any) -> dict[str, Any]:
    context: GoalContext = runtime.context
    if isinstance(state.get("goal_contract"), Mapping):
        return {
            "goal_status": "running" if state.get("goal_contract_confirmed") else "pending",
        }

    context.events.stage(
        "goal.intake",
        "started",
        "正在提取目标、约束和完成条件",
        action_id=f"goal:{context.run_id}:intake",
        details={"kind": "goal_intake", "phase": "goal_intake"},
    )
    try:
        contract = await _structured_call(
            context,
            GoalContract,
            [
                SystemMessage(
                    content=_goal_system_prompt(
                        state,
                        "你是 Goal 模式的目标分析器。把用户请求转换成一个可执行的 GoalContract。"
                        "必须写出至少一个具体的 success_criteria，并为每个条件选择 verification_method。"
                        "可验证的方法包括 tool_result、runtime_check、user_confirmation、model_assessment。"
                        "如果无法判断验证方式，使用 unknown，不要编造完成标准。"
                        "把用户明确的限制逐条填入 constraints，不要只放在 scope 中；"
                        "范围和限制已明确时无需再向用户索要同样的信息。"
                        "对非阻塞的角度偏好采用合理、宽泛的默认范围直接推进，不要问用户从几个分析角度中任选其一。"
                        "只有缺失信息会实质阻碍安全或有用地推进时，才将一个具体问题写入 clarification_question；"
                        "一旦提出 clarification_question，Goal 必须暂停等待回答。progress_text 只能陈述你理解的目标和关注点，不提问；"
                        "尚未执行任何工具时，不要声称实时数据不可获取、数据最新程度或任何数据结果。"
                        "不要把通用免责声明、回答格式或分析质量要求编造成用户目标的 success_criteria，除非用户明确要求。"
                        "不要逐句复述用户已经说明的要求和限制。"
                        "不要执行工具，不要输出最终答案，只返回 GoalContract。",
                    )
                ),
                HumanMessage(content=_short(state.get("user_text"), 8_000)),
            ],
            projection_phase="goal_intake",
            projection_kind="GoalContract",
            projection_id=f"{context.run_id}:goal:intake:{int(state.get('model_turn_count') or 0) + 1}",
            # Do not stream an intake question before the server has decided
            # whether this contract will actually pause for user input.
            project_progress=False,
        )
        normalized = _normalize_contract(contract)
    except Exception as exc:
        detail = _short(f"{type(exc).__name__}: {exc}", 1_000)
        context.events.stage(
            "goal.intake",
            "failed",
            "目标合同提取失败，本轮未执行工具",
            action_id=f"goal:{context.run_id}:intake",
            error_code="goal_contract_invalid",
            details={"kind": "goal_intake", "error": detail},
        )
        return {
            "goal_status": "failed",
            "status": "failed",
            "error_code": "goal_contract_invalid",
            "terminal_detail": "Goal 目标合同无法可靠提取，本轮未执行工具。",
            "goal_terminal_reason": "目标合同提取失败",
            "goal_blocker": detail,
            "model_turn_count": 1,
        }

    question = _clarification_question(normalized)
    if question:
        normalized["clarification_question"] = question
    needs_confirmation = _contract_needs_confirmation(normalized)
    progress_text = question or safe_projection_text(contract.progress_text).strip()
    _publish_goal_progress_text(
        context,
        progress_text,
        phase="goal_intake",
        kind="GoalContract",
        projection_id=f"{context.run_id}:goal:intake:{int(state.get('model_turn_count') or 0) + 1}",
    )
    context.events.stage(
        "goal.intake",
        "completed",
        "目标合同已提取，正在检查是否需要确认",
        action_id=f"goal:{context.run_id}:intake",
        details={
            "kind": "goal_intake",
            "phase": "goal_intake",
            "criteria_count": len(normalized.get("success_criteria") or []),
            "confirmation_required": needs_confirmation,
        },
    )
    return {
        "goal_contract": normalized,
        "goal_criteria": list(normalized.get("success_criteria") or []),
        "goal_contract_confirmed": not needs_confirmation,
        "goal_confirmation_status": "required" if needs_confirmation else "not_required",
        "goal_status": "pending" if needs_confirmation else "running",
        "goal_progress": progress_text,
        "model_turn_count": 1,
        "goal_history": _record_history(
            state,
            event="goal_intake",
            details={"confirmation_required": needs_confirmation},
        ),
    }


def _after_intake(state: Mapping[str, Any]) -> str:
    if str(state.get("goal_status") or "") in {"failed", "blocked"}:
        return "goal_finalize"
    if not state.get("goal_contract_confirmed"):
        return "goal_confirm"
    return "goal_action_select"


async def _goal_confirm(state: GoalState, runtime: Any) -> dict[str, Any]:
    context: GoalContext = runtime.context
    if state.get("goal_contract_confirmed") and state.get("goal_status") == "running":
        return {}

    waiting_for_criteria = list(state.get("goal_pending_confirmation_criteria") or [])
    kind = "goal_progress_confirmation" if waiting_for_criteria else "goal_contract_confirmation"
    contract_question = _short(_contract(state).get("clarification_question"), 1_200)
    summary = contract_question or _short(state.get("goal_progress"), 1_200) or (
        "Goal 当前有无法自动验证的完成条件，请确认是否继续。"
        if waiting_for_criteria
        else "请确认 Goal 提取出的目标和完成条件，确认后才会执行工具。"
    )
    payload = _confirmation_payload(state, kind=kind, summary=summary)
    context.events.stage(
        "goal.confirm",
        "started",
        summary,
        action_id=f"goal:{context.run_id}:confirm:{kind}",
        details={"kind": kind, "phase": "goal_confirm", "criteria": waiting_for_criteria},
    )
    decision = interrupt(payload)
    decision_value = str(decision.get("decision") or "").strip().lower() if isinstance(decision, Mapping) else ""
    if decision_value == "modify":
        modified_text = _short(decision.get("message"), 8_000) if isinstance(decision, Mapping) else ""
        if not modified_text:
            context.events.stage(
                "goal.confirm",
                "blocked",
                "Goal 修改内容为空，已停止执行",
                action_id=f"goal:{context.run_id}:confirm:{kind}",
                error_code="goal_modification_empty",
                details={"kind": kind, "phase": "goal_confirm"},
            )
            return {
                "goal_status": "blocked",
                "status": "blocked",
                "error_code": "goal_modification_empty",
                "terminal_detail": "Goal 修改内容为空，无法重新提取目标。",
                "goal_terminal_reason": "目标修改为空",
                "goal_blocker": "请提供具体的修改后目标。",
                "goal_confirmation_status": "rejected",
                "pending_interrupt": None,
            }
        context.events.stage(
            "goal.confirm",
            "completed",
            "已收到修改后的 Goal，重新提取完成条件",
            action_id=f"goal:{context.run_id}:confirm:{kind}",
            details={"kind": kind, "phase": "goal_confirm", "modified": True},
        )
        return {
            "user_text": modified_text,
            "goal_contract": None,
            "goal_contract_confirmed": False,
            "goal_confirmation_status": "modified",
            "goal_status": "revising",
            "goal_criteria": [],
            "goal_action": None,
            "goal_action_status": "",
            "goal_current_action_id": "",
            "goal_last_observation": None,
            "goal_assessment": None,
            "goal_pending_confirmation_criteria": [],
            "goal_evidence_ids": Overwrite([]),
            "goal_iterations": 0,
            "goal_replan_count": 0,
            "tool_results": Overwrite([]),
            "evidence": Overwrite([]),
            "completed_tool_call_ids": Overwrite([]),
            "approved_tool_call_ids": Overwrite([]),
            "rejected_tool_call_ids": Overwrite([]),
            "runtime_errors": Overwrite([]),
            "tool_call_count": Overwrite(0),
            "pending_interrupt": None,
            "goal_progress": "",
        }
    if decision_value != "approve":
        context.events.stage(
            "goal.confirm",
            "blocked",
            "用户未确认 Goal，已停止执行",
            action_id=f"goal:{context.run_id}:confirm:{kind}",
            error_code="goal_confirmation_rejected",
            details={"kind": kind, "phase": "goal_confirm"},
        )
        return {
            "goal_status": "blocked",
            "goal_action_status": "blocked",
            "status": "blocked",
            "error_code": "goal_confirmation_rejected",
            "terminal_detail": "用户未确认 Goal 的目标或完成条件。",
            "goal_terminal_reason": "用户未确认目标",
            "goal_blocker": "用户拒绝或未提供有效的 Goal 确认。",
            "goal_confirmation_status": "rejected",
            "pending_interrupt": None,
        }

    criteria = _goal_criteria(state)
    confirmed_ids = set(waiting_for_criteria)
    confirmation_evidence_id = f"goal_user_{context.run_id}_{int(state.get('goal_iterations') or 0)}"
    updated_criteria: list[dict[str, Any]] = []
    for criterion in criteria:
        item = dict(criterion)
        if item.get("criterion_id") in confirmed_ids and item.get("verification_method") == "user_confirmation":
            item["status"] = "satisfied"
            item["evidence_ids"] = [confirmation_evidence_id]
            item["explanation"] = "用户已确认该完成条件。"
        elif item.get("status") not in {"satisfied", "failed"}:
            item["status"] = "pending"
        updated_criteria.append(item)
    confirmation_evidence = {
        "id": confirmation_evidence_id,
        "evidence_id": confirmation_evidence_id,
        "action_id": f"goal:{context.run_id}:confirm:{kind}",
        "tool_name": "goal_confirmation",
        "tool_call_id": "",
        "effect": "read",
        "success": True,
        "has_data": True,
        "usable": True,
        "evidence_eligible": True,
        "source_refs": ["user:goal-confirmation"],
        "result": {"confirmation": "approve", "criteria": waiting_for_criteria},
        "observed_at": datetime.now().astimezone().isoformat(),
    }
    context.events.stage(
        "goal.confirm",
        "completed",
        "Goal 已确认，继续执行",
        action_id=f"goal:{context.run_id}:confirm:{kind}",
        details={"kind": kind, "phase": "goal_confirm", "criteria": waiting_for_criteria},
    )
    return {
        "goal_contract_confirmed": True,
        "goal_confirmation_status": "confirmed",
        "goal_status": "running",
        "goal_criteria": updated_criteria,
        "goal_pending_confirmation_criteria": [],
        "goal_evidence_ids": [confirmation_evidence_id] if waiting_for_criteria else [],
        "evidence": [confirmation_evidence] if waiting_for_criteria else [],
        "pending_interrupt": None,
    }


def _after_confirm(state: Mapping[str, Any]) -> str:
    if str(state.get("goal_status") or "") == "revising":
        return "goal_intake"
    if str(state.get("goal_status") or "") in {"failed", "blocked"}:
        return "goal_finalize"
    return "goal_action_select"


async def _goal_action_select(state: GoalState, runtime: Any) -> dict[str, Any]:
    context: GoalContext = runtime.context
    if int(state.get("goal_iterations") or 0) >= int(state.get("goal_iteration_limit") or 0):
        return {
            "goal_status": "blocked",
            "status": "blocked",
            "goal_blocker": "已达到 Goal 迭代上限。",
            "goal_terminal_reason": "Goal 迭代预算耗尽",
            "terminal_detail": "Goal 已达到服务端迭代上限，保留已有证据并停止继续调用。",
        }
    if int(state.get("tool_call_count") or 0) >= int(state.get("goal_tool_call_limit") or 0):
        return {
            "goal_status": "blocked",
            "status": "blocked",
            "goal_blocker": "已达到 Goal 工具调用上限。",
            "goal_terminal_reason": "Goal 工具调用预算耗尽",
            "terminal_detail": "Goal 已达到服务端工具调用上限，保留已有证据并停止继续调用。",
        }
    if _model_call_limit_reached(state):
        return {
            "goal_status": "blocked",
            "status": "blocked",
            "goal_blocker": "已达到 Goal 模型调用上限。",
            "goal_terminal_reason": "Goal 模型调用预算耗尽",
            "terminal_detail": "Goal 已达到服务端模型调用上限，保留已有证据并停止继续调用。",
        }

    action_number = int(state.get("goal_iterations") or 0) + 1
    context.events.stage(
        "goal.action_select",
        "started",
        "正在选择下一步目标动作",
        action_id=f"goal:{context.run_id}:select:{action_number}",
        details={"kind": "goal_action_select", "phase": "goal_action_select", "iteration": action_number},
    )
    catalog = []
    try:
        catalog = context.catalog.compact_catalog()
    except Exception:
        catalog = context.catalog.planner_catalog() if hasattr(context.catalog, "planner_catalog") else []
    selected_ids = tuple(
        str(item).strip()
        for item in (state.get("knowledge_base_ids") or getattr(context, "knowledge_base_ids", ()) or ())
        if str(item).strip()
    )
    knowledge_base_selected = bool(selected_ids)
    available_names = research_tool_names(
        (str(item.get("operation") or item.get("name") or "") for item in catalog if isinstance(item, Mapping)),
        state,
        selected=knowledge_base_selected,
    )
    catalog = [
        item for item in catalog
        if isinstance(item, Mapping)
        and str(item.get("operation") or item.get("name") or "") in available_names
    ]
    prompt_payload = {
        "contract": _contract(state),
        "criteria": _goal_criteria(state),
        "previous_update": _short(state.get("goal_progress"), 1_200),
        "last_observation": state.get("goal_last_observation"),
        "evidence": _evidence_summary(state),
        "iteration": action_number,
        "knowledge_base_selected": knowledge_base_selected,
        "selected_document_catalog": document_catalog_for_model(context),
        "remaining_limits": {
            "iterations": max(0, int(state.get("goal_iteration_limit") or 0) - action_number + 1),
            "tool_calls": max(0, int(state.get("goal_tool_call_limit") or 0) - int(state.get("tool_call_count") or 0)),
        },
        "tools": catalog[:96],
    }
    model_limit = int(state.get("goal_model_call_limit") or 0)
    calls_available = max(0, model_limit - int(state.get("model_turn_count") or 0))
    repair_limit = max(0, int(state.get("goal_action_validation_repair_limit") or 0))
    max_calls = min(calls_available, repair_limit + 1)
    action_messages: list[Any] = [
        SystemMessage(
            content=_goal_system_prompt(
                state,
                "你是独立 Goal 模式的动作选择器。每次只能选择一个最小、可验证的下一步动作。"
                "如果需要数据或执行能力，kind=tool，tool_name 必须是工具目录中的 operation 名称；"
                "source_id 等来源标识不是 operation 名称。arguments 必须严格符合该 operation 的参数 schema。"
                "如果已有证据足够，可以 kind=finish，但不能凭空声称条件已满足。"
                "如果必须让用户作决定，kind=ask_user。不要调用 Plan 或 Team，不要输出最终答案。"
                "你同时负责下一段面向用户的过程叙述。若有 last_observation，"
                "先用日常语言点明其中与目标相关的新发现或遇到的问题，再自然接上这次要做什么。"
                "只讲实际观察，不引用未通过服务端核验的完成判断；整段控制在一两句，不罗列状态。"
                "若 previous_update 已经交代同一动作，progress_text 留空，不重复预告。"
                "ask_user 时在 progress_text 中直接提出用户需要回答的问题。"
                "criteria 是服务端核验后的事实，以此判断缺口。"
                "progress_text 不要用‘好的’‘收到’‘我来帮你’等确认套话开头；如果只是重复上一段或预告动作而没有新信息，留空。"
                "不要对尚未观察到的数据可用性、实时性或结果作判断。tool 动作应在 criterion_ids 中列出它实际要收集证据的现有完成条件。"
                "只能返回 GoalAction 结构化对象。\n当前工具目录："
                + _safe_json(catalog[:96], limit=60_000),
            )
        ),
        HumanMessage(content=_safe_json(prompt_payload)),
    ]
    action: GoalAction | None = None
    normalized: dict[str, Any] | None = None
    validation_issue: dict[str, Any] | None = None
    validation_repairs = 0
    calls_made = 0
    last_error: BaseException | None = None
    for attempt in range(max_calls):
        calls_made += 1
        try:
            candidate = await _structured_call(
                context,
                GoalAction,
                action_messages,
                projection_phase="goal_action_select",
                projection_kind="GoalAction",
                projection_id=(
                    f"{context.run_id}:goal:action_select:"
                    f"{int(state.get('model_turn_count') or 0) + attempt + 1}"
                ),
                # A proposed operation is not a user-visible update until the
                # server confirms that its operation and arguments are valid.
                project_progress=False,
            )
            action = candidate
            normalized = _normalize_action(candidate, state)
            validation_issue = None
            if (
                normalized.get("kind") == "tool"
                and normalized.get("tool_name") not in available_names
            ):
                validation_issue = {
                    "tool_name": normalized.get("tool_name"),
                    "error": "该工具不在当前可用工具目录中，请从本次提供的目录选择。",
                }
            action_kind = normalized.get("kind")
            if action_kind == "ask_user" and validation_issue is None:
                break
            if action_kind == "finish" and validation_issue is None:
                validation_issue = _validate_goal_finish_action(state)
                if validation_issue is None:
                    break
            if action_kind not in {"tool", "finish", "ask_user"} and validation_issue is None:
                validation_issue = {"error": "Goal 动作类型无效。"}
            if validation_issue is None:
                validation_issue = _validate_goal_action_criteria(normalized, state)
            if validation_issue is None:
                try:
                    _validated_arguments, validation_issue = _validate_goal_tool_action(
                        normalized,
                        context.registry,
                        context=context, tool_results=state.get("tool_results") or [],
                    )
                except Exception as exc:
                    validation_issue = {
                        "tool_name": normalized.get("tool_name"),
                        "error": _safe_validation_detail(exc),
                    }
            if validation_issue is None:
                break

            can_repair = (
                attempt + 1 < max_calls
                and validation_repairs < repair_limit
            )
            context.events.stage(
                "goal.action_validation",
                "retrying" if can_repair else "failed",
                "Goal 工具动作未通过服务端校验" if not can_repair else "Goal 正在修正未通过校验的工具动作",
                action_id=str(normalized.get("action_id") or "") or None,
                error_code="goal_action_invalid",
                details={
                    "kind": "goal_action_validation",
                    "phase": "goal_action_select",
                    "tool_name": normalized.get("tool_name"),
                    "validation_repair": validation_repairs + 1,
                    "repair_limit": repair_limit,
                    "error": validation_issue.get("error"),
                },
            )
            if not can_repair:
                break
            validation_repairs += 1
            action_messages = [
                *action_messages,
                HumanMessage(
                    content=_safe_json(
                        {
                            "correction_required": True,
                            "previous_action": normalized,
                            "validation_feedback": validation_issue,
                            "instruction": (
                                "上一个 Goal 动作未通过服务端校验。请依据反馈修正为有效 GoalAction；"
                                "finish 必须等所有必需完成条件被服务端核验为 satisfied 后才能选择。"
                                "若仍有未满足条件，应依据反馈补充取证或 ask_user，不要再次选择 finish；"
                                "工具参数须符合 operation schema，criterion_ids 只能引用当前合同中的完成条件。"
                                "保留目标意图，但不要重述用户进度或输出说明文字。"
                            ),
                        },
                        limit=16_000,
                    )
                ),
            ]
        except Exception as exc:
            last_error = exc
            break

    if normalized is None or validation_issue is not None or last_error is not None:
        model_budget_exhausted = bool(validation_issue) and calls_made >= calls_available
        budget_blocked = model_budget_exhausted
        if last_error is not None:
            detail = f"{type(last_error).__name__}: 动作选择或修正模型调用失败。"
            if validation_issue:
                detail += f"上次校验提示：{_short(validation_issue.get('error'), 400)}"
        else:
            detail = _short(validation_issue.get("error"), 1_000) if validation_issue else ""
        detail = detail or "Goal 未能生成有效的工具动作。"
        if model_budget_exhausted:
            detail = f"{detail} Goal 模型调用预算已耗尽。"
        failed_action_id = str((normalized or {}).get("action_id") or f"goal:{context.run_id}:select:{action_number}")
        failed_tool_name = _short((normalized or {}).get("tool_name"), 128)
        receipt_error = ValueError(detail)
        receipt = emit_runtime_error(
            context.events,
            receipt_error,
            summary="Goal 未能生成通过校验的工具动作",
            error_code="goal_action_invalid",
            failure_kind="model_output" if last_error is not None else "tool_validation",
            retryable=False,
            terminal_impact="terminal",
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            node="goal_action_select",
            phase="goal_action_select",
            tool_name=failed_tool_name,
            action_id=failed_action_id,
            details={
                "validation_repair_count": validation_repairs,
                "validation_repair_limit": repair_limit,
                "validation_error": detail,
            },
        )
        context.events.stage(
            "goal.action_select",
            "failed",
            "Goal 无法选择可靠动作",
            action_id=failed_action_id,
            error_code="goal_action_invalid",
            details={"kind": "goal_action_select", "error": detail},
        )
        return {
            "goal_action": normalized,
            "goal_action_status": "failed",
            "goal_status": "blocked" if budget_blocked else "failed",
            "status": "blocked" if budget_blocked else "failed",
            "error_code": "goal_action_invalid",
            "terminal_detail": (
                "Goal 动作校验未能在服务端预算内完成。"
                if budget_blocked else "Goal 无法选择通过服务端校验的下一步动作。"
            ),
            "goal_terminal_reason": "动作校验预算耗尽" if budget_blocked else "动作选择或参数校验失败",
            "goal_blocker": detail,
            "runtime_errors": [receipt],
            "model_turn_count": calls_made,
            "goal_action_validation_repair_limit": repair_limit,
            "goal_action_validation_repairs": int(state.get("goal_action_validation_repairs") or 0) + validation_repairs,
        }

    assert action is not None and normalized is not None
    progress = safe_projection_text(action.progress_text)
    _publish_goal_progress(
        context,
        action,
        phase="goal_action_select",
        kind="GoalAction",
        projection_id=f"{context.run_id}:goal:action_select:{int(state.get('model_turn_count') or 0) + calls_made}",
    )
    if validation_repairs:
        context.events.stage(
            "goal.action_validation",
            "completed",
            "Goal 已修正并通过工具参数校验",
            action_id=str(normalized.get("action_id") or "") or None,
            details={
                "kind": "goal_action_validation",
                "phase": "goal_action_select",
                "tool_name": normalized.get("tool_name"),
                "validation_repair": validation_repairs,
                "repair_limit": repair_limit,
            },
        )

    context.events.stage(
        "goal.action_select",
        "completed",
        "已选择下一步 Goal 动作",
        action_id=str(normalized.get("action_id")),
        details={
            "kind": "goal_action_select",
            "phase": "goal_action_select",
            "action_kind": normalized.get("kind"),
            "tool_name": normalized.get("tool_name"),
            "criterion_ids": normalized.get("criterion_ids"),
        },
    )
    if normalized.get("kind") == "ask_user":
        return {
            "goal_action": normalized,
            "goal_action_status": "waiting_for_user",
            "goal_current_action_id": str(normalized.get("action_id")),
            "goal_status": "waiting_for_user",
            "goal_confirmation_status": "requested",
            "goal_pending_confirmation_criteria": list(normalized.get("criterion_ids") or []),
            "goal_progress": progress,
            "model_turn_count": calls_made,
            "goal_action_validation_repair_limit": repair_limit,
            "goal_action_validation_repairs": int(state.get("goal_action_validation_repairs") or 0) + validation_repairs,
        }
    return {
        "goal_action": normalized,
        "goal_action_status": "selected",
        "goal_current_action_id": str(normalized.get("action_id")),
        "goal_status": "running",
        "goal_progress": progress,
        "model_turn_count": calls_made,
        "goal_action_validation_repair_limit": repair_limit,
        "goal_action_validation_repairs": int(state.get("goal_action_validation_repairs") or 0) + validation_repairs,
    }


def _after_action_select(state: Mapping[str, Any]) -> str:
    status = str(state.get("goal_status") or "")
    if status in {"failed", "blocked"}:
        return "goal_finalize"
    action = state.get("goal_action") if isinstance(state.get("goal_action"), Mapping) else {}
    if status == "waiting_for_user" or str(action.get("kind") or "") == "ask_user":
        return "goal_confirm"
    if str(action.get("kind") or "") == "tool":
        return "goal_execute"
    return "goal_observe"


def _after_execute(state: Mapping[str, Any]) -> str:
    if str(state.get("goal_status") or "") in {"blocked", "failed"}:
        return "goal_finalize"
    return "goal_observe"


async def _goal_execute(state: GoalState, runtime: Any) -> dict[str, Any]:
    context: GoalContext = runtime.context
    raw_action = state.get("goal_action")
    if not isinstance(raw_action, Mapping) or str(raw_action.get("kind") or "") != "tool":
        return {"goal_last_observation": {"success": True, "kind": "finish"}}
    action = dict(raw_action)
    tool_name = _short(action.get("tool_name"), 128)
    action_id = _short(action.get("action_id"), 128)
    if (
        user_requests_knowledge_base_only(state.get("user_text"))
        and tool_name != "search_knowledge_base"
    ):
        detail = "用户明确限定只依据所选 PDF；本轮不得调用其他工具。"
        context.events.stage(
            "goal.execute",
            "blocked",
            "用户限定的 PDF 来源范围已生效，其他工具未执行",
            action_id=action_id or None,
            error_code="knowledge_base_only_restriction",
            details={"kind": "goal_execute", "phase": "goal_execute", "tool_name": tool_name},
        )
        return {
            "goal_last_observation": {
                "success": False,
                "error_code": "knowledge_base_only_restriction",
                "error": detail,
                "tool_name": tool_name,
                "action_id": action_id,
            },
            "goal_action_status": "blocked",
            "goal_status": "blocked",
            "status": "blocked",
            "error_code": "knowledge_base_only_restriction",
            "goal_blocker": detail,
            "goal_terminal_reason": "用户限定的来源范围不允许该工具",
            "terminal_detail": detail,
        }
    criterion_issue = _validate_goal_action_criteria(action, state)
    if criterion_issue is not None:
        detail = _short(criterion_issue.get("error"), 1_000)
        receipt = emit_runtime_error(
            context.events,
            ValueError(detail),
            summary="Goal 工具动作没有有效的完成条件关联，未执行",
            error_code="goal_action_invalid",
            failure_kind="tool_validation",
            retryable=False,
            terminal_impact="terminal",
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            node="goal_execute",
            phase="goal_execute",
            tool_name=tool_name,
            action_id=action_id,
            details=criterion_issue,
        )
        context.events.stage(
            "goal.execute",
            "failed",
            "Goal 工具动作未关联有效的完成条件，未执行",
            action_id=action_id or None,
            error_code="goal_action_invalid",
            details={"kind": "goal_execute", "phase": "goal_execute", **criterion_issue},
        )
        return {
            "goal_last_observation": {
                "success": False,
                "error_code": "goal_action_invalid",
                "error": detail,
                "tool_name": tool_name,
                "action_id": action_id,
            },
            "goal_action_status": "failed",
            "goal_status": "failed",
            "status": "failed",
            "error_code": "goal_action_invalid",
            "goal_blocker": detail,
            "goal_terminal_reason": "工具动作未关联有效的完成条件",
            "terminal_detail": detail,
            "runtime_errors": [receipt] if isinstance(receipt, Mapping) else [],
        }
    if not tool_name or context.registry.get_tool(tool_name) is None:
        detail = f"未注册的 Goal 工具 operation：{tool_name or '空工具名'}"
        receipt = emit_runtime_error(
            context.events,
            ValueError(detail),
            summary="Goal 选中的工具 operation 不存在，未执行",
            error_code="goal_unknown_tool",
            failure_kind="tool_validation",
            retryable=False,
            terminal_impact="terminal",
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            node="goal_execute",
            phase="goal_execute",
            tool_name=tool_name,
            action_id=action_id,
        )
        context.events.stage(
            "goal.execute",
            "failed",
            "Goal 选中的工具 operation 不存在，未执行",
            action_id=action_id or None,
            error_code="goal_unknown_tool",
            details={"kind": "goal_execute", "phase": "goal_execute", "tool_name": tool_name},
        )
        return {
            "goal_last_observation": {
                "success": False,
                "error_code": "goal_unknown_tool",
                "error": detail,
                "tool_name": tool_name,
                "action_id": action_id,
            },
            "goal_action_status": "failed",
            "goal_status": "failed",
            "status": "failed",
            "error_code": "goal_unknown_tool",
            "goal_blocker": detail,
            "goal_terminal_reason": "选择了未注册的工具 operation",
            "terminal_detail": detail,
            "runtime_errors": [receipt],
        }

    try:
        _validated_arguments, validation_issue = _validate_goal_tool_action(
            action, context.registry, context=context, tool_results=state.get("tool_results") or [],
        )
    except Exception as exc:
        validation_issue = {"error": _safe_validation_detail(exc)}
    if validation_issue is not None:
        detail = _short(validation_issue.get("error"), 1_000)
        receipt = emit_runtime_error(
            context.events,
            ValueError(detail or "Goal 工具参数未通过注册表校验"),
            summary="Goal 执行前的工具参数校验失败，未执行",
            error_code="goal_action_invalid",
            failure_kind="tool_validation",
            retryable=False,
            terminal_impact="terminal",
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            node="goal_execute",
            phase="goal_execute",
            tool_name=tool_name,
            action_id=action_id,
            details={"validation_error": detail},
        )
        context.events.stage(
            "goal.execute",
            "failed",
            "Goal 工具参数未通过校验，未执行",
            action_id=action_id or None,
            error_code="goal_action_invalid",
            details={"kind": "goal_execute", "phase": "goal_execute", "tool_name": tool_name, "error": detail},
        )
        return {
            "goal_last_observation": {
                "success": False,
                "error_code": "goal_action_invalid",
                "error": detail,
                "tool_name": tool_name,
                "action_id": action_id,
            },
            "goal_action_status": "failed",
            "goal_status": "failed",
            "status": "failed",
            "error_code": "goal_action_invalid",
            "goal_blocker": detail,
            "goal_terminal_reason": "工具参数校验失败",
            "terminal_detail": detail,
            "runtime_errors": [receipt],
        }

    approved = False
    with tool_execution_context(
        conversation_id=getattr(context, "conversation_id", None), run_id=getattr(context, "run_id", None),
        tenant_id=getattr(context, "tenant_id", None), owner_id=getattr(context, "owner_id", None),
        knowledge_base_ids=getattr(context, "knowledge_base_ids", ()),
    ):
        effect = context.registry.effect_for(tool_name, action.get("arguments") or {})
    if effect == "side_effect":
        payload = _confirmation_payload(
            state,
            kind="goal_side_effect_confirmation",
            summary=f"Goal 准备执行可能产生外部副作用的动作：{tool_name}，请确认是否执行一次。",
        )
        payload["toolName"] = tool_name
        payload["arguments"] = dict(action.get("arguments") or {})
        decision = interrupt(payload)
        approved = isinstance(decision, Mapping) and str(decision.get("decision") or "").lower() == "approve"
        if not approved:
            context.events.stage(
                "goal.execute",
                "blocked",
                "用户未批准 Goal 的外部副作用动作",
                action_id=action_id,
                error_code="approval_rejected",
                details={"kind": "goal_side_effect_confirmation", "tool_name": tool_name},
            )
            return {
                "goal_last_observation": {
                    "success": False,
                    "error_code": "approval_rejected",
                    "tool_name": tool_name,
                },
                "goal_action_status": "blocked",
                "goal_status": "blocked",
                "status": "blocked",
                "goal_blocker": "用户未批准外部副作用动作。",
                "goal_terminal_reason": "外部动作未获批准",
                "terminal_detail": "Goal 需要执行外部副作用动作，但用户未批准。",
                "pending_interrupt": None,
            }

    context.events.stage(
        "goal.execute",
        "started",
        f"Goal 执行 {tool_name}",
        action_id=action_id,
        details={
            "kind": "goal_execute",
            "phase": "goal_execute",
            "tool_name": tool_name,
            "criterion_ids": list(action.get("criterion_ids") or []),
        },
    )
    try:
        record, evidence = await context.executor.execute(action, approved=approved)
    except Exception as exc:
        detail = _short(f"{type(exc).__name__}: {exc}", 1_000)
        error_code = "goal_action_failed"
        receipt = emit_runtime_error(
            context.events,
            exc,
            summary=f"Goal 动作 {tool_name} 执行失败",
            error_code=error_code,
            failure_kind="tool",
            retryable=False,
            terminal_impact="recoverable",
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            node="goal_execute",
            phase="goal_execute",
            tool_name=tool_name,
            action_id=action_id,
        )
        context.events.stage(
            "goal.execute",
            "failed",
            f"Goal 动作 {tool_name} 执行失败",
            action_id=action_id,
            error_code=error_code,
            details={"kind": "goal_execute", "phase": "goal_execute", "tool_name": tool_name, "error": detail},
        )
        return {
            "goal_last_observation": {
                "success": False,
                "tool_name": tool_name,
                "action_id": action_id,
                "error_code": error_code,
                "error": detail,
            },
            "goal_action_status": "failed",
            "runtime_errors": [receipt] if isinstance(receipt, Mapping) else [],
            "tool_call_count": 1,
        }

    success = isinstance(record, Mapping) and record.get("success") is True
    eligible_evidence = (
        evidence
        if (
            success
            and isinstance(evidence, Mapping)
            and str(evidence.get("action_id") or "") == action_id
            and evidence_record_is_eligible(evidence)
        )
        else None
    )
    linked_criteria = _link_action_evidence_to_criteria(
        _goal_criteria(state),
        action,
        eligible_evidence,
    )
    context.events.stage(
        "goal.execute",
        "completed" if success else "failed",
        f"Goal 动作 {tool_name} {'已完成' if success else '未成功'}",
        action_id=action_id,
        error_code=(str(record.get("error_code") or "goal_tool_failed") if not success and isinstance(record, Mapping) else None),
        details={
            "kind": "goal_execute",
            "phase": "goal_execute",
            "tool_name": tool_name,
            "success": success,
        },
    )
    return {
        "tool_results": [record] if isinstance(record, Mapping) else [],
        "evidence": [evidence] if isinstance(evidence, Mapping) else [],
        "goal_evidence_ids": [
            str(evidence.get("evidence_id") or evidence.get("id"))
        ] if (
            eligible_evidence is not None
            and (evidence.get("evidence_id") or evidence.get("id"))
        ) else [],
        "goal_criteria": linked_criteria,
        "completed_tool_call_ids": [action_id] if success else [],
        "goal_last_observation": record,
        "goal_action_status": "completed" if success else "failed",
        "tool_call_count": 1,
    }


async def _goal_observe(state: GoalState, runtime: Any) -> dict[str, Any]:
    context: GoalContext = runtime.context
    observation = state.get("goal_last_observation")
    context.events.stage(
        "goal.observe",
        "completed",
        "Goal 已记录动作观察结果",
        action_id=str(state.get("goal_current_action_id") or "") or None,
        details={
            "kind": "goal_observe",
            "phase": "goal_observe",
            "success": bool(observation.get("success")) if isinstance(observation, Mapping) else False,
            "evidence_ids": list(state.get("goal_evidence_ids") or [])[-12:],
        },
    )
    iteration = int(state.get("goal_iterations") or 0) + 1
    return {
        "goal_iterations": iteration,
        "goal_history": _record_history(
            state,
            event="goal_observe",
            details={
                "iteration": iteration,
                "action_id": state.get("goal_current_action_id"),
                "success": bool(observation.get("success")) if isinstance(observation, Mapping) else False,
            },
        ),
    }


def _apply_assessment(
    state: Mapping[str, Any],
    assessment: GoalAssessment,
) -> dict[str, Any]:
    contract_criteria = _goal_criteria(state)
    evidence = _goal_evidence(state)
    proposals = {
        str(item.criterion_id): item
        for item in assessment.criteria
    }
    existing = {
        str(item.get("criterion_id")): item
        for item in contract_criteria
        if str(item.get("criterion_id") or "").strip()
    }
    updated: list[dict[str, Any]] = []
    pending_confirmation: list[str] = []
    all_required_satisfied = bool(contract_criteria)
    for criterion_id, criterion in existing.items():
        item = dict(criterion)
        proposal = proposals.get(criterion_id)
        # Only server-linked evidence (created by an action explicitly aimed at
        # this criterion) can be selected by the model as completion evidence.
        linked_ids = {
            str(evidence_id)
            for evidence_id in item.get("evidence_ids") or []
            if str(evidence_id) in evidence
        }
        proposed_ids = [
            str(evidence_id)
            for evidence_id in (proposal.evidence_ids if proposal else [])
            if str(evidence_id) in linked_ids
        ]
        previous_status = str(item.get("status") or "pending")
        previous_evidence_ids = [
            str(value)
            for value in item.get("evidence_ids") or []
            if str(value) in linked_ids
        ]
        if str(item.get("verification_method") or "unknown") == "user_confirmation":
            # A model cannot substitute a data/tool observation for an explicit
            # user decision. Only _goal_confirm writes this satisfied state.
            status = "satisfied" if previous_status == "satisfied" and previous_evidence_ids else "pending"
            evidence_ids = previous_evidence_ids if status == "satisfied" else []
            if bool(item.get("required", True)) and status != "satisfied":
                pending_confirmation.append(criterion_id)
        elif previous_status == "satisfied" and previous_evidence_ids:
            status = "satisfied"
            evidence_ids = previous_evidence_ids
        elif proposal is not None and proposal.status == "satisfied" and proposed_ids:
            status = "satisfied"
            evidence_ids = list(dict.fromkeys(proposed_ids))[:24]
        elif proposal is not None and proposal.status == "failed":
            status = "failed"
            evidence_ids = proposed_ids
        elif proposal is not None and proposal.status == "unverifiable":
            status = "unverifiable"
            evidence_ids = proposed_ids
        else:
            status = "pending"
            # Preserve eligible candidates already linked by the server. A
            # pending model assessment must not erase collected evidence that
            # a later assessment or corrective action may need to reconsider.
            evidence_ids = list(dict.fromkeys([*previous_evidence_ids, *proposed_ids]))[:24]
        item["status"] = status
        item["evidence_ids"] = evidence_ids
        item["explanation"] = _short(proposal.explanation if proposal else item.get("explanation"), 1_000)
        if bool(item.get("required", True)) and status != "satisfied":
            all_required_satisfied = False
        if (
            bool(item.get("required", True))
            and status == "unverifiable"
            and str(item.get("verification_method") or "unknown") == "unknown"
        ):
            pending_confirmation.append(criterion_id)
        updated.append(item)

    assessment_status = str(assessment.status)
    if all_required_satisfied:
        goal_status = "completed"
        generic_status = "completed"
        terminal_reason = "所有必需完成条件均已获得有效证据。"
        blocker = ""
    elif assessment_status == "waiting_for_user" or pending_confirmation:
        goal_status = "waiting_for_user"
        generic_status = "running"
        terminal_reason = ""
        blocker = _short(assessment.blocker or "部分完成条件无法自动验证，需要用户确认。", 1_200)
    elif assessment_status == "blocked":
        goal_status = "blocked"
        generic_status = "blocked"
        terminal_reason = _short(assessment.blocker or "Goal 无法继续推进。", 1_200)
        blocker = terminal_reason
    elif assessment_status == "failed":
        goal_status = "failed"
        generic_status = "failed"
        terminal_reason = _short(assessment.blocker or "Goal 监控失败。", 1_200)
        blocker = terminal_reason
    else:
        goal_status = "replanning" if assessment_status == "replan" else "running"
        generic_status = "running"
        terminal_reason = ""
        blocker = ""

    replan_count = int(state.get("goal_replan_count") or 0)
    if goal_status == "replanning":
        replan_count += 1
        if replan_count > int(state.get("goal_replan_limit") or 0):
            goal_status = "blocked"
            generic_status = "blocked"
            terminal_reason = "Goal 已达到服务端重规划上限。"
            blocker = terminal_reason

    if not all_required_satisfied and int(state.get("goal_iterations") or 0) >= int(state.get("goal_iteration_limit") or 0):
        goal_status = "blocked"
        generic_status = "blocked"
        terminal_reason = "Goal 已达到服务端迭代上限。"
        blocker = terminal_reason
    return {
        "goal_criteria": updated,
        "goal_assessment": assessment.model_dump(mode="json"),
        "goal_status": goal_status,
        "status": generic_status,
        "goal_progress": _short(state.get("goal_progress"), 1_200),
        "goal_blocker": blocker,
        "goal_terminal_reason": terminal_reason,
        "goal_pending_confirmation_criteria": pending_confirmation,
        "goal_confirmation_status": "requested" if pending_confirmation else state.get("goal_confirmation_status") or "confirmed",
        "goal_replan_count": replan_count,
        "goal_history": _record_history(
            state,
            event="goal_monitor",
            details={
                "status": goal_status,
                "criteria": updated,
                "evidence_ids": list(state.get("goal_evidence_ids") or [])[-24:],
            },
        ),
    }


def _assessment_projection_accepted(assessment: GoalAssessment, update: Mapping[str, Any]) -> bool:
    """Do not narrate a model verdict that the evidence/budget gate rejected."""

    expected_status = {"continue": "running", "replan": "replanning"}.get(assessment.status, assessment.status)
    if update.get("goal_status") != expected_status:
        return False
    accepted = {str(item["criterion_id"]): item for item in update.get("goal_criteria") or []}
    return all(
        proposal.criterion_id in accepted
        and proposal.status == accepted[proposal.criterion_id].get("status")
        and set(proposal.evidence_ids).issubset(accepted[proposal.criterion_id].get("evidence_ids") or [])
        for proposal in assessment.criteria
    )


async def _goal_monitor(state: GoalState, runtime: Any) -> dict[str, Any]:
    context: GoalContext = runtime.context
    if _model_call_limit_reached(state):
        return {
            "goal_status": "blocked",
            "status": "blocked",
            "goal_blocker": "已达到 Goal 模型调用上限。",
            "goal_terminal_reason": "Goal 模型调用预算耗尽",
            "terminal_detail": "Goal 已达到服务端模型调用上限，保留已有证据并停止继续调用。",
        }
    context.events.stage(
        "goal.monitor",
        "started",
        "正在根据观察结果检查目标进度",
        action_id=str(state.get("goal_current_action_id") or "") or None,
        details={"kind": "goal_monitor", "phase": "goal_monitor"},
    )
    payload = {
        "contract": _contract(state),
        "criteria": _goal_criteria(state),
        "last_observation": state.get("goal_last_observation"),
        "evidence": _evidence_summary(state),
        "previous_update": _short(state.get("goal_progress"), 1_200),
        "iterations": int(state.get("goal_iterations") or 0),
        "replans": int(state.get("goal_replan_count") or 0),
    }
    try:
        assessment = await _structured_call(
            context,
            GoalAssessment,
            [
                SystemMessage(
                    content=_goal_system_prompt(
                        state,
                        "你是 Goal 模式的进度监控器。逐条检查 success_criteria，只能引用当前 evidence 中存在的 evidence_id。"
                        "只有有有效证据时才能把条件标记为 satisfied；不能因为动作成功或模型自评就宣布完成。"
                        "criteria 中的 evidence_ids 是服务端已关联到该条件的候选证据；同一条证据可以支持多个条件。"
                        "如果一条已关联证据同时包含多个条件所需的信息，请在每个适用条件中都选择该 evidence_id 并标记 satisfied；"
                        "不要仅因条件拆成金额、页码等多个要求，就把同一条完整证据拆成一个满足、另一个 pending。"
                        "如果需要新的动作，返回 continue 或 replan；无法自动验证时返回 waiting_for_user；"
                        "verification_method=user_confirmation 的条件必须保持 pending，直到用户明确确认；若仍未确认，返回 waiting_for_user，"
                        "并在 progress_text 中就该条件向用户提出自然、具体的问题。"
                        "无法安全继续时返回 blocked。progress_text 简短说明刚获得的发现如何影响用户的目标，"
                        "以及仍需核实的关键问题；下一步的动作交给动作选择器向用户说明，不在这里重复预告。"
                        "不要把所有已有数据和未完成项目重新报一遍。若已完成，progress_text 留空，最终结果由下一步统一回答。"
                        "使用自然语言，不要输出隐藏推理、固定套话或未经证据支持的完成结论。只能返回 GoalAssessment。",
                    )
                ),
                HumanMessage(content=_safe_json(payload)),
            ],
            projection_phase="goal_monitor",
            projection_kind="GoalAssessment",
            projection_id=f"{context.run_id}:goal:monitor:{int(state.get('model_turn_count') or 0) + 1}",
            # A monitor proposal is not a user-visible fact until the server
            # accepts it. Never stream an unverified completion claim.
            project_progress=False,
        )
        update = _apply_assessment(state, assessment)
        # A continuing loop has one narration owner: the next action choice
        # joins the accepted observation to the next action in one paragraph.
        # Otherwise monitor and action selection repeat the same transition.
        # Pauses/stops have no next action, so publish their accepted question
        # or limitation here. Completion is narrated only by the final answer.
        if update.get("goal_status") in {"waiting_for_user", "blocked", "failed"} and _assessment_projection_accepted(assessment, update):
            _publish_goal_progress(
                context, assessment, phase="goal_monitor", kind="GoalAssessment",
                projection_id=f"{context.run_id}:goal:monitor:{int(state.get('model_turn_count') or 0) + 1}",
            )
            update["goal_progress"] = safe_projection_text(assessment.progress_text)
    except Exception as exc:
        detail = _short(f"{type(exc).__name__}: {exc}", 1_000)
        context.events.stage(
            "goal.monitor",
            "failed",
            "Goal 进度无法可靠判断，已停止继续执行",
            action_id=str(state.get("goal_current_action_id") or "") or None,
            error_code="goal_monitor_invalid",
            details={"kind": "goal_monitor", "error": detail},
        )
        return {
            "goal_status": "failed",
            "status": "failed",
            "error_code": "goal_monitor_invalid",
            "terminal_detail": "Goal 进度无法可靠判断，已保留已有证据并停止执行。",
            "goal_terminal_reason": "目标监控失败",
            "goal_blocker": detail,
            "model_turn_count": 1,
        }
    context.events.stage(
        "goal.monitor",
        "completed" if update.get("goal_status") == "completed" else "started",
        _short(update.get("goal_progress"), 1_200) or "Goal 进度已更新",
        action_id=str(state.get("goal_current_action_id") or "") or None,
        details={
            "kind": "goal_monitor",
            "phase": "goal_monitor",
            "goal_status": update.get("goal_status"),
            "criteria": update.get("goal_criteria"),
        },
    )
    update["model_turn_count"] = 1
    return update


def _after_monitor(state: Mapping[str, Any]) -> str:
    status = str(state.get("goal_status") or "")
    if status in {"completed", "blocked", "failed"}:
        return "goal_finalize"
    if status == "waiting_for_user":
        return "goal_confirm"
    return "goal_action_select"


async def _goal_finalize(state: GoalState, runtime: Any) -> dict[str, Any]:
    context: GoalContext = runtime.context
    goal_status = str(state.get("goal_status") or "blocked")
    generic_status = (
        "completed" if goal_status == "completed"
        else "failed" if goal_status == "failed"
        else "blocked"
    )
    contract = _contract(state)
    criteria = _goal_criteria(state)
    evidence = _evidence_summary(state)
    fallback = _short(state.get("goal_terminal_reason") or state.get("goal_blocker") or state.get("goal_progress"), 1_600)
    answer = "Goal 未能完成。"
    final_model_calls = 0
    eligible_evidence_ids = set(_goal_evidence(state))
    evidence_ids = [
        str(value)
        for value in state.get("goal_evidence_ids") or []
        if str(value) in eligible_evidence_ids
    ]
    requested_answer_evidence_ids: list[str] = []
    limitations: list[str] = []
    if generic_status != "completed" and fallback:
        limitations.append(fallback)

    if not _model_call_limit_reached(state):
        final_model_calls = 1
        try:
            final = await _structured_call(
                context,
                GoalFinalAnswer,
                [
                    SystemMessage(
                        content=_goal_system_prompt(
                            state,
                            "你是 Goal 模式的结果整理器。根据目标合同、完成条件和已存在的证据，"
                            "输出简洁、诚实的最终结果。不要声称未被证据支持的条件已经完成。"
                            "事实结论要在 evidence_ids 字段中引用实际支持它的证据；只能选择输入中 eligible=true 的 evidence_id，"
                            "不要在 answer 正文里写证据编号，也不要为了凑引用选择无关证据。"
                            "completed Goal 说明已满足的条件；blocked/failed Goal 明确说明未完成条件和限制。"
                            "直接回答用户的问题，承接前面的沟通；不写任务报告、内部状态标题或完成清单。"
                            "运行故障不等于数据缺失；已取得的观察仍然有效，不能抹掉或编造故障原因。"
                            "只能返回 GoalFinalAnswer。",
                        )
                    ),
                    HumanMessage(
                        content=_safe_json(
                            {
                                "status": goal_status,
                                "contract": contract,
                                "criteria": criteria,
                                "evidence": evidence,
                                "last_observation": state.get("goal_last_observation"),
                                "previous_update": state.get("goal_progress"),
                                "fallback_reason": fallback,
                            }
                        )
                    ),
                ],
                projection_phase="goal_finalize",
                projection_kind="GoalFinalAnswer",
                projection_id=f"{context.run_id}:goal:finalize:{int(state.get('model_turn_count') or 0) + 1}",
            )
            answer = str(final.answer or "").strip()
            requested_answer_evidence_ids = [str(value) for value in final.evidence_ids]
            evidence_ids = list(dict.fromkeys([
                *evidence_ids,
                *[
                    str(value)
                    for value in final.evidence_ids
                    if str(value) in eligible_evidence_ids
                ],
            ]))[:80]
            limitations = list(dict.fromkeys([*limitations, *[_short(value, 600) for value in final.limitations]]))[:12]
        except Exception as exc:
            limitations.append("最终结果整理模型未返回有效结构，以下内容基于已记录的 Goal 状态。")
            context.events.stage(
                "goal.finalize",
                "failed",
                "Goal 最终结果整理失败，已使用安全降级结果",
                action_id=f"goal:{context.run_id}:finalize",
                error_code="goal_finalize_invalid",
                details={"kind": "goal_finalize", "error": _short(str(exc), 800)},
            )
    if generic_status == "completed" and answer == "Goal 未能完成。":
        answer = "Goal 已完成，所有必需完成条件均已获得有效证据。"
    if generic_status != "completed" and answer == "Goal 未能完成。" and fallback:
        answer = f"Goal 未能完成：{fallback}"
    answer_evidence_ids = _goal_final_answer_evidence_ids(
        criteria,
        requested_answer_evidence_ids,
        eligible_evidence_ids,
    )
    # The shared answer contract renders citations from validated evidence IDs.
    # Remove any free-form model markers first so only the typed, run-local IDs
    # below can become clickable references in the client.
    answer, _ = canonicalize_evidence_markers(answer, ())
    if answer_evidence_ids:
        evidence_ids = list(dict.fromkeys([*evidence_ids, *answer_evidence_ids]))[:80]
        answer = answer.rstrip() + " " + " ".join(
            f"【证据 {evidence_id}】" for evidence_id in answer_evidence_ids
        )
    remaining_limitations = [item for item in dict.fromkeys(limitations) if item and item not in answer]
    if remaining_limitations:
        answer = answer.rstrip() + "\n\n限制：" + "；".join(remaining_limitations)
    final_text = finalize_terminal_answer(
        answer,
        status=generic_status,
        error_code=(str(state.get("error_code")) if state.get("error_code") else None),
        detail=fallback if generic_status != "completed" else None,
    )
    factual_evidence = [
        item
        for item in state.get("evidence") or []
        if isinstance(item, Mapping)
        and evidence_record_is_eligible(item)
        and str(item.get("effect") or "read") != "side_effect"
    ]
    claim_evidence = (
        build_claim_evidence_ledger(
            final_text,
            factual_evidence,
            [
                item
                for item in state.get("tool_results") or []
                if isinstance(item, Mapping)
            ],
        ).get("claims")
        if factual_evidence
        else []
    )
    context.events.stage(
        "goal.finalize",
        "completed" if generic_status == "completed" else generic_status,
        "Goal 已生成最终结果" if generic_status == "completed" else "Goal 已进入终态",
        action_id=f"goal:{context.run_id}:finalize",
        details={
            "kind": "goal_finalize",
            "phase": "goal_finalize",
            "goal_status": goal_status,
            "evidence_ids": evidence_ids[:24],
            "criteria": criteria,
        },
    )
    context.events.text(final_text)
    return {
        "status": generic_status,
        "answer_final": final_text,
        "goal_status": goal_status,
        "goal_terminal_reason": fallback if generic_status != "completed" else "所有必需完成条件均已满足。",
        "goal_evidence_ids": evidence_ids,
        "claim_evidence": list(claim_evidence or []),
        "pending_interrupt": None,
        "model_turn_count": final_model_calls,
    }


def build_goal_graph(*, checkpointer: Any) -> Any:
    """Compile the standalone Goal graph with its own state schema."""

    builder = StateGraph(GoalState, context_schema=GoalContext)
    builder.add_node("goal_intake", _goal_intake)
    builder.add_node("goal_confirm", _goal_confirm)
    builder.add_node("goal_action_select", _goal_action_select)
    builder.add_node("goal_execute", _goal_execute)
    builder.add_node("goal_observe", _goal_observe)
    builder.add_node("goal_monitor", _goal_monitor)
    builder.add_node("goal_finalize", _goal_finalize)
    builder.add_edge(START, "goal_intake")
    builder.add_conditional_edges("goal_intake", _after_intake)
    builder.add_conditional_edges("goal_confirm", _after_confirm)
    builder.add_conditional_edges("goal_action_select", _after_action_select)
    builder.add_conditional_edges("goal_execute", _after_execute)
    builder.add_edge("goal_observe", "goal_monitor")
    builder.add_conditional_edges("goal_monitor", _after_monitor)
    builder.add_edge("goal_finalize", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "GOAL_DEFAULT_ITERATION_LIMIT",
    "GOAL_DEFAULT_MODEL_CALL_LIMIT",
    "GOAL_DEFAULT_REPLAN_LIMIT",
    "build_goal_graph",
    "goal_runtime_limits",
    "goal_turn_defaults",
]
