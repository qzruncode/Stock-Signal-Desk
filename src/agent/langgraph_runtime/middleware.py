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

from src.agent.claim_validation import claim_checks_pass
from src.tools.base import classify_result_semantics, evidence_record_is_eligible

from .agent_tools import NATIVE_TOOL_RESULT_MARKER, native_tool_context
from .content_access import (
    DEFAULT_CONTENT_ACCESS_REPAIR_LIMIT,
    build_content_access_targets,
    canonical_url,
    cited_reference_action_ids,
    cited_reference_access_status,
    content_read_call_urls,
    required_content_access_targets,
    successful_content_read_urls,
)
from .answer_contract import (
    STRUCTURED_OUTPUT_TOOL_NAME,
    evidence_source_catalog,
    finalize_terminal_answer,
    render_structured_answer,
    resolve_answer_sources,
    structured_answer_blocks,
    structured_answer_mapping,
)
from .claim_evidence import (
    build_claim_evidence_ledger,
    build_structured_claim_evidence_ledger,
)
from .events import redact_arguments
from .executor import action_fingerprint
from .evidence_identity import canonicalize_evidence_markers, prepare_answer_for_client
from .presentation import project_arguments_for_timeline, project_tool_result_for_timeline
from .state import AgentState, GraphContext


def _last_ai_message(messages: Sequence[BaseMessage]) -> AIMessage | None:
    return next((message for message in reversed(messages) if isinstance(message, AIMessage)), None)


_WEB_FALLBACK_TOOL_NAMES = frozenset({"search_web_source", "read_web_source"})
_WEB_FALLBACK_CATEGORIES = frozenset(
    {
        "source_read",
        "source_search",
        "news_source",
        "research",
        "events",
        "macro",
        "market",
        "financials",
    }
)


def _semantic_result_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    """Build one semantic payload from a current or legacy tool record."""
    raw_result = record.get("result")
    payload = dict(raw_result) if isinstance(raw_result, Mapping) else {}
    # Native executor records keep the application envelope in ``result``;
    # older test/embedding records may keep the status fields on the record.
    for key in (
        "success",
        "partial",
        "data_time",
        "data_time_applicable",
        "is_stale",
        "freshness_unknown",
        "has_data",
        "data_status",
        "fallback_used",
        "fallback_recommended",
    ):
        if key not in payload and key in record:
            payload[key] = record[key]
    return payload


def _web_fallback_enabled(spec: Any) -> bool:
    """Resolve the declarative web-fallback capability for one tool."""
    configured = getattr(spec, "web_fallback", None)
    if configured is not None:
        return bool(configured)
    return str(getattr(spec, "category", "") or "") in _WEB_FALLBACK_CATEGORIES


def _source_fallback_reason(
    record: Mapping[str, Any],
    spec: Any,
) -> str | None:
    """Return a user-safe reason when a source result must be web-checked."""
    tool_name = str(record.get("tool_name") or "").strip()
    if (
        not tool_name
        or tool_name in _WEB_FALLBACK_TOOL_NAMES
        or spec is None
        or getattr(spec, "effect", "read") != "read"
    ):
        return None

    payload = _semantic_result_payload(record)
    semantics = classify_result_semantics(payload)
    fallback_recommended = payload.get("fallback_recommended") is True
    if not _web_fallback_enabled(spec) and not fallback_recommended:
        return None
    success = record.get("success") is True
    stale = payload.get("is_stale") is True or record.get("is_stale") is True
    data_time_applicable = payload.get("data_time_applicable") is not False
    freshness_unknown = bool(
        payload.get("freshness_unknown") or record.get("freshness_unknown")
    ) and data_time_applicable
    empty = (
        payload.get("has_data") is False
        or record.get("has_data") is False
        or semantics["data_status"] == "empty"
    )

    if not success:
        return "主来源调用失败"
    if empty:
        return "主来源返回了成功但无可用数据的结果"
    if stale:
        return "主来源返回的数据已过期"
    if freshness_unknown:
        return "主来源没有提供可核验的数据时间"
    if semantics["data_status"] == "partial" and payload.get("fallback_used") is not True:
        return "主来源只返回了不完整结果"
    if fallback_recommended:
        return "主来源已明确建议改用替代来源"
    return None


def _source_fallback_requirements(
    state: Mapping[str, Any],
    registry: Any,
) -> list[dict[str, Any]]:
    """Expose source problems for recovery, never infer coverage from tool order.

    A later successful read does not establish that an earlier gap was filled.
    The existing claim/evidence validator decides whether the actual answer
    has usable support, regardless of which source produced that evidence.
    """
    records = [
        item
        for item in state.get("tool_results") or []
        if isinstance(item, Mapping)
    ]
    requirements: list[dict[str, Any]] = []
    for record in records:
        tool_name = str(record.get("tool_name") or "").strip()
        spec = registry.get_tool(tool_name) if tool_name else None
        reason = _source_fallback_reason(record, spec)
        if reason is None:
            continue
        payload = _semantic_result_payload(record)
        raw_refs = record.get("source_refs") or payload.get("source_refs") or []
        if isinstance(raw_refs, (str, bytes, bytearray)):
            raw_refs = [raw_refs]
        elif not isinstance(raw_refs, (list, tuple, set)):
            raw_refs = []
        source_refs = [
            str(value).strip()
            for value in raw_refs
            if str(value).strip().startswith(("http://", "https://"))
        ]
        requirements.append(
            {
                "action_id": str(record.get("action_id") or record.get("id") or ""),
                "tool_name": tool_name,
                "reason": reason,
                "fallback_operation": "read_web_source" if source_refs else "search_web_source",
                "source_refs": source_refs[:6],
                "arguments": dict(record.get("display_arguments") or record.get("arguments") or {}),
            }
        )
    return requirements


