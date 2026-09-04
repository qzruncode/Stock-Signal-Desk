"""Operational middleware around LangChain's standard message/tool loop.

The model decides *whether* to call an operation and what to do after each
observation.  This module owns only universal runtime concerns: prompt
context, source/evidence visibility, budgets, approval, atomic execution,
idempotency handoff, and terminal publication.  It contains no business SOP,
intent classifier, tool ranking, or precompiled action DAG.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping, Sequence

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langgraph.types import Command, interrupt

from .agent_tools import NATIVE_TOOL_RESULT_MARKER, native_tool_context
from .content_access import (
    DEFAULT_CONTENT_ACCESS_REPAIR_LIMIT,
    build_content_access_targets,
    canonical_url,
    cited_reference_action_ids,
    content_read_call_urls,
    required_content_access_targets,
    successful_content_read_urls,
)
from .claim_evidence import build_claim_evidence_ledger
from .events import redact_arguments
from .executor import action_fingerprint
from .evidence_identity import canonicalize_evidence_markers
from .presentation import project_arguments_for_timeline, project_tool_result_for_timeline
from .state import AgentState, GraphContext


def _last_ai_message(messages: Sequence[BaseMessage]) -> AIMessage | None:
    return next((message for message in reversed(messages) if isinstance(message, AIMessage)), None)


def _message_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        return "".join(parts).strip()
    return str(content or "").strip()


def _serialized_character_count(value: Any) -> int:
    """Return a cheap prompt-footprint metric without changing the payload."""
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError):
        return len(str(value))


def _message_character_count(messages: Sequence[BaseMessage]) -> int:
    total = 0
    for message in messages:
        total += _serialized_character_count(getattr(message, "content", ""))
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            total += _serialized_character_count(tool_calls)
    return total


def _claim_evidence_for_partial_answer(
    state: AgentState,
    answer: str,
) -> list[dict[str, Any]]:
    """Keep the evidence ledger populated when a guard publishes a partial answer.

    Content-access and budget guards can terminate the normal ``_check_answer``
    path before the claim ledger is built.  The answer still contains the
    model's evidence markers, so a partial run must retain the same mechanical
    claim-to-evidence projection for the Run Explorer audit.
    """
    factual_evidence = [
        item
        for item in state.get("evidence") or []
        if isinstance(item, Mapping)
        and item.get("success") is True
        and str(item.get("effect") or "read") != "side_effect"
    ]
    if not factual_evidence:
        return []
    normalized_answer, _ = canonicalize_evidence_markers(answer, factual_evidence)
    ledger = build_claim_evidence_ledger(
        normalized_answer,
        factual_evidence,
        [
            item
            for item in state.get("tool_results") or []
            if isinstance(item, Mapping)
        ],
    )
    return list(ledger.get("claims") or [])


def _bounded(value: Any, *, depth: int = 0) -> Any:
    """Make a useful ToolMessage observation without inflating the next prompt."""
    if depth >= 4:
        return "[内容已截断]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:1_200] + ("…" if len(value) > 1_200 else "")
    if isinstance(value, Mapping):
        return {
            str(key)[:120]: _bounded(item, depth=depth + 1)
            for key, item in list(value.items())[:24]
            if str(key) not in {"_fetched_at", "retrieved_at", "completed_at"}
        }
    if isinstance(value, (list, tuple)):
        result = [_bounded(item, depth=depth + 1) for item in list(value)[:16]]
        if len(value) > 16:
            result.append({"omitted_item_count": len(value) - 16})
        return result
    return str(value)[:1_200]


def _tool_result_observation(record: Mapping[str, Any], result: Mapping[str, Any]) -> Any:
    """Keep a usable body preview for the next model turn.

    Generic tool observations stay small, but a 1,200-character slice is too
    short for the model to use a fetched article or a MarkItDown PDF result.
    The complete result remains in the checkpoint and can be loaded by the
    Run Explorer; this only enlarges the immediate reader observation.
    """
    projected = _bounded(result)
    if str(record.get("tool_name") or "") != "read_web_source" or not isinstance(projected, Mapping):
        return projected
    content = str(result.get("content") or "")
    if len(content) <= 1_200:
        return projected
    projected = dict(projected)
    projected["content"] = content[:12_000] + ("…[正文预览已截断]" if len(content) > 12_000 else "")
    projected["content_preview_length"] = len(content)
    return projected


def _tool_message_content(record: Mapping[str, Any], evidence: Mapping[str, Any] | None) -> str:
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    tool_name = str(record.get("tool_name") or "")
    result_count = result.get("result_count")
    empty_result = (
        result_count == 0
        or (isinstance(result.get("items"), Sequence) and not result.get("items"))
        or (isinstance(result.get("results"), Sequence) and not result.get("results"))
    )
    stale = result.get("is_stale") is True or record.get("is_stale") is True
    freshness_unknown = bool(
        result.get("freshness_unknown")
        or record.get("freshness_unknown")
    )
    failed = record.get("success") is not True or result.get("success") is False
    fallback_recommended = bool(result.get("fallback_recommended"))
    observation_status = (
        "failed"
        if failed
        else "stale"
        if stale
        else "empty"
        if empty_result
        else "freshness_unknown"
        if freshness_unknown
        else "ok"
    )
    payload = {
        "success": record.get("success") is True,
        "partial": bool(record.get("partial")),
        "tool": tool_name,
        "evidence_id": str((evidence or {}).get("evidence_id") or "") or None,
        "data_time": record.get("data_time"),
        "data_time_provenance": record.get("data_time_provenance"),
        "is_stale": record.get("is_stale", result.get("is_stale")),
        "freshness_unknown": freshness_unknown,
        "observation_status": observation_status,
        "source_refs": [str(item)[:500] for item in list(record.get("source_refs") or [])[:8]],
        "errors": [str(item)[:800] for item in list(record.get("errors") or [])[:6]],
        "result": _tool_result_observation(record, result),
    }
    if failed or stale or empty_result or freshness_unknown or fallback_recommended:
        if tool_name == "search_web_source":
            next_action = (
                "若用户需要最新资料，请改写查询或读取本次返回的相关 URL；"
                "不要把本次空、失败、过期或时间未知的搜索结果表述为最新事实。"
            )
        elif tool_name == "read_web_source":
            next_action = (
                "若正文读取失败或不可用，请按错误类型重试 source_id=auto，"
                "或换一个可访问的相关 URL；不要声称已经读取正文。"
            )
        else:
            next_action = (
                "如果用户问题要求最新外部事实，请显式调用 search_web_source(source_id=auto) 获取网页来源；"
                "若已有相关 URL，再调用 read_web_source(source_id=auto)。"
                "保留本次失败/空/过期观察，并禁止把它当作最新事实。"
            )
        payload["next_action"] = next_action
    return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def _conversation_context_from_result(result: Any) -> dict[str, Any] | None:
    """Accept a small server-owned context reference from a tool result."""
    if not isinstance(result, Mapping):
        return None
    candidate = result.get("_agent_context")
    if not isinstance(candidate, Mapping):
        return None
    context_type = str(candidate.get("type") or "").strip()
    if not context_type:
        return None
    context: dict[str, Any] = {"type": context_type}
    for key in ("group_id", "group_name", "member_count", "source"):
        value = candidate.get(key)
        if value not in (None, ""):
            context[key] = value
    return context


def _error_tool_message(
    *,
    tool_call_id: str,
    tool_name: str,
    message: str,
) -> ToolMessage:
    return ToolMessage(
        content=message,
        name=tool_name,
        tool_call_id=tool_call_id,
        status="error",
    )


def _native_tool_result(response: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Decode the envelope returned by a native LangGraph tool handler."""
    content = getattr(response, "content", None)
    payload: Any = content
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("native tool handler returned invalid JSON") from exc
    if not isinstance(payload, Mapping) or payload.get(NATIVE_TOOL_RESULT_MARKER) is not True:
        raise RuntimeError("native tool handler returned no application result envelope")
    record = payload.get("record")
    if not isinstance(record, Mapping):
        raise RuntimeError("native tool handler returned an invalid result record")
    evidence = payload.get("evidence")
    return dict(record), dict(evidence) if isinstance(evidence, Mapping) else None


