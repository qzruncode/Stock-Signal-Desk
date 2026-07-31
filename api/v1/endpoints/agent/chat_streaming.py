# -*- coding: utf-8 -*-
"""Model streaming primitives and final synthesis orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

import litellm

from src.agent.orchestrator_v2.contracts import AgentStage, AgentStageEventV2
from src.agent.result_contracts import AnalysisPlaybook, INVESTMENT_DECISION
from src.llm.anthropic_gateway import build_litellm_kwargs
from api.v1.endpoints.agent.chat_reasoning import (
    _BufferedReasoningEmitter,
    _append_model_reasoning,
    _append_process_reasoning,
    _extract_model_reasoning_delta,
    _response_field,
    _visible_reasoning_uses_chinese,
    _with_chinese_visible_reasoning,
)

logger = logging.getLogger(__name__)
MODEL_STREAM_HEARTBEAT_SECONDS = 5.0
ControllerLike = Any

async def _await_model_stream_step(
    awaitable,
    *,
    controller: ControllerLike,
    label: str,
    started_at: float,
    heartbeat_seconds: float = MODEL_STREAM_HEARTBEAT_SECONDS,
):
    """Wait for one model-stream operation without imposing a deadline."""
    task = asyncio.ensure_future(awaitable)
    heartbeat: asyncio.Task[None] | None = None
    try:
        while True:
            heartbeat = asyncio.create_task(
                asyncio.sleep(heartbeat_seconds)
            )
            done, _ = await asyncio.wait(
                {task, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                heartbeat = None
                return task.result()
            _append_process_reasoning(
                controller,
                (f"模型仍在处理「{label}」，" f"已等待 {max(1, int(time.monotonic() - started_at))} 秒"),
            )
    finally:
        if heartbeat is not None and not heartbeat.done():
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _stream_structured_model_completion(
    controller: ControllerLike,
    completion: Callable[..., Any],
    heartbeat_seconds: float = MODEL_STREAM_HEARTBEAT_SECONDS,
    **kwargs: Any,
) -> Any:
    """Stream visible model analysis while rebuilding one tool-call response."""
    request_kwargs = dict(kwargs)
    request_kwargs["stream"] = True
    request_kwargs["messages"] = _with_chinese_visible_reasoning(request_kwargs.get("messages"))
    function_name = str((request_kwargs.get("tool_choice") or {}).get("function", {}).get("name") or "结构化分析")
    started_at = time.monotonic()
    response = await _await_model_stream_step(
        completion(**request_kwargs),
        controller=controller,
        label=function_name,
        started_at=started_at,
        heartbeat_seconds=heartbeat_seconds,
    )
    if not hasattr(response, "__aiter__"):
        choices = _response_field(response, "choices") or []
        for choice in choices:
            message = _response_field(choice, "message")
            reasoning = _extract_model_reasoning_delta(message)
            if reasoning and _visible_reasoning_uses_chinese(reasoning):
                _append_process_reasoning(
                    controller,
                    f"模型可见分析 · {function_name}",
                )
                _append_model_reasoning(controller, reasoning)
                _append_model_reasoning(controller, "\n")
        return response

    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_calls: Dict[int, Dict[str, Any]] = {}
    reasoning_emitter = _BufferedReasoningEmitter(
        controller,
        label=f"模型可见分析 · {function_name}",
    )
    iterator = response.__aiter__()
    try:
        while True:
            try:
                chunk = await _await_model_stream_step(
                    anext(iterator),
                    controller=controller,
                    label=function_name,
                    started_at=started_at,
                    heartbeat_seconds=heartbeat_seconds,
                )
            except StopAsyncIteration:
                break
            choices = _response_field(chunk, "choices") or []
            if not choices:
                continue
            delta = _response_field(choices[0], "delta")
            reasoning_delta = _extract_model_reasoning_delta(delta)
            if reasoning_delta:
                reasoning_parts.append(reasoning_delta)
                reasoning_emitter.append(reasoning_delta)
            content = _response_field(delta, "content")
            if isinstance(content, str) and content:
                content_parts.append(content)
            for fallback_index, raw_call in enumerate(_response_field(delta, "tool_calls") or []):
                raw_index = _response_field(raw_call, "index")
                index = raw_index if isinstance(raw_index, int) else fallback_index
                call = tool_calls.setdefault(
                    index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {
                            "name": "",
                            "arguments": "",
                        },
                    },
                )
                call_id = _response_field(raw_call, "id")
                if isinstance(call_id, str) and call_id:
                    call["id"] = call_id
                function = _response_field(raw_call, "function")
                name_delta = _response_field(function, "name")
                if isinstance(name_delta, str) and name_delta:
                    call["function"]["name"] += name_delta
                arguments_delta = _response_field(function, "arguments")
                if isinstance(arguments_delta, str) and arguments_delta:
                    call["function"]["arguments"] += arguments_delta
                elif isinstance(arguments_delta, dict):
                    call["function"]["arguments"] = json.dumps(
                        arguments_delta,
                        ensure_ascii=False,
                    )
    finally:
        reasoning_emitter.flush()
        closer = getattr(response, "aclose", None)
        if callable(closer):
            await closer()
    if reasoning_emitter.emitted:
        _append_model_reasoning(controller, "\n")
    rebuilt_calls = []
    for index in sorted(tool_calls):
        call = tool_calls[index]
        if not call["id"]:
            call["id"] = f"structured_{index}"
        if not call["function"]["name"]:
            call["function"]["name"] = function_name
        rebuilt_calls.append(call)
    return {
        "choices": [
            {
                "message": {
                    "content": "".join(content_parts) or None,
                    "reasoning_content": "".join(reasoning_parts) or None,
                    "tool_calls": rebuilt_calls,
                },
            }
        ],
    }


async def _collect_streamed_model_answer(
    controller: ControllerLike,
    completion: Callable[..., Any],
    request_kwargs: Mapping[str, Any],
    *,
    label: str,
    heartbeat_seconds: float = MODEL_STREAM_HEARTBEAT_SECONDS,
) -> tuple[str, str]:
    """Collect one answer stream until the model finishes or the user cancels."""
    started_at = time.monotonic()
    visible_request_kwargs = dict(request_kwargs)
    visible_request_kwargs["messages"] = _with_chinese_visible_reasoning(visible_request_kwargs.get("messages"))
    response = await _await_model_stream_step(
        completion(**visible_request_kwargs),
        controller=controller,
        label=label,
        started_at=started_at,
        heartbeat_seconds=heartbeat_seconds,
    )
    if not hasattr(response, "__aiter__"):
        choices = _response_field(response, "choices") or []
        choice = choices[0] if choices else None
        message = _response_field(choice, "message")
        reasoning = _extract_model_reasoning_delta(message)
        if reasoning and _visible_reasoning_uses_chinese(reasoning):
            _append_process_reasoning(controller, f"模型可见分析 · {label}")
            _append_model_reasoning(controller, reasoning)
            _append_model_reasoning(controller, "\n")
        return (
            str(_response_field(message, "content") or ""),
            str(_response_field(choice, "finish_reason") or ""),
        )

    content_parts: List[str] = []
    finish_reason = ""
    reasoning_emitter = _BufferedReasoningEmitter(
        controller,
        label=f"模型可见分析 · {label}",
    )
    iterator = response.__aiter__()
    try:
        while True:
            try:
                chunk = await _await_model_stream_step(
                    anext(iterator),
                    controller=controller,
                    label=label,
                    started_at=started_at,
                    heartbeat_seconds=heartbeat_seconds,
                )
            except StopAsyncIteration:
                break
            choices = _response_field(chunk, "choices") or []
            if not choices:
                continue
            choice = choices[0]
            choice_finish_reason = _response_field(choice, "finish_reason")
            if choice_finish_reason:
                finish_reason = str(choice_finish_reason)
            delta = _response_field(choice, "delta")
            reasoning_delta = _extract_model_reasoning_delta(delta)
            if reasoning_delta:
                reasoning_emitter.append(reasoning_delta)
            content = _response_field(delta, "content")
            if isinstance(content, str) and content:
                content_parts.append(content)
    finally:
        reasoning_emitter.flush()
        closer = getattr(response, "aclose", None)
        if callable(closer):
            await closer()
    if reasoning_emitter.emitted:
        _append_model_reasoning(controller, "\n")
    return "".join(content_parts), finish_reason


async def _stream_final_answer_without_tools(
    controller: ControllerLike,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    *,
    state: Optional[Dict[str, Any]] = None,
    evidence: Optional[List[Dict[str, Any]]] = None,
    playbook: Optional[AnalysisPlaybook] = None,
    answer_validator: Optional[Callable[[str, Optional[List[Dict[str, Any]]]], List[str]]] = None,
    completion: Optional[Callable[..., Any]] = None,
    synthesis_builder: Callable[..., List[Dict[str, Any]]],
    contract_checker: Callable[..., List[str]],
    prepare_answer: Callable[[Optional[AnalysisPlaybook], str], str],
    fallback_builder: Callable[[Optional[List[Dict[str, Any]]]], Optional[str]],
    llm_builder: Callable[..., Dict[str, Any]] = build_litellm_kwargs,
    heartbeat_seconds: float = MODEL_STREAM_HEARTBEAT_SECONDS,
) -> str:
    """Force one final synthesis pass without tool use to avoid silent exits.

    state: 可选共享容器,累积最终答案文本,使外层在取消时能取到已生成内容。
    """
    completion = completion or litellm.acompletion
    forced_messages = synthesis_builder(messages, evidence, playbook)

    def contract_issues(content: str) -> List[str]:
        issues = contract_checker(playbook, content, evidence)
        if answer_validator is not None:
            issues.extend(answer_validator(content, evidence))
        return list(dict.fromkeys(issues))

    is_professional_decision = playbook is not None and playbook.id == INVESTMENT_DECISION.id
    is_playbook_answer = playbook is not None

    kwargs = llm_builder(
        llm_cfg,
        stream=True,
        messages=forced_messages,
        max_tokens=4200 if is_professional_decision else (3200 if is_playbook_answer else 2600),
        temperature=0.1,
    )

    try:
        content_text, finish_reason = await _collect_streamed_model_answer(
            controller,
            completion,
            kwargs,
            label="最终答案综合",
            heartbeat_seconds=heartbeat_seconds,
        )
    except Exception:
        logger.exception("[Agent] Forced final answer failed")
        if state is not None:
            state["_synthesis_failed"] = True
        content_text = fallback_builder(evidence) or ""
        controller.append_text(content_text)
        if state is not None:
            state["assistant_text"] = content_text
            controller.assistant_text_snapshot = content_text
        return content_text

    content_text = prepare_answer(playbook, content_text)
    if content_text.strip() and finish_reason.lower() not in {"length", "max_tokens"}:
        reasons = contract_issues(content_text)
        if reasons:
            logger.warning("[Agent] final answer needs repair: %s", "; ".join(reasons))
            repair_messages = [
                *forced_messages,
                {"role": "assistant", "content": content_text},
                {
                    "role": "user",
                    "content": (
                        "[运行时输出验收未通过]\n"
                        + "；".join(reasons)
                        + "。请仅重写最终答案，不再调用工具。必须使用已有证据补齐这些结构；"
                        "证据没有提供的内容明确写‘证据缺失’，禁止猜测。"
                    ),
                },
            ]
            repair_kwargs = llm_builder(
                llm_cfg,
                stream=True,
                messages=repair_messages,
                max_tokens=4200 if is_professional_decision else 3600,
                temperature=0.0,
            )
            repaired_text = ""
            repaired_finish_reason = ""
            try:
                repaired_text, repaired_finish_reason = await _collect_streamed_model_answer(
                    controller,
                    completion,
                    repair_kwargs,
                    label="最终答案字段修复",
                    heartbeat_seconds=heartbeat_seconds,
                )
            except Exception:
                logger.exception("[Agent] final answer repair failed")

            repaired_text = prepare_answer(playbook, repaired_text)
            repair_issues = contract_issues(repaired_text)
            if (
                repaired_text.strip()
                and repaired_finish_reason.lower() not in {"length", "max_tokens"}
                and not repair_issues
            ):
                content_text = repaired_text
            else:
                if repair_issues:
                    logger.warning("[Agent] rejected repaired answer: %s", "; ".join(repair_issues))
                if state is not None:
                    state["_synthesis_failed"] = True
                content_text = fallback_builder(evidence) or ""
        controller.append_text(content_text)
        if state is not None:
            # Final answer replaces any earlier internal planning snapshot.
            state["assistant_text"] = content_text
            controller.assistant_text_snapshot = content_text
        return content_text

    if content_text.strip():
        logger.warning("[Agent] final synthesis truncated: finish_reason=%s", finish_reason)

    if is_playbook_answer:
        logger.warning(
            "[Agent] playbook synthesis returned %s; retrying compact final",
            "truncated text" if content_text.strip() else "empty text",
        )
        retry_messages = [
            *forced_messages,
            {
                "role": "user",
                "content": (
                    "[最终综合没有产生完整文本]\n请直接输出一份紧凑但完整的最终答案，不调用工具。"
                    "逐项满足当前 Playbook 输出合同，关键事实保留可点击来源；"
                    "缺失证据明确写出，不得猜测。"
                ),
            },
        ]
        retry_kwargs = llm_builder(
            llm_cfg,
            stream=True,
            messages=retry_messages,
            max_tokens=4200 if is_professional_decision else 2800,
            temperature=0.0,
        )
        retry_text = ""
        retry_finish_reason = ""
        try:
            retry_text, retry_finish_reason = await _collect_streamed_model_answer(
                controller,
                completion,
                retry_kwargs,
                label="最终答案完整性修复",
                heartbeat_seconds=heartbeat_seconds,
            )
        except Exception:
            logger.exception("[Agent] empty/truncated synthesis retry failed")
        retry_text = prepare_answer(playbook, retry_text)
        retry_issues = contract_issues(retry_text)
        if retry_text.strip() and retry_finish_reason.lower() not in {"length", "max_tokens"} and not retry_issues:
            content_text = retry_text
            controller.append_text(content_text)
            if state is not None:
                state["assistant_text"] = content_text
                controller.assistant_text_snapshot = content_text
            return content_text
        if retry_issues:
            logger.warning("[Agent] rejected empty/truncated retry: %s", "; ".join(retry_issues))

    if state is not None:
        state["_synthesis_failed"] = True
    content_text = fallback_builder(evidence) or ""
    controller.append_text(content_text)
    if state is not None:
        state["assistant_text"] = content_text
        controller.assistant_text_snapshot = content_text
    return content_text



__all__ = ["_await_model_stream_step", "_stream_structured_model_completion", "_collect_streamed_model_answer", "_stream_final_answer_without_tools"]