def _source_recovery_tool_names(state: Mapping[str, Any], registry: Any) -> set[str]:
    """Search when no source URL exists; a reader needs an actual target."""
    names = {"search_web_source"}
    if any(item.get("source_refs") for item in _source_fallback_requirements(state, registry)):
        names.add("read_web_source")
    return {name for name in names if registry.get_tool(name) is not None}


def _source_answer_ledger(
    state: Mapping[str, Any],
    last: AIMessage,
    candidate: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Reuse the publication validator to distinguish an answer from progress."""
    evidence = [
        item for item in state.get("evidence") or []
        if isinstance(item, Mapping)
        and evidence_record_is_eligible(item)
        and str(item.get("effect") or "read") != "side_effect"
    ]
    results = [item for item in state.get("tool_results") or [] if isinstance(item, Mapping)]
    if candidate:
        return build_structured_claim_evidence_ledger(
            structured_answer_blocks(candidate), evidence, results,
        )
    return build_claim_evidence_ledger(_message_text(last), evidence, results)


def _has_supported_claim(ledger: Mapping[str, Any]) -> bool:
    return any(
        claim.get("requires_evidence") is not False
        and claim.get("evidence_ids")
        and claim_checks_pass(claim)
        for claim in ledger.get("claims") or []
    )


def _structured_output_call_id(message: AIMessage | None) -> str | None:
    if message is None:
        return None
    for call in message.tool_calls or []:
        if str(call.get("name") or "").strip() == STRUCTURED_OUTPUT_TOOL_NAME:
            call_id = str(call.get("id") or "").strip()
            return call_id or None
    return None


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
        and evidence_record_is_eligible(item)
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
                "如果用户问题仍需要这些外部事实，请选择能补齐相同数据的替代来源，"
                "或调用 search_web_source 搜索、read_web_source 读取相关网页；参数以工具 schema 为准。"
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

    async def abefore_model(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        """Consume recovery feedback once, after the native tools join.

        Parallel tools must not write this scalar state channel. Matching
        ToolMessages also cover rejected/failed calls, so an exhausted tool
        budget cannot leave every later request forced into recovery mode.
        """
        if not state.get("fallback_feedback"):
            return None
        messages = state.get("messages") or []
        last = _last_ai_message(messages)
        completed_ids = {
            message.tool_call_id for message in messages if isinstance(message, ToolMessage)
        }
        if last and any(
            call.get("name") in _WEB_FALLBACK_TOOL_NAMES and call.get("id") in completed_ids
            for call in last.tool_calls or []
        ):
            return {"fallback_feedback": ""}
        return None

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
        fallback_feedback = str(state.get("fallback_feedback") or "").strip()
        # Recovery is a real native tool turn. Exclude the answer tool for
        # this one request so ToolStrategy cannot satisfy required tool use
        # by producing another final-answer candidate instead of a read.
        recovery_names = _source_recovery_tool_names(state, context.registry) if fallback_feedback else set()
        recovery_tools = [
            tool for tool in request.tools
            if getattr(tool, "name", None) in recovery_names
        ] if fallback_feedback else []
        if recovery_tools:
            request = request.override(
                tools=recovery_tools, tool_choice="required", response_format=None,
            )
        content_feedback = str(state.get("content_access_feedback") or "").strip()
        response_format_feedback = str(state.get("response_format_feedback") or "").strip()
        content_targets, pending_content_reads = build_content_access_targets(
            tool_results=state.get("tool_results") or [],
            existing_targets=state.get("content_access_targets") or [],
        )
        target_urls = {canonical_url(item.get("url")) for item in content_targets}
        successful_target_reads = successful_content_read_urls(state.get("tool_results") or []) & target_urls
        unread_target_reads = target_urls - successful_target_reads
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

准备结束本轮时，必须调用结构化输出工具 StructuredAgentAnswer，不要直接输出最终 Markdown。把标题、表格、结论、判断、操作建议和风险拆成有明确边界的 blocks；每个事实、推断、建议或风险 block 都要从本轮来源目录选择支持它的数字 source_id，放入 source_ids（例如 [1, 3]）。不要抄写 ev_ 长编号，也不要把引用写进 content；服务端会将数字映射到真实证据并统一渲染引用。只有 context/disclaimer block 可以在没有外部证据时输出。同一指标的不同口径或时间不得混用；来源冲突时应说明差异，不可拼成一个确定结论。

外部事实只能使用本轮成功工具结果里的证据；只能使用证据里的 data_time，不能把检索时间当成数据时间。没有可用 data_time 时，不要称为“最新/当前/今日”，应继续取证或明确时效未知。来源失败、空结果或不满足所需时效时，继续选择可补齐同一问题的替代来源；也可使用 search_web_source 搜索、read_web_source 读取相关网页，参数以绑定 schema 为准。失败尝试保留在执行记录中，最终结论必须引用实际取得的有效证据。不要重复调用同一个已失败的来源和参数，不要引用失败结果。

reference-only 结果只是标题、摘要或来源索引，不是正文。只有在确实需要文章/PDF内容时，选择相关 URL 调用 read_web_source；不要为了满足规则读取全部候选链接。没有正文时只能按索引事实表述，并明确正文未读取。表格或连续列表可由紧随其后的来源行统一引用；结论、判断和操作建议也必须关联支持它们的有效 evidence_id，不要因引用位置而删除已核实内容。""",
                (
                    "本轮可引用来源目录（source_ids 只能选这些数字；目录随成功取证追加）：\n"
                    + json.dumps(evidence_source_catalog(evidence), ensure_ascii=False, default=str)
                ),
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
                    "结构化回答格式反馈：\n" + response_format_feedback
                    if response_format_feedback
                    else ""
                ),
                (
                    "来源兜底反馈：\n" + fallback_feedback
                    if fallback_feedback
                    else ""
                ),
                (
                    "本轮是取证恢复调用，只能调用当前绑定的搜索/读取工具，"
                    "不可提交最终回答。读取结果返回后会恢复正常工具和结构化回答。"
                    if recovery_tools else ""
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
                    if content_targets and (content_feedback or pending_content_reads or unread_target_reads)
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
                "has_fallback_feedback": bool(fallback_feedback),
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
        structured_response = getattr(response, "structured_response", None)
        structured_call_id = _structured_output_call_id(last)
        if structured_response is not None:
            context.events.stage(
                "model",
                "completed",
                f"第 {model_turn} 轮：模型已提交结构化回答，正在进入终态检查",
                details={
                    "model_turn": model_turn,
                    "structured_output": True,
                    "structured_output_call_id": structured_call_id,
                    "structured_block_count": len(structured_answer_blocks(structured_response)),
                },
            )
        elif last is not None and last.tool_calls:
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
        if last is not None and last.tool_calls and any(
            str(call.get("name") or "").strip() != STRUCTURED_OUTPUT_TOOL_NAME
            for call in last.tool_calls
        ):
            # Tool-planning text is useful progress.  A structured-output
            # call, on the other hand, remains a candidate until the policy
            # middleware validates it.
            context.events.commit_model_progress()
        command_update: dict[str, Any] = {"model_turn_count": 1}
        if structured_call_id:
            # Clear the application-side identity when LangChain is retrying
            # an invalid structured call.  This prevents the previous valid
            # response from being reused if a provider happens to recycle a
            # tool-call id across retries.
            command_update["structured_answer_call_id"] = (
                structured_call_id if structured_response is not None else ""
            )
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update=command_update),
        )