def _failed_record(
    *,
    tool_call_id: str,
    tool_name: str,
    arguments: Mapping[str, Any],
    error_code: str,
    message: str,
    effect: str = "read",
    sensitive_fields: Sequence[str] = (),
    server_controlled_fields: Sequence[str] = ("confirmed",),
) -> dict[str, Any]:
    display_arguments = project_arguments_for_timeline(
        arguments,
        sensitive_fields=sensitive_fields,
        server_controlled_fields=server_controlled_fields,
    )
    result = {"success": False, "errors": [message], "error_code": error_code}
    return {
        "id": tool_call_id,
        "action_id": tool_call_id,
        "tool_name": tool_name,
        "model_tool_call_id": tool_call_id,
        "effect": effect,
        "arguments": dict(arguments),
        "display_arguments": display_arguments,
        "success": False,
        "partial": False,
        "errors": [message],
        "error_code": error_code,
        "data_time": None,
        "data_time_provenance": "unavailable",
        "freshness_unknown": True,
        "is_stale": None,
        "source_refs": [],
        "result": result,
        "display_result": project_tool_result_for_timeline(result),
    }


class AgentPromptMiddleware(AgentMiddleware[AgentState, GraphContext]):
    """Provide one complete operational/source context to each model turn."""

    name = "agent_prompt"

    async def awrap_model_call(
        self,
        request: ModelRequest[GraphContext],
        handler: Any,
    ) -> ModelResponse | ExtendedModelResponse:
        context = request.runtime.context
        state = request.state
        evidence = [item for item in state.get("evidence") or [] if isinstance(item, Mapping)]
        observations = [item for item in state.get("tool_results") or [] if isinstance(item, Mapping)]
        feedback = str(state.get("evidence_feedback") or "").strip()
        content_feedback = str(state.get("content_access_feedback") or "").strip()
        content_targets, pending_content_reads = build_content_access_targets(
            tool_results=state.get("tool_results") or [],
            existing_targets=state.get("content_access_targets") or [],
        )
        target_urls = {canonical_url(item.get("url")) for item in content_targets}
        successful_target_reads = successful_content_read_urls(state.get("tool_results") or []) & target_urls
        conversation_context = state.get("conversation_context")
        model_turn = max(0, int(state.get("model_turn_count") or 0)) + 1
        context.events.begin_model_turn(model_turn)
        source_catalog = context.catalog.model_context()
        base_prompt = str(state.get("system_prompt") or request.system_prompt or "").strip()
        prompt_parts = [
            part
            for part in (
                base_prompt,
                """你是通用的证据驱动助手，只围绕本轮用户问题工作，不套用预设行业流程。

所有 operation schema 已直接绑定到本次模型调用，是名称、参数和 source_id 的唯一权威。工具是可选的；需要外部事实、实时数据或用户明确要求检索时才调用。

对需要多步取证的问题：第一次工具调用前，用一小段简洁的用户可见文字说明目标、准备做的步骤和下一步；每轮工具返回后，先简洁总结已完成的工作，再说明下一步。不要输出隐藏的 chain-of-thought，只输出可供用户理解的计划、阶段总结和行动说明。

外部事实必须紧邻标注本轮成功的【证据 ev_...】；只能使用证据里的 data_time，不能把检索时间当成数据时间。没有可用 data_time 时，不要称为“最新/当前/今日”，应继续取证或明确时效未知。失败、空结果、过期结果和 freshness_unknown 都必须如实读取：若用户需要最新事实，改用 search_web_source(source_id=auto)；已有 URL 则用 read_web_source(source_id=auto)。不要把失败或过期观察当成最新事实。

reference-only 结果只是标题、摘要或来源索引，不是正文。只有在确实需要文章/PDF内容时，选择相关 URL 调用 read_web_source；不要为了满足规则读取全部候选链接。没有正文时只能按索引事实表述，并明确正文未读取。表格或连续列表可由紧随其后的来源行统一引用，不要因引用位置而删除已核实内容。""",
                (
                    "最近一次已解析的会话数据上下文（仅在本轮问题继续引用时使用）：\n"
                    + json.dumps(conversation_context, ensure_ascii=False, default=str)
                    if isinstance(conversation_context, Mapping)
                    else ""
                ),
                (
                    "待处理的正文读取反馈：\n" + content_feedback
                    if content_feedback
                    else ""
                ),
                (
                    "可选的参考来源候选（只选择与当前问题相关的 URL，不要求全部读取）：\n"
                    + json.dumps(
                        [
                            {
                                key: item.get(key)
                                for key in ("url", "title", "kind", "tool_name", "action_id")
                                if item.get(key) not in (None, "")
                            }
                            for item in content_targets[:24]
                        ],
                        ensure_ascii=False,
                        default=str,
                    )
                    + (f"\n其余候选数量：{len(content_targets) - 24}" if len(content_targets) > 24 else "")
                    if content_targets and (content_feedback or pending_content_reads or not successful_target_reads)
                    else ""
                ),
                (
                    "系统证据检查反馈：\n"
                    + feedback
                    + "\n请只返回修订后的最终答案，保留已证实内容并删除、弱化或明确标注无法关联证据的结论。"
                    if feedback
                    else ""
                ),
            )
            if part
        ]
        system_prompt = "\n\n".join(prompt_parts)
        context.events.stage(
            "model",
            "started",
            f"第 {model_turn} 轮：模型正在基于当前问题、工具观察和证据决定下一步",
            details={
                "model_turn": model_turn,
                "operation_count": context.catalog.size,
                "bound_tool_count": len(getattr(request, "tools", ()) or ()),
                "directory_character_count": len(source_catalog),
                "system_prompt_character_count": len(system_prompt),
                "message_character_count": _message_character_count(state.get("messages") or []),
                "evidence_count": len(evidence),
                "evidence_character_count": _serialized_character_count(evidence),
                "prior_tool_observation_count": len(observations),
                "observation_character_count": _serialized_character_count(observations),
                "has_evidence_feedback": bool(feedback),
                "content_access_candidate_count": len(content_targets),
                "content_access_successful_count": len(successful_target_reads),
                "pending_content_read_count": len(pending_content_reads),
                "has_content_access_feedback": bool(content_feedback),
            },
        )
        response = await handler(
            request.override(
                model=context.model,
                system_message=SystemMessage(content=system_prompt),
            )
        )
        last = _last_ai_message(response.result)
        if last is not None and last.tool_calls:
            operations: list[dict[str, Any]] = []
            for call in last.tool_calls:
                tool_name = str(call.get("name") or "")
                arguments = dict(call.get("args") or {})
                spec = context.registry.get_tool(tool_name)
                operations.append(
                    {
                        "tool_name": tool_name,
                        "arguments": project_arguments_for_timeline(
                            arguments,
                            sensitive_fields=(spec.sensitive_fields if spec else ()),
                            server_controlled_fields=(
                                spec.server_controlled_fields if spec else ("confirmed",)
                            ),
                        ),
                        "argument_keys": sorted(str(key) for key in arguments),
                    }
                )
            context.events.stage(
                "model",
                "completed",
                f"第 {model_turn} 轮：模型请求 {len(last.tool_calls)} 个原子操作",
                details={
                    "model_turn": model_turn,
                    "operations": operations,
                    "progress_preview": _message_text(last)[:1_200] if _message_text(last) else None,
                },
            )
        else:
            answer = _message_text(last) if last else ""
            preview_limit = 2_400
            context.events.stage(
                "model",
                "completed",
                f"第 {model_turn} 轮：模型已给出候选回答，正在检查其证据关联",
                details={
                    "model_turn": model_turn,
                    "answer_preview": answer[:preview_limit],
                    "answer_character_count": len(answer),
                    "answer_preview_truncated": len(answer) > preview_limit,
                },
            )
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"model_turn_count": 1}),
        )


class OperationPolicyMiddleware(AgentMiddleware[AgentState, GraphContext]):
    """Enforce budgets, approval, and selected source-body access before finalization."""

    name = "operation_policy"

    @hook_config(can_jump_to=["model"])
    async def aafter_model(
        self,
        state: AgentState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        context: GraphContext = runtime.context
        last = _last_ai_message(state.get("messages") or [])
        if last is None:
            return None
        if not last.tool_calls:
            content_access_update, blocked = self._content_access_gate(state, context, last)
            if blocked:
                return content_access_update
            return {**content_access_update, **self._check_answer(state, context, last)}

        used = max(0, int(state.get("tool_call_count") or 0))
        remaining = max(0, int(state.get("tool_call_limit") or 0) - used)
        artificial_messages: list[ToolMessage] = []
        rejected: list[str] = []
        allowed_side_effect: dict[str, Any] | None = None
        allowed_calls = 0
        budget_exhausted = False

        for raw_call in last.tool_calls:
            call = dict(raw_call)
            call_id = str(call.get("id") or "").strip()
            tool_name = str(call.get("name") or "").strip()
            arguments = dict(call.get("args") or {}) if isinstance(call.get("args"), Mapping) else {}
            if not call_id or not tool_name:
                continue
            if allowed_calls >= remaining:
                budget_exhausted = True
                artificial_messages.append(
                    _error_tool_message(
                        tool_call_id=call_id,
                        tool_name=tool_name,
                        message=(
                            "本轮工具调用预算已用尽；请基于已经返回的观察收束回答，"
                            "并明确说明仍缺少的证据。"
                        ),
                    )
                )
                rejected.append(call_id)
                continue
            spec = context.registry.get_tool(tool_name)
            if spec is None:
                artificial_messages.append(
                    _error_tool_message(
                        tool_call_id=call_id,
                        tool_name=tool_name,
                        message="该 operation 不在当前完整目录中；请选择已绑定的 operation。",
                    )
                )
                rejected.append(call_id)
                continue
            try:
                effect = context.registry.effect_for(tool_name, arguments)
            except Exception as exc:
                artificial_messages.append(
                    _error_tool_message(
                        tool_call_id=call_id,
                        tool_name=tool_name,
                        message=f"无法解析该 operation 的执行策略：{type(exc).__name__}: {exc}",
                    )
                )
                rejected.append(call_id)
                continue
            allowed_calls += 1
            if effect != "side_effect":
                continue
            if allowed_side_effect is None:
                allowed_side_effect = {"id": call_id, "name": tool_name, "args": arguments, "spec": spec}
                continue
            artificial_messages.append(
                _error_tool_message(
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    message=(
                        "副作用 operation 会严格串行执行；当前只保留本轮第一个待审批操作。"
                        "请在收到其结果后再决定是否需要下一项。"
                    ),
                )
            )

        updates: dict[str, Any] = {}
        if artificial_messages:
            updates["messages"] = artificial_messages
        if rejected:
            updates["rejected_tool_call_ids"] = rejected
        if budget_exhausted:
            updates["work_budget_exhausted"] = True
            updates["work_budget_detail"] = (
                "模型请求了额外工具，但本轮工具调用预算已用尽；"
                "最终回答只能基于已返回的观察，并必须明确缺口。"
            )

        if allowed_side_effect is None:
            return updates or None

        action_id = str(allowed_side_effect["id"])
        tool_name = str(allowed_side_effect["name"])
        arguments = dict(allowed_side_effect["args"])
        spec = allowed_side_effect["spec"]
        fingerprint = action_fingerprint(
            run_id=context.run_id,
            action_id=action_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        payload = {
            "run_id": context.run_id,
            "conversation_id": context.conversation_id,
            "fingerprint": fingerprint,
            "action_id": action_id,
            "tool_name": tool_name,
            "summary": str(spec.description or f"执行 {tool_name}")[:600],
            "arguments": redact_arguments(arguments, sensitive_fields=spec.sensitive_fields),
        }
        context.events.stage(
            "approval",
            "started",
            f"{tool_name} 会产生外部副作用，正在等待用户批准",
            action_id=action_id,
            tool_call_id=action_id,
            details={"tool_name": tool_name, "arguments": payload["arguments"]},
        )
        decision = interrupt(payload)
        if not isinstance(decision, Mapping):
            raise ValueError("approval resume payload must be an object")
        if str(decision.get("fingerprint") or "") != fingerprint:
            raise ValueError("approval fingerprint mismatch")
        selected = str(decision.get("decision") or "").strip().lower()
        if selected == "approve":
            context.events.stage(
                "approval",
                "completed",
                f"用户已批准 {tool_name}，将串行执行一次",
                action_id=action_id,
                tool_call_id=action_id,
            )
            updates.update(
                {
                    "approved_tool_call_ids": [action_id],
                    "pending_interrupt": None,
                }
            )
            return updates
        if selected == "reject":
            context.events.stage(
                "approval",
                "completed",
                f"用户拒绝执行 {tool_name}，该操作没有被调用",
                action_id=action_id,
                tool_call_id=action_id,
            )
            rejection = _failed_record(
                tool_call_id=action_id,
                tool_name=tool_name,
                arguments=arguments,
                effect="side_effect",
                error_code="approval_rejected",
                message="用户拒绝了该副作用操作；不要在没有新授权的情况下重试。",
                sensitive_fields=spec.sensitive_fields,
                server_controlled_fields=spec.server_controlled_fields,
            )
            updates["messages"] = [
                *artificial_messages,
                _error_tool_message(
                    tool_call_id=action_id,
                    tool_name=tool_name,
                    message="用户拒绝了该副作用操作；没有执行。请基于现有信息重新决定下一步。",
                ),
            ]
            updates["tool_results"] = [rejection]
            updates["rejected_tool_call_ids"] = [*rejected, action_id]
            updates["pending_interrupt"] = None
            return updates
        raise ValueError("approval decision must be approve or reject")

    @staticmethod
    def _content_access_partial(
        *,
        state: AgentState,
        context: GraphContext,
        message: AIMessage,
        targets: list[dict[str, Any]],
        pending: list[dict[str, Any]],
        required: list[dict[str, Any]],
        reason: str,
        error_code: str,
    ) -> dict[str, Any]:
        answer = _message_text(message).rstrip()
        factual_evidence = [
            item
            for item in state.get("evidence") or []
            if isinstance(item, Mapping)
            and item.get("success") is True
            and str(item.get("effect") or "read") != "side_effect"
        ]
        answer, _ = canonicalize_evidence_markers(answer, factual_evidence)
        if not answer:
            answer = "已获取来源索引，但本轮未完成正文读取。"
        claim_evidence = _claim_evidence_for_partial_answer(state, answer)
        suffix = (
            "\n\n[正文取证未完成："
            + reason
            + "以上结论仅基于来源链接、标题/摘要或结构化字段，正文未核验。]"
        )
        context.events.stage(
            "content_access",
            "failed",
            "正文读取未完成，已阻止无正文核验的完整回答",
            error_code=error_code,
            details={
                "target_count": len(targets),
                "required_count": len(required),
                "pending_count": len(pending),
                "pending_urls": [str(item.get("url") or "") for item in pending],
                "reason": reason,
            },
        )
        context.events.commit_model_answer(answer + suffix)
        return {
            "content_access_targets": targets,
            "required_content_reads": required,
            "pending_content_reads": pending,
            "content_access_feedback": "",
            "claim_evidence": claim_evidence,
            "answer_draft": answer,
            "answer_final": answer + suffix,
            "status": "partial",
            "error_code": error_code,
        }

    @classmethod
    def _content_access_gate(
        cls,
        state: AgentState,
        context: GraphContext,
        message: AIMessage,
    ) -> tuple[dict[str, Any], bool]:
        """Keep reference-only results from silently becoming body-backed facts."""
        targets, pending = build_content_access_targets(
            tool_results=state.get("tool_results") or [],
            existing_targets=state.get("content_access_targets") or [],
        )
        answer = _message_text(message)
        required = required_content_access_targets(
            answer=answer,
            evidence=state.get("evidence") or [],
            tool_results=state.get("tool_results") or [],
            targets=targets,
        )
        base_update = {
            "content_access_targets": targets,
            "required_content_reads": required,
            "pending_content_reads": pending,
            "content_access_feedback": "",
        }
        if not targets:
            return {}, False
        target_urls = {canonical_url(item.get("url")) for item in targets}
        selected_target_reads = content_read_call_urls(state.get("tool_results") or []) & target_urls
        successful_target_reads = successful_content_read_urls(state.get("tool_results") or []) & target_urls
        successful_required_reads = successful_target_reads & {
            canonical_url(item.get("url")) for item in required
        }
        required_pending = [
            item
            for item in required
            if canonical_url(item.get("url")) not in successful_target_reads
        ]
        pending_urls = {
            canonical_url(item.get("url"))
            for item in required_pending
        }
        pending = [
            *required_pending,
            *[
                item
                for item in pending
                if canonical_url(item.get("url")) not in pending_urls
            ],
        ]
        cited_reference_actions = cited_reference_action_ids(
            answer=answer,
            evidence=state.get("evidence") or [],
            tool_results=state.get("tool_results") or [],
        )
        if not pending:
            if successful_target_reads or required:
                context.events.stage(
                    "content_access",
                    "completed",
                    "已完成模型选定正文来源的读取核对，允许进入最终回答检查",
                    details={
                        "candidate_count": len(targets),
                        "required_count": len(required),
                        "required_successful_count": len(successful_required_reads),
                        "selected_count": len(selected_target_reads),
                        "successful_count": len(successful_target_reads),
                        "pending_count": 0,
                    },
                )
                return base_update, False
            # A reference-only result may contain many candidates.  If the
            # answer cites it without selecting a body URL, ask the model to
            # choose the relevant page; never manufacture an all-links read
            # fan-out.  This keeps the body contract strict for cited claims
            # without turning unrelated candidates into false gaps.
            if not cited_reference_actions:
                return base_update, False
            feedback = (
                "当前答案引用了 reference-only 来源，但还没有选择要核验的正文 URL。"
                "请只选择与当前问题直接相关的 URL，调用 read_web_source（通常使用 source_id=auto）；"
                "不要求读取未选择的候选链接。若只需来源索引，请明确按标题/摘要或索引信息表述。\n"
                "候选来源：\n"
                + json.dumps(targets[:24], ensure_ascii=False, default=str)
                + (f"\n其余候选数量：{len(targets) - 24}" if len(targets) > 24 else "")
            )
        if required_pending:
            feedback = (
                "当前不能结束回答。你已选定正文来源，但该 URL 尚未成功提取非空正文。"
                "请重试 read_web_source 或改选一个相关 URL；不要求读取未选择的候选链接。\n"
                + json.dumps(required_pending, ensure_ascii=False, default=str)
            )
        else:
            feedback = (
                "当前不能结束回答。你已经选择了部分来源，但以下已选择 URL 尚未成功提取非空正文。"
                "请重试 read_web_source 或选择合适的来源读取器；未选择的候选来源不需要读取。\n"
                "待完成的已选择来源：\n"
                + json.dumps(pending, ensure_ascii=False, default=str)
            )
        if bool(state.get("work_budget_exhausted")):
            return (
                cls._content_access_partial(
                    state=state,
                    context=context,
                    message=message,
                    targets=targets,
                    pending=pending,
                    required=required,
                    reason="工具调用预算已用尽；未成功读取的候选来源只能作为标题/摘要或结构化字段，",
                    error_code="content_access_budget_exceeded",
                ),
                True,
            )

        repair_count = max(0, int(state.get("content_access_repair_count") or 0))
        repair_limit = max(
            0,
            int(
                state.get("content_access_repair_limit")
                if state.get("content_access_repair_limit") is not None
                else DEFAULT_CONTENT_ACCESS_REPAIR_LIMIT
            ),
        )
        if repair_count >= repair_limit:
            return (
                cls._content_access_partial(
                    state=state,
                    context=context,
                    message=message,
                    targets=targets,
                    pending=pending,
                    required=required,
                    reason="正文读取失败、被忽略或达到取证重试上限；未成功读取的候选来源只能作为标题/摘要或结构化字段，",
                    error_code="content_access_incomplete",
                ),
                True,
            )

        context.events.stage(
            "content_access",
            "started",
            "发现未完成的正文取证，要求模型继续选择或读取来源",
            details={
                "candidate_count": len(targets),
                "required_count": len(required),
                "required_pending_count": len(required_pending),
                "selected_count": len(selected_target_reads),
                "successful_count": len(successful_target_reads),
                "pending_count": len(pending),
                "pending_urls": [str(item.get("url") or "") for item in pending],
                "repair_count": repair_count + 1,
                "repair_limit": repair_limit,
            },
        )
        return (
            {
                **base_update,
                "content_access_feedback": feedback,
                "content_access_repair_count": 1,
                "jump_to": "model",
            },
            True,
        )

    @staticmethod
    def _check_answer(
        state: AgentState,
        context: GraphContext,
        message: AIMessage,
    ) -> dict[str, Any]:
        raw_answer = _message_text(message)
        evidence = [item for item in state.get("evidence") or [] if item.get("success") is True]
        factual_evidence = [item for item in evidence if str(item.get("effect") or "read") != "side_effect"]
        answer, unresolved_evidence_ids = canonicalize_evidence_markers(
            raw_answer,
            factual_evidence,
        )
        if not answer:
            answer = raw_answer
        budget_exhausted = bool(state.get("work_budget_exhausted"))
        budget_detail = str(state.get("work_budget_detail") or "").strip()

        def _budget_partial(payload: dict[str, Any]) -> dict[str, Any]:
            final_answer = str(payload.get("answer_final") or answer).rstrip()
            if budget_exhausted:
                suffix = (
                    "\n\n[本轮未完成全部取证："
                    + (budget_detail or "工具调用预算已用尽")
                    + "]"
                )
                final_answer += suffix
                payload = {
                    **payload,
                    "answer_final": final_answer,
                    "status": "partial",
                    "error_code": "tool_call_budget_exceeded",
                }
                context.events.stage(
                    "evidence",
                    "failed",
                    "工具调用预算已用尽，已保留现有证据并明确未完成的取证缺口",
                    error_code="tool_call_budget_exceeded",
                    details={"detail": budget_detail or "tool call budget exhausted"},
                )
            context.events.commit_model_answer(final_answer)
            return payload

        if not factual_evidence:
            return _budget_partial(
                {
                    "answer_draft": answer,
                    "answer_final": answer,
                    "claim_evidence": [],
                    "status": "completed",
                    "error_code": None,
                }
            )

        ledger = build_claim_evidence_ledger(
            answer,
            factual_evidence,
            [item for item in state.get("tool_results") or [] if isinstance(item, Mapping)],
        )
        cited = list(ledger["cited_evidence_ids"])
        issues: list[str] = []
        if unresolved_evidence_ids:
            issues.append(
                "引用了无法解析的 evidence_id: "
                + ", ".join(unresolved_evidence_ids)
            )
        if not cited:
            issues.append("答案使用了外部工具结果，但没有标注任何 evidence_id")
        issues.extend(str(item) for item in ledger["issues"])
        available_ids = sorted(
            str(item.get("evidence_id") or item.get("id") or "")
            for item in factual_evidence
            if str(item.get("evidence_id") or item.get("id") or "")
        )
        audit_details = {
            "evidence_ids": cited,
            "claim_count": len(ledger["claims"]),
            "fact_claim_count": int(ledger["fact_claim_count"]),
            "inference_claim_count": int(ledger["inference_claim_count"]),
            "unresolved_evidence_ids": unresolved_evidence_ids,
        }

        if issues and int(state.get("evidence_repair_count") or 0) < int(state.get("evidence_repair_limit") or 0):
            repair_targets = [
                {
                    "text": str(claim.get("text") or "")[:600],
                    "evidence_ids": list(claim.get("evidence_ids") or []),
                    "checks": dict(claim.get("checks") or {}),
                }
                for claim in ledger["claims"]
                if not all(bool(value) for value in (claim.get("checks") or {}).values())
            ][:12]
            target_text = "\n".join(
                "- "
                + target["text"]
                + (
                    "（已有证据：" + "、".join(target["evidence_ids"]) + "）"
                    if target["evidence_ids"]
                    else "（尚无证据引用）"
                )
                for target in repair_targets
            )
            feedback = (
                "；".join(issues)
                + "\n可用 evidence_id："
                + "、".join(available_ids)
                + "\n修订要求：只修复真实的证据关联问题，不要因为引用位于表格后的来源行而删除表格内容；"
                "表格或连续列表可由紧随其后的来源行统一引用。重复已核实事实时，沿用对应的已有 evidence_id；"
                "引用必须逐字复制工具结果中的完整 evidence_id，禁止截断、改写或自造 ID；无法确定 ID 时不要添加引用标记。"
                "否定性时效说明（例如无法确认最新价）不要改写成当前/最新事实。"
                + ("\n需要处理的片段：\n" + target_text if target_text else "")
            )
            context.events.stage(
                "evidence",
                "started",
                "发现候选回答的证据关联缺口，正在请求模型基于已有证据修订",
                details={
                    **audit_details,
                    "issues": issues,
                    "available_evidence_ids": available_ids,
                    "repair_targets": repair_targets,
                },
            )
            return {
                "answer_draft": answer,
                "claim_evidence": ledger["claims"],
                "evidence_feedback": feedback,
                "evidence_repair_count": 1,
                "jump_to": "model",
            }

        if issues:
            context.events.stage(
                "evidence",
                "failed",
                "证据关联修订预算已用尽，保留答案并明确标注未完全核验的缺口",
                details={
                    **audit_details,
                    "issues": issues,
                    "available_evidence_ids": available_ids,
                },
            )
            return _budget_partial(
                {
                    "answer_draft": answer,
                    "answer_final": answer,
                    "claim_evidence": ledger["claims"],
                    "status": "partial",
                    "error_code": "evidence_link_incomplete",
                    "evidence_feedback": "",
                }
            )

        context.events.stage(
            "evidence",
            "completed",
            f"已核对 {len(ledger['claims'])} 条结论与 {len(cited)} 条成功证据",
            details=audit_details,
        )
        return _budget_partial(
            {
                "answer_draft": answer,
                "answer_final": answer,
                "claim_evidence": ledger["claims"],
                "status": "completed",
                "error_code": None,
                "evidence_feedback": "",
            }
        )


class ToolExecutionMiddleware(AgentMiddleware[AgentState, GraphContext]):
    """Keep native tool dispatch and guard side effects at the app boundary."""

    name = "atomic_tool_execution"

    async def awrap_tool_call(self, request: Any, handler: Any) -> ToolMessage | Command:
        context: GraphContext = request.runtime.context
        state: AgentState = request.state
        call = dict(request.tool_call)
        tool_call_id = str(call.get("id") or "").strip()
        tool_name = str(call.get("name") or "").strip()
        arguments = dict(call.get("args") or {}) if isinstance(call.get("args"), Mapping) else {}
        spec = context.registry.get_tool(tool_name)
        if not tool_call_id or spec is None:
            return _error_tool_message(
                tool_call_id=tool_call_id or "unknown",
                tool_name=tool_name or "unknown",
                message="该 operation 不存在于当前目录，未执行。",
            )
        try:
            effect = context.registry.effect_for(tool_name, arguments)
            approved = tool_call_id in set(state.get("approved_tool_call_ids") or [])
            if effect == "side_effect" and not approved:
                raise PermissionError("side-effect operation requires a server-approved interrupt")
            if effect == "read":
                # Let the compiled create_agent/ToolNode invoke the real
                # StructuredTool.  The tool adapter preserves the application
                # result/evidence envelope without taking over fan-out.
                with native_tool_context(context, tool_call_id):
                    response = await handler(request)
                record, evidence = _native_tool_result(response)
            else:
                action = {
                    "action_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                }
                if context.side_effect_lock is not None:
                    async with context.side_effect_lock:
                        record, evidence = await context.executor.execute(action, approved=approved)
                else:
                    record, evidence = await context.executor.execute(action, approved=approved)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_code = "invalid_arguments" if isinstance(exc, (ValueError, TypeError, KeyError)) else "tool_dispatch_failed"
            message = f"{type(exc).__name__}: {exc}"
            record = _failed_record(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
                error_code=error_code,
                message=message,
                effect=("side_effect" if 'effect' in locals() and effect == "side_effect" else "read"),
                sensitive_fields=spec.sensitive_fields,
                server_controlled_fields=spec.server_controlled_fields,
            )
            evidence = None
            context.events.stage(
                "tool",
                "failed",
                f"{tool_name} 未执行成功；模型将收到可选择替代来源的错误观察",
                action_id=tool_call_id,
                tool_call_id=tool_call_id,
                error_code=error_code,
                details={"tool_name": tool_name, "error": message[:1_000]},
            )

        record = dict(record)
        record["model_tool_call_id"] = tool_call_id
        success = record.get("success") is True
        conversation_context = (
            _conversation_context_from_result(record.get("result"))
            if success
            else None
        )
        tool_message = ToolMessage(
            content=_tool_message_content(record, evidence),
            name=tool_name,
            tool_call_id=tool_call_id,
            status="success" if success else "error",
        )
        update: dict[str, Any] = {
            "messages": [tool_message],
            "tool_results": [record],
            "evidence": [dict(evidence)] if isinstance(evidence, Mapping) else [],
            "completed_tool_call_ids": [tool_call_id],
            "tool_call_count": 1,
        }
        if conversation_context is not None:
            update["conversation_context"] = conversation_context
        return Command(update=update)


class TerminalPublicationMiddleware(AgentMiddleware[AgentState, GraphContext]):
    """Publish the final model message once the standard agent loop ends."""

    name = "terminal_publication"

    async def aafter_agent(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        context: GraphContext = runtime.context
        answer = str(state.get("answer_final") or "").strip()
        if not answer:
            last = _last_ai_message(state.get("messages") or [])
            answer = _message_text(last) if last is not None else ""
        if not answer:
            answer = "本轮未能生成可发布的回答。"
        status = str(state.get("status") or "partial")
        if status not in {"completed", "partial", "failed", "cancelled", "blocked"}:
            status = "partial"
        context.events.stage(
            "publish",
            "completed" if status == "completed" else "failed",
            "已发布最终回答" if status == "completed" else "已发布带明确缺口说明的结果",
            details={"status": status, "answer_preview": answer[:1_200]},
        )
        context.events.text(answer)
        return {"answer_final": answer, "status": status}


__all__ = [
    "AgentPromptMiddleware",
    "OperationPolicyMiddleware",
    "TerminalPublicationMiddleware",
    "ToolExecutionMiddleware",
]