class OperationPolicyMiddleware(AgentMiddleware[AgentState, GraphContext]):
    """Enforce budgets, approval, and selected source-body access before finalization."""

    name = "operation_policy"

    @staticmethod
    def _source_fallback_partial(
        *,
        state: AgentState,
        context: GraphContext,
        last: AIMessage,
        requirements: Sequence[Mapping[str, Any]],
        candidate: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Publish a terminal result without promoting pending tool narration."""
        factual_evidence = [
            item
            for item in state.get("evidence") or []
            if isinstance(item, Mapping)
            and evidence_record_is_eligible(item)
            and str(item.get("effect") or "read") != "side_effect"
        ]
        structured_candidate = structured_answer_mapping(candidate)
        if not structured_candidate:
            structured_candidate = structured_answer_mapping(state.get("structured_answer"))
        ledger = _source_answer_ledger(state, last, structured_candidate)
        if structured_candidate and _has_supported_claim(ledger):
            answer = render_structured_answer(structured_candidate, factual_evidence)
        else:
            answer = "未能取得支持本次分析的有效外部数据，本轮分析已结束。"
        answer, _ = canonicalize_evidence_markers(answer, factual_evidence)
        if not answer:
            answer = "本轮未完成外部来源核验。"
        detail = (
            "来源恢复后仍缺少可支持回答的证据："
            + "；".join(
                f"{item.get('tool_name')}: {item.get('reason')}"
                for item in requirements[:8]
            )
        )
        final_answer = finalize_terminal_answer(
            answer,
            status="partial",
            error_code="source_fallback_incomplete",
            detail=detail,
        )
        context.events.stage(
            "source_fallback",
            "failed",
            "来源恢复未取得可支持回答的证据，本轮已结束",
            error_code="source_fallback_incomplete",
            details={
                "requirements": [dict(item) for item in requirements[:8]],
                "fallback_repair_count": int(state.get("fallback_repair_count") or 0),
                "fallback_repair_limit": int(state.get("fallback_repair_limit") or 0),
            },
        )
        update: dict[str, Any] = {
            "answer_draft": answer,
            "answer_final": final_answer,
            "claim_evidence": _claim_evidence_for_partial_answer(state, answer),
            "fallback_feedback": "",
            "status": "partial",
            "error_code": "source_fallback_incomplete",
            "terminal_detail": detail,
            "jump_to": "end",
        }
        if structured_candidate:
            update["structured_answer"] = structured_candidate
        context.events.commit_model_answer(final_answer)
        return update

    def _source_fallback_gate(
        self,
        state: AgentState,
        context: GraphContext,
        last: AIMessage,
        candidate: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Recover an unsupported terminal candidate inside the native loop."""
        # An ordinary tool call is ongoing work, not a terminal candidate.
        # Native ToolNode must execute it and return its matching ToolMessage;
        # jumping back to the model here would strand pending tool calls.
        if any(
            str(call.get("name") or "").strip() != STRUCTURED_OUTPUT_TOOL_NAME
            for call in last.tool_calls or []
        ):
            return None

        requirements = _source_fallback_requirements(state, context.registry)
        if not requirements:
            return None
        # A failed attempt need not block an answer supported by another
        # source. The normal evidence/content gates still validate every
        # block; a successful but uncited web read never clears all failures.
        if _has_supported_claim(_source_answer_ledger(state, last, candidate)):
            return None

        repair_count = max(0, int(state.get("fallback_repair_count") or 0))
        repair_limit = max(0, int(state.get("fallback_repair_limit") or 0))
        recovery_available = bool(_source_recovery_tool_names(state, context.registry))
        remaining = max(0, int(state.get("tool_call_limit") or 0) - int(state.get("tool_call_count") or 0))
        if repair_count >= repair_limit or not recovery_available or not remaining:
            return self._source_fallback_partial(
                state=state,
                context=context,
                last=last,
                requirements=requirements,
                candidate=candidate,
            )

        instructions: list[str] = []
        for item in requirements[:8]:
            operation = str(item.get("fallback_operation") or "search_web_source")
            if operation == "read_web_source" and item.get("source_refs"):
                instructions.append(
                    f"{item.get('tool_name')}：{item.get('reason')}；相关 URL："
                    + "、".join(str(url) for url in item["source_refs"])
                    + "。调用 read_web_source 读取，必要时搜索独立来源；参数以绑定 schema 为准。"
                )
            else:
                instructions.append(
                    f"{item.get('tool_name')}：{item.get('reason')}；请显式调用 "
                    "search_web_source 重新取证；若返回相关 URL，再调用 read_web_source。"
                )
        feedback = (
            "当前不能结束回答。检测到外部来源存在未恢复的数据缺口：\n"
            + "\n".join(f"- {item}" for item in instructions)
            + "\n本轮执行搜索/读取以补齐用户问题需要的证据；一次无关网页成功不能证明缺口已恢复。"
            "不要重试同一个已失败的主来源，不要引用失败、空、过期或时效未知的观察。"
            "若所有来源仍不可用，明确说明未获得的数据，不要把下一步计划当成最终结论。"
        )
        context.events.stage(
            "source_fallback",
            "started",
            "最终回答缺少有效来源，下一轮通过工具选择约束执行搜索/读取",
            details={
                "requirements": [dict(item) for item in requirements[:8]],
                "fallback_repair_count": repair_count + 1,
                "fallback_repair_limit": repair_limit,
            },
        )
        return {
            "fallback_feedback": feedback,
            "fallback_repair_count": 1,
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["end", "model"])
    async def aafter_model(
        self,
        state: AgentState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        context: GraphContext = runtime.context
        last = _last_ai_message(state.get("messages") or [])
        if last is None:
            return None
        current_structured_call_id = _structured_output_call_id(last)
        structured_answer = resolve_answer_sources(
            state.get("structured_response"), state.get("evidence") or [],
        )
        recorded_structured_call_id = str(state.get("structured_answer_call_id") or "").strip()
        pending_structured_answer = self._pending_structured_answer(state)
        valid_current_answer = bool(
            structured_answer and current_structured_call_id
            and current_structured_call_id == recorded_structured_call_id
        )
        candidate = structured_answer if valid_current_answer else pending_structured_answer
        # Source checks need a parsed candidate with resolved bindings. An
        # unstructured/invalid response has no binding contract yet; treating
        # that as missing source data needlessly forces another external read.
        if candidate or state.get("fallback_feedback") or not state.get("structured_output_required"):
            source_fallback_update = self._source_fallback_gate(state, context, last, candidate)
            if source_fallback_update is not None:
                return source_fallback_update
        if valid_current_answer:
            return self._check_structured_answer(state, context, structured_answer)
        if current_structured_call_id and current_structured_call_id != recorded_structured_call_id:
            # LangChain has already attached a schema-validation error for this
            # new structured output call.  It is still a provider candidate,
            # not an implicit retry instruction.  Keep the retry inside the
            # same bounded response-contract state machine.
            if pending_structured_answer:
                return self._check_structured_answer(
                    state,
                    context,
                    pending_structured_answer,
                )
            return self._request_structured_output_repair(
                state,
                context,
                candidate=_message_text(last),
                reason="模型返回的结构化输出没有通过 LangChain 的解析校验",
            )
        if not last.tool_calls:
            if pending_structured_answer:
                # A content-repair turn may return ordinary text even though
                # the previous typed candidate is still the only answer under
                # review.  Re-run the candidate through the existing content
                # gate; never publish the ordinary text as a second answer.
                return self._check_structured_answer(
                    state,
                    context,
                    pending_structured_answer,
                )
            if bool(state.get("structured_output_required")):
                return self._request_structured_output_repair(
                    state,
                    context,
                    candidate=_message_text(last),
                    reason="模型返回了普通文本而不是要求的 StructuredAgentAnswer",
                )
            content_access_update, blocked = self._content_access_gate(
                state,
                context,
                _message_text(last),
            )
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
    def _pending_structured_answer(state: AgentState) -> dict[str, Any] | None:
        """Return a typed candidate that has not reached a terminal state."""
        candidate = structured_answer_mapping(state.get("structured_answer"))
        if not candidate or str(state.get("answer_final") or "").strip():
            return None
        return candidate

    @staticmethod
    def _structured_output_partial(
        *,
        state: AgentState,
        context: GraphContext,
        candidate: str,
        reason: str,
        content_access_update: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """End a failed response-format repair without exposing its candidate."""
        structured_answer = structured_answer_mapping(state.get("structured_answer"))
        factual_evidence = [
            item
            for item in state.get("evidence") or []
            if isinstance(item, Mapping)
            and evidence_record_is_eligible(item)
            and str(item.get("effect") or "read") != "side_effect"
        ]
        tool_results = [
            item
            for item in state.get("tool_results") or []
            if isinstance(item, Mapping)
        ]

        answer = str(candidate or state.get("answer_draft") or "").strip()
        claims: list[dict[str, Any]] = []
        if structured_answer:
            answer = render_structured_answer(structured_answer, factual_evidence)
            claims = list(
                build_structured_claim_evidence_ledger(
                    structured_answer_blocks(structured_answer),
                    factual_evidence,
                    tool_results,
                ).get("claims")
                or []
            )
        else:
            answer, _ = canonicalize_evidence_markers(answer, factual_evidence)
            if factual_evidence:
                claims = list(
                    build_claim_evidence_ledger(
                        answer,
                        factual_evidence,
                        tool_results,
                    ).get("claims")
                    or []
                )
        if not answer:
            answer = "本轮未能生成符合要求的结构化回答。"

        detail = "结构化回答未能在有限修订次数内完成：" + str(reason or "未提供原因")
        final_answer = finalize_terminal_answer(
            answer,
            status="partial",
            error_code="structured_output_incomplete",
            detail=detail,
        )
        context.events.stage(
            "response_format",
            "failed",
            "结构化回答修订次数已用尽，已停止继续调用模型",
            error_code="structured_output_incomplete",
            details={
                "response_repair_count": int(state.get("response_repair_count") or 0),
                "response_repair_limit": int(state.get("response_repair_limit") or 0),
                "candidate_character_count": len(str(candidate or "")),
                "had_structured_candidate": bool(structured_answer),
                "reason": reason,
            },
        )
        context.events.commit_model_answer(final_answer)
        update: dict[str, Any] = {
            **dict(content_access_update or {}),
            "answer_draft": answer,
            "answer_final": final_answer,
            "claim_evidence": claims,
            "response_format_feedback": "",
            "status": "partial",
            "error_code": "structured_output_incomplete",
            "terminal_detail": detail,
            "jump_to": "end",
        }
        if structured_answer:
            update["structured_answer"] = structured_answer
        return update

    def _request_structured_output_repair(
        self,
        state: AgentState,
        context: GraphContext,
        *,
        candidate: str,
        reason: str,
        content_access_update: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Request one bounded format repair or terminate with a partial result."""
        repair_count = max(0, int(state.get("response_repair_count") or 0))
        repair_limit = max(0, int(state.get("response_repair_limit") or 0))
        if repair_count >= repair_limit:
            return self._structured_output_partial(
                state=state,
                context=context,
                candidate=candidate,
                reason=reason,
                content_access_update=content_access_update,
            )

        feedback = (
            reason
            + "。本次不会把普通文本当作最终答案；请根据当前工具观察和反馈，"
            "只调用 StructuredAgentAnswer 输出完整 blocks。"
        )
        context.events.stage(
            "response_format",
            "failed",
            "候选回答未满足结构化输出契约，已请求一次受限修订",
            details={
                "response_repair_count": repair_count + 1,
                "response_repair_limit": repair_limit,
                "candidate_character_count": len(str(candidate or "")),
                "reason": reason,
            },
        )
        return {
            **dict(content_access_update or {}),
            "answer_draft": str(candidate or "").strip(),
            "response_format_feedback": feedback,
            "response_repair_count": 1,
            "jump_to": "model",
        }

    @staticmethod
    def _content_access_partial(
        *,
        state: AgentState,
        context: GraphContext,
        message: AIMessage | None = None,
        answer: str | None = None,
        targets: list[dict[str, Any]],
        pending: list[dict[str, Any]],
        required: list[dict[str, Any]],
        selection_required: Sequence[Mapping[str, Any]] = (),
        reason: str,
        error_code: str,
        claim_evidence: Sequence[Mapping[str, Any]] | None = None,
        structured_answer: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate_answer = str(answer if answer is not None else _message_text(message)).rstrip()
        factual_evidence = [
            item
            for item in state.get("evidence") or []
            if isinstance(item, Mapping)
            and evidence_record_is_eligible(item)
            and str(item.get("effect") or "read") != "side_effect"
        ]
        candidate_answer, _ = canonicalize_evidence_markers(candidate_answer, factual_evidence)
        if not candidate_answer:
            candidate_answer = "已获取来源索引，但本轮未完成正文读取。"
        claims = list(claim_evidence or [])
        if claim_evidence is None:
            claims = _claim_evidence_for_partial_answer(state, candidate_answer)
        detail = (
            "正文取证未完成："
            + reason
            + "以上结论仅基于来源链接、标题/摘要或结构化字段，正文未核验"
        )
        final_answer = finalize_terminal_answer(
            candidate_answer,
            status="partial",
            error_code=error_code,
            detail=detail,
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
                "selection_required_count": len(selection_required),
                "selection_required_action_ids": [
                    str(item.get("action_id") or "")
                    for item in selection_required
                    if str(item.get("action_id") or "")
                ],
                "reason": reason,
            },
        )
        update = {
            "content_access_targets": targets,
            "required_content_reads": required,
            "pending_content_reads": pending,
            "content_access_feedback": "",
            "response_format_feedback": "",
            "claim_evidence": claims,
            "answer_draft": candidate_answer,
            "answer_final": final_answer,
            "status": "partial",
            "error_code": error_code,
            "terminal_detail": detail,
            "jump_to": "end",
        }
        if structured_answer is not None:
            update["structured_answer"] = dict(structured_answer)
        context.events.commit_model_answer(final_answer)
        return update

    @classmethod
    def _content_access_gate(
        cls,
        state: AgentState,
        context: GraphContext,
        answer: str,
    ) -> tuple[dict[str, Any], bool]:
        """Keep reference-only results from silently becoming body-backed facts."""
        targets, pending = build_content_access_targets(
            tool_results=state.get("tool_results") or [],
            existing_targets=state.get("content_access_targets") or [],
        )
        answer = str(answer or "").strip()
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
        cited_access = cited_reference_access_status(
            answer=answer,
            evidence=state.get("evidence") or [],
            tool_results=state.get("tool_results") or [],
            targets=targets,
        )
        selection_gaps = [
            access
            for access in cited_access.values()
            if access.get("selection_required") is True
        ]
        content_access_gaps = bool(pending or required_pending or selection_gaps)
        if not content_access_gaps:
            if successful_target_reads or required or not cited_reference_actions or cited_access:
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
                        "selection_required_count": 0,
                    },
                )
            return base_update, False

        feedback_parts: list[str] = []
        if selection_gaps:
            selection_candidates = [
                {
                    "action_id": access.get("action_id"),
                    "candidate_count": access.get("candidate_count"),
                    "candidates": access.get("candidate_targets") or [],
                }
                for access in selection_gaps
            ]
            feedback_parts.append(
                "当前答案引用了包含多个候选链接的 reference-only 来源，但还没有选择要核验的正文 URL。"
                "请按引用的具体结论，只选择相关 URL 调用 read_web_source（通常使用 source_id=auto）；"
                "不要求读取未选择的候选链接。若只需来源索引，请明确按标题/摘要或索引信息表述。\n"
                "待选择的来源：\n"
                + json.dumps(selection_candidates, ensure_ascii=False, default=str)
            )
        if required_pending:
            feedback_parts.append(
                "当前不能结束回答。已选定或唯一的正文来源尚未成功提取非空正文。"
                "请重试 read_web_source 或改选一个相关 URL；不要求读取未选择的候选链接。\n"
                + json.dumps(required_pending, ensure_ascii=False, default=str)
            )
        elif pending:
            feedback_parts.append(
                "当前不能结束回答。已选择的来源尚未成功提取非空正文。"
                "请重试 read_web_source 或选择合适的来源读取器；未选择的候选来源不需要读取。\n"
                "待完成的已选择来源：\n"
                + json.dumps(pending, ensure_ascii=False, default=str)
            )
        feedback = "\n\n".join(feedback_parts)
        selection_required = selection_gaps
        if selection_gaps and not required_pending and not pending:
            partial_reason = "引用了多链接 reference-only 来源但未选择结论对应的正文链接；"
        elif selection_gaps:
            partial_reason = "部分引用的 reference-only 来源未选择正文链接，且已有来源读取未完成；"
        elif bool(state.get("work_budget_exhausted")):
            partial_reason = "工具调用预算已用尽；未成功读取的候选来源只能作为标题/摘要或结构化字段，"
        else:
            partial_reason = "正文读取失败、被忽略或达到取证重试上限；未成功读取的候选来源只能作为标题/摘要或结构化字段，"
        if bool(state.get("work_budget_exhausted")):
            return (
                cls._content_access_partial(
                    state=state,
                    context=context,
                    answer=answer,
                    targets=targets,
                    pending=pending,
                    required=required,
                    selection_required=selection_required,
                    reason=partial_reason,
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
                    answer=answer,
                    targets=targets,
                    pending=pending,
                    required=required,
                    selection_required=selection_required,
                    reason=partial_reason,
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

    def _check_structured_answer(
        self,
        state: AgentState,
        context: GraphContext,
        structured_answer: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate and publish one native LangChain structured response.

        ``ToolStrategy`` owns schema parsing and retry.  This method owns the
        application boundary after parsing: source-body access, the explicit
        block-to-evidence ledger, and the terminal status.  It deliberately
        does not parse the rendered Markdown back into claims.
        """
        factual_evidence = [
            item
            for item in state.get("evidence") or []
            if isinstance(item, Mapping)
            and evidence_record_is_eligible(item)
            and str(item.get("effect") or "read") != "side_effect"
        ]
        blocks = structured_answer_blocks(structured_answer)
        answer = render_structured_answer(structured_answer, factual_evidence)
        tool_results = [
            item
            for item in state.get("tool_results") or []
            if isinstance(item, Mapping)
        ]
        ledger = build_structured_claim_evidence_ledger(
            blocks,
            factual_evidence,
            tool_results,
        )
        base_update: dict[str, Any] = {
            "structured_answer": dict(structured_answer),
            "answer_draft": answer,
            "claim_evidence": list(ledger.get("claims") or []),
            "response_format_feedback": "",
        }

        if not answer:
            detail = "结构化回答没有可发布的正文区块"
            final_answer = finalize_terminal_answer(
                "本轮未能生成可发布的回答。",
                status="partial",
                error_code="agent_runtime_failed",
                detail=detail,
            )
            context.events.stage(
                "evidence",
                "failed",
                "结构化回答为空，已阻止发布空结果",
                error_code="agent_runtime_failed",
                details={"structured_output": True, "block_count": len(blocks)},
            )
            context.events.commit_model_answer(final_answer)
            return {
                **base_update,
                "answer_final": final_answer,
                "status": "partial",
                "error_code": "agent_runtime_failed",
                "terminal_detail": detail,
                "evidence_feedback": "",
            }

        content_access_update, blocked = self._content_access_gate(
            state,
            context,
            answer,
        )
        if blocked:
            # A content-access retry keeps the typed candidate in the
            # checkpoint.  If the retry limit is reached, the gate already
            # supplied the terminal partial answer; its fields win below.
            return {
                **base_update,
                **content_access_update,
                "structured_answer": dict(structured_answer),
                "claim_evidence": list(ledger.get("claims") or []),
                "answer_draft": answer,
            }

        issues = list(dict.fromkeys(str(item) for item in ledger.get("issues") or []))
        available_ids = sorted(
            str(item.get("evidence_id") or item.get("id") or "")
            for item in factual_evidence
            if str(item.get("evidence_id") or item.get("id") or "")
        )
        audit_details = {
            "structured_output": True,
            "block_count": len(blocks),
            "claim_count": len(ledger.get("claims") or []),
            "fact_claim_count": int(ledger.get("fact_claim_count") or 0),
            "inference_claim_count": int(ledger.get("inference_claim_count") or 0),
            "evidence_ids": list(ledger.get("cited_evidence_ids") or []),
            "available_evidence_ids": available_ids,
            "unresolved_evidence_ids": list(ledger.get("unresolved_evidence_ids") or []),
        }
        if issues:
            repair_count = max(0, int(state.get("evidence_repair_count") or 0))
            repair_limit = max(0, int(state.get("evidence_repair_limit") or 0))
            if repair_count < repair_limit:
                repair_targets = []
                for index, block in enumerate(blocks):
                    claim = (
                        ledger.get("claims") or []
                    )[index] if index < len(ledger.get("claims") or []) else {}
                    checks = dict(claim.get("checks") or {}) if isinstance(claim, Mapping) else {}
                    if claim_checks_pass(claim):
                        continue
                    repair_targets.append(
                        {
                            "section": str(block.get("section") or "")[:160],
                            "kind": str(block.get("kind") or "fact")[:32],
                            "content": str(block.get("content") or "")[:600],
                            "evidence_ids": list(block.get("evidence_ids") or [])[:24],
                            "source_ids": list(block.get("source_ids") or [])[:24],
                            "checks": checks,
                            "issues": list(claim.get("issues") or []),
                            "unresolved_evidence_ids": list(claim.get("unresolved_evidence_ids") or []),
                        }
                    )
                feedback = (
                    "结构化回答没有通过证据校验，请重新输出完整的 StructuredAgentAnswer。"
                    "保留已核实内容；按具体问题补齐来源、修正口径或明确证据不足。"
                    "fact/inference/recommendation/risk 区块必须在 source_ids 中选择支持它的数字来源编号；"
                    "context/disclaimer 区块可以不引用。不要抄写 ev_ 长编号。\n"
                    "可用来源目录："
                    + json.dumps(evidence_source_catalog(state.get("evidence") or []), ensure_ascii=False, default=str)
                    + "\n未通过的区块："
                    + json.dumps(repair_targets[:12], ensure_ascii=False, default=str)
                    + "\n校验问题："
                    + "；".join(issues)
                )
                context.events.stage(
                    "evidence",
                    "started",
                    "发现结构化回答的证据关联缺口，正在请求模型修订结构化区块",
                    details={
                        **audit_details,
                        "issues": issues,
                        "repair_count": repair_count + 1,
                        "repair_limit": repair_limit,
                        "repair_targets": repair_targets[:12],
                    },
                )
                return {
                    **base_update,
                    "evidence_feedback": feedback,
                    "evidence_repair_count": 1,
                    "jump_to": "model",
                }

            detail = "结构化回答中有区块未关联有效证据，证据关联修订预算已用尽"
            final_answer = finalize_terminal_answer(
                answer,
                status="partial",
                error_code="evidence_link_incomplete",
                detail=detail,
            )
            context.events.stage(
                "evidence",
                "failed",
                "结构化回答的区块证据校验未通过，已发布可追溯的部分结果",
                error_code="evidence_link_incomplete",
                details={**audit_details, "issues": issues},
            )
            context.events.commit_model_answer(final_answer)
            return {
                **base_update,
                "answer_final": final_answer,
                "status": "partial",
                "error_code": "evidence_link_incomplete",
                "terminal_detail": detail,
                "evidence_feedback": "",
            }

        context.events.stage(
            "evidence",
            "completed",
            f"已核对结构化回答的 {len(ledger.get('claims') or [])} 个区块与 {len(ledger.get('cited_evidence_ids') or [])} 条成功证据",
            details=audit_details,
        )
        context.events.commit_model_answer(answer)
        return {
            **base_update,
            "answer_final": answer,
            "status": "completed",
            "error_code": None,
            "terminal_detail": "",
            "evidence_feedback": "",
            **content_access_update,
        }

    @staticmethod
    def _check_answer(
        state: AgentState,
        context: GraphContext,
        message: AIMessage,
    ) -> dict[str, Any]:
        raw_answer = _message_text(message)
        factual_evidence = [
            item
            for item in state.get("evidence") or []
            if evidence_record_is_eligible(item)
            and str(item.get("effect") or "read") != "side_effect"
        ]
        normalized_answer, unresolved_evidence_ids = canonicalize_evidence_markers(
            raw_answer,
            factual_evidence,
        )
        answer = normalized_answer
        ledger_answer = normalized_answer
        if not answer and not unresolved_evidence_ids:
            answer = raw_answer
            ledger_answer = answer
        budget_exhausted = bool(state.get("work_budget_exhausted"))
        budget_detail = str(state.get("work_budget_detail") or "").strip()

        def _budget_partial(payload: dict[str, Any]) -> dict[str, Any]:
            payload_status = str(payload.get("status") or "completed")
            payload_error_code = str(payload.get("error_code") or "").strip() or None
            terminal_error_code = "tool_call_budget_exceeded" if budget_exhausted else payload_error_code
            terminal_detail = (
                "本轮未完成全部取证：" + (budget_detail or "工具调用预算已用尽")
                if budget_exhausted
                else str(payload.get("terminal_detail") or "").strip()
            )
            final_status = "partial" if budget_exhausted else payload_status
            final_answer = finalize_terminal_answer(
                payload.get("answer_final") or answer,
                status=final_status,
                error_code=terminal_error_code,
                detail=terminal_detail,
            )
            if budget_exhausted:
                payload = {
                    **payload,
                    "answer_final": final_answer,
                    "status": "partial",
                    "error_code": "tool_call_budget_exceeded",
                    "terminal_detail": terminal_detail,
                }
                context.events.stage(
                    "evidence",
                    "failed",
                    "工具调用预算已用尽，已保留现有证据并明确未完成的取证缺口",
                    error_code="tool_call_budget_exceeded",
                    details={"detail": budget_detail or "tool call budget exhausted"},
                )
            else:
                payload = {**payload, "answer_final": final_answer}
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
            ledger_answer,
            factual_evidence,
            [item for item in state.get("tool_results") or [] if isinstance(item, Mapping)],
        )
        unresolved_evidence_ids = list(
            dict.fromkeys(
                [
                    *unresolved_evidence_ids,
                    *list(ledger.get("unresolved_evidence_ids") or []),
                ]
            )
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
        issues = list(dict.fromkeys(issues))
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
            if unresolved_evidence_ids and not repair_targets:
                repair_targets = [
                    {
                        "text": raw_answer[:600],
                        "evidence_ids": [],
                        "checks": {"evidence_id": False},
                    }
                ]
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
                "表格或连续列表可由紧随其后的来源行统一引用；表格、结论、判断和操作建议必须在本片段内或紧随其后的来源行关联有效 evidence_id，"
                "后续分析段的引用不能覆盖前面的片段。重复已核实事实时，沿用对应的已有 evidence_id；"
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
                    "terminal_detail": "证据关联修订预算已用尽，保留答案并明确标注未完全核验的缺口",
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
        publishable_evidence = [
            item
            for item in state.get("evidence") or []
            if isinstance(item, Mapping)
            and evidence_record_is_eligible(item)
            and str(item.get("effect") or "read") != "side_effect"
        ]
        answer, _ = prepare_answer_for_client(answer, publishable_evidence)
        status = str(state.get("status") or "partial")
        if status not in {"completed", "partial", "failed", "cancelled", "blocked"}:
            status = "partial"
        error_code = str(state.get("error_code") or "").strip() or None
        terminal_detail = str(
            state.get("terminal_detail")
            or state.get("work_budget_detail")
            or ""
        ).strip()
        answer = finalize_terminal_answer(
            answer,
            status=status,
            error_code=error_code,
            detail=terminal_detail,
        )
        context.events.stage(
            "publish",
            "completed" if status == "completed" else "failed",
            "已发布最终回答" if status == "completed" else "已发布带明确缺口说明的结果",
            error_code=error_code,
            details={"status": status, "answer_preview": answer[:1_200]},
        )
        context.events.text(answer)
        return {
            "answer_final": answer,
            "status": status,
            "error_code": error_code,
            "terminal_detail": terminal_detail,
        }


__all__ = [
    "AgentPromptMiddleware",
    "OperationPolicyMiddleware",
    "TerminalPublicationMiddleware",
    "ToolExecutionMiddleware",
]
