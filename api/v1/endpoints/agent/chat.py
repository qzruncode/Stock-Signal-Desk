# -*- coding: utf-8 -*-
"""
Agent Chat endpoint — ReAct loop with assistant-stream DataStreamResponse.

POST /api/v1/agent/chat

Body: { "messages": [ { "role": "user", "content": "贵州茅台行情" } ] }
Response: DataStreamResponse (line-delimited type-code:json chunks)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List

import litellm
from assistant_stream import RunController, create_run
from assistant_stream.serialization.data_stream import DataStreamResponse
from fastapi import Depends, HTTPException, Request

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.endpoints.agent.tools import (
    _compact_tool_result,
    _format_result,
    _maybe_attach_search_fallback,
)
from src.agent.tool_registry import ToolRegistry
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

MAX_REACT_ITERATIONS = 10

_registry = ToolRegistry()

SYSTEM_PROMPT = """\
你是 A 股智能分析助手，擅长股票分析、行业研究和投资辅助。

## 核心原则
1. 所有数据必须通过工具获取，不得编造任何数字
2. 分析要有理有据，每个结论都要有数据支撑
3. 风险提示优先，宁可不推荐也不做错误推荐
4. 回答使用中文
5. 当用户给出股票名称时，先用 get_stock_info 确认股票代码再查询
6. 数字用千分位，百分比保留两位小数
7. 回答中使用 Markdown 格式化

## 分析框架
- 基本面分析：财务指标 → 估值水平 → 股东结构
- 技术面分析：K线形态 → 量价关系 → 资金流向
- 消息面分析：新闻舆情 → 公告研报 → 社交情绪
- 宏观面分析：大盘指数 → 宏观指标 → 板块轮动

## 工作方式
1. 先理解用户意图，判断需要哪些数据
2. 调用工具获取数据，每次调 1-3 个
3. 分析数据，形成判断
4. 如果信息不足，继续调用工具补充
5. 给出结构化的分析结论

## 工具调用约束
- 优先使用默认参数，不要主动放大时间窗口，除非用户明确要求更长周期
- 优先轻量工具：先看摘要型数据，再决定是否需要更深层明细
- 新闻/公告/舆情默认只看近30天，研报默认近365天，除非用户明确要求长期回溯
- 财务分析优先最近 4-6 个报告期，只有在比较长期趋势时才扩大 periods
- 技术分析优先最近 60 根日线；只有在复盘长周期趋势时才请求更长 K 线
- 板块和宏观工具优先使用摘要结果，不要重复请求相似维度的数据
- 若用户输入的是股票名称，可直接调用支持名称解析的工具，无需先额外做一次无意义查询
"""


class AgentModelConfigError(Exception):
    """Setting 页「模型设置」的 Anthropic 配置缺失时抛出。"""


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TRAILING_SGR_FRAGMENT_RE = re.compile(r"(?:\[(?:0|1|2|3|4|5|7|9)(?:;\d+)*m\])+$")

# 历史轮 tool 结果中的「明细数组」字段：这些是单次压缩保留给当前轮 LLM 看的细节，
# 进入下一轮后已无价值（模型只引用最新结论），裁掉可显著降低 token 累积。
_TOOL_DETAIL_ARRAY_KEYS = (
    "recent", "recent_periods", "history", "items", "top_movers", "bottom_movers",
    "inflow_top", "outflow_top", "top_holders", "holder_changes", "daily_trend",
    "score_trend", "search_fallback", "criteria",
)


def _slim_tool_content(result_str: str) -> str:
    """对历史轮的 tool 结果做二次瘦身：丢弃明细数组，保留摘要字段。

    解析失败（非 JSON / 非 dict）则原样返回，绝不破坏结果。已裁剪过的（含
    `_slimmed` 标记）不重复处理。只影响回灌给 LLM 的历史消息，不影响 UI 气泡。
    """
    text = (result_str or "").strip()
    if not text or text[0] != "{":
        return result_str
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return result_str
    if not isinstance(payload, dict) or payload.get("_slimmed"):
        return result_str

    slimmed: Dict[str, Any] = {}
    for key, value in payload.items():
        if key in _TOOL_DETAIL_ARRAY_KEYS:
            continue
        slimmed[key] = value
    slimmed["_slimmed"] = True
    try:
        return json.dumps(slimmed, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return result_str


# ---------------------------------------------------------------------------
# 入站消息格式适配：前端（@assistant-ui/react-data-stream）发的是 AI SDK v5
# 格式（content 为 part 数组），后端 ReAct 循环内部与 litellm/anthropic 用的是
# OpenAI 格式（tool_calls / tool_call_id + 字符串 content）。两类格式混发会让
# litellm 的 anthropic 转换层静默丢弃历史 tool 数据（convert_to_anthropic_tool_result
# 只认 text/image_url part 且要求顶层 tool_call_id）。这里在入口统一归一化。
# ---------------------------------------------------------------------------

_AI_SDK_PART_TYPES = {"text", "tool-call", "tool-result", "reasoning", "file", "image"}


def _is_aisdk_content(content: Any) -> bool:
    """判断 content 是否为 AI SDK v5 的 part 数组（而非 OpenAI 的字符串/对象）。"""
    if not isinstance(content, list) or not content:
        return False
    return any(
        isinstance(p, dict) and p.get("type") in _AI_SDK_PART_TYPES for p in content
    )


def _join_text_parts(parts: List[Dict[str, Any]]) -> str:
    """把 AI SDK 的 text part 文本拼成一个字符串（reasoning 不拼入 content）。"""
    chunks: List[str] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            text = str(p.get("text") or "").strip()
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def _convert_aisdk_assistant(msg: Dict[str, Any]) -> Dict[str, Any]:
    """AI SDK assistant 消息 → OpenAI 格式（content 字符串 + tool_calls）。"""
    parts = msg.get("content") or []
    content_text = _join_text_parts(parts)
    tool_calls: List[Dict[str, Any]] = []
    for p in parts:
        if not isinstance(p, dict) or p.get("type") != "tool-call":
            continue
        tool_call_id = p.get("toolCallId") or f"call_{uuid.uuid4().hex}"
        tool_name = p.get("toolName") or ""
        try:
            arguments = json.dumps(p.get("input") or {}, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            arguments = "{}"
        tool_calls.append({
            "id": tool_call_id,
            "type": "function",
            "function": {"name": tool_name, "arguments": arguments},
        })
    out: Dict[str, Any] = {"role": "assistant", "content": content_text or None}
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _convert_aisdk_tool(msg: Dict[str, Any]) -> Dict[str, Any]:
    """AI SDK tool 消息 → OpenAI 格式（tool_call_id + 字符串 content）。

    转换后立即对 content 跑一次 _slim_tool_content：这是从前端回传的历史 tool 结果，
    属于更早轮次，明细数组对当前/后续轮无价值，裁掉省 token。本轮新加的 tool 消息
    由 _run_react_loop 末尾的 current_tool_ids 逻辑负责（保完整）。
    """
    parts = msg.get("content") or []
    tool_result = next(
        (p for p in parts if isinstance(p, dict) and p.get("type") == "tool-result"),
        None,
    )
    if tool_result is None:
        return {"role": "tool", "tool_call_id": f"call_{uuid.uuid4().hex}", "content": ""}
    tool_call_id = tool_result.get("toolCallId") or f"call_{uuid.uuid4().hex}"
    output = tool_result.get("output") or {}
    value = output.get("value") if isinstance(output, dict) else output
    is_error = bool(
        (isinstance(output, dict) and output.get("type") == "error-json")
        or tool_result.get("isError")
    )
    try:
        content_str = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        content_str = str(value)
    if is_error:
        content_str = "[工具执行错误] " + content_str
    # 历史 tool 结果二次瘦身（明细数组已无价值）
    content_str = _slim_tool_content(content_str)
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content_str}


def _normalize_incoming_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把前端入站消息归一化为 OpenAI 格式（litellm/anthropic 期望）。

    兼容三种 content 形态：
    - 字符串：OpenAI/简化格式，按原样透传（user/system/assistant）。
    - AI SDK part 数组：转换（见 _convert_aisdk_*）。
    - 已是 OpenAI 格式（tool 有顶层 tool_call_id、assistant 有顶层 tool_calls）：透传。

    单条消息转换失败则原样透传，不阻断整批。
    """
    normalized: List[Dict[str, Any]] = []
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        try:
            role = str(raw.get("role") or "").strip()
            content = raw.get("content")

            # 已是 OpenAI 格式：tool 有顶层 tool_call_id，或 assistant 有顶层 tool_calls → 透传
            if role == "tool" and raw.get("tool_call_id") is not None:
                normalized.append(raw)
                continue
            if role == "assistant" and raw.get("tool_calls") is not None:
                normalized.append(raw)
                continue

            # assistant / tool 的数组 content 一律按 AI SDK 转（OpenAI 这两个角色的
            # content 不会是数组，无需用 _is_aisdk_content 探测 part type）。
            if role == "assistant" and isinstance(content, list):
                normalized.append(_convert_aisdk_assistant(raw))
                continue
            if role == "tool" and isinstance(content, list):
                normalized.append(_convert_aisdk_tool(raw))
                continue

            if not _is_aisdk_content(content):
                # 字符串/空 content：原样透传（补 role 默认值）
                normalized.append({
                    "role": role or "user",
                    **{k: v for k, v in raw.items() if k != "role"},
                })
                continue

            # user / system / 未知 role 的 AI SDK 数组 content：拼文本
            normalized.append({
                "role": role or "user",
                "content": _join_text_parts(content) or None,
            })
        except Exception:
            logger.debug("[Agent] normalize message failed, passthrough: %s", raw)
            normalized.append(raw)
    return normalized


def _clean_model_name(model_name: str) -> str:
    """Remove terminal style fragments that can be pasted into the settings field."""
    cleaned = _ANSI_ESCAPE_RE.sub("", model_name).strip()
    return _TRAILING_SGR_FRAGMENT_RE.sub("", cleaned).strip()


def _get_llm_config() -> Dict[str, Any]:
    """Resolve the agent chat model config from the Setting page (ANTHROPIC_*).

    仅使用 Setting 页「模型设置」保存的接入地址、鉴权令牌和主模型；三者任一
    缺失即报错，不回落到 AGENT_LITELLM_MODEL / litellm_model 等其他来源。
    """
    base_url = (os.getenv("ANTHROPIC_BASE_URL") or "").strip()
    auth_token = (os.getenv("ANTHROPIC_AUTH_TOKEN") or "").strip()
    model_name = _clean_model_name(os.getenv("ANTHROPIC_MODEL") or "")

    missing = []
    if not base_url:
        missing.append("接入地址(ANTHROPIC_BASE_URL)")
    if not auth_token:
        missing.append("鉴权令牌(ANTHROPIC_AUTH_TOKEN)")
    if not model_name:
        missing.append("主模型(ANTHROPIC_MODEL)")
    if missing:
        raise AgentModelConfigError(
            "AI 助手模型未配置完整，请前往「设置 - 模型设置」补全：" + "、".join(missing)
        )

    return {
        "model": model_name,
        "custom_llm_provider": "anthropic",
        "api_key": auth_token,
        "api_base": base_url,
        "extra_headers": {"authorization": f"Bearer {auth_token}"},
    }


def _build_llm_kwargs(
    llm_cfg: Dict[str, Any], **extra: Any
) -> Dict[str, Any]:
    """Assemble litellm.acompletion kwargs from the agent model config.

    合并基础鉴权字段（model/api_key/api_base/custom_llm_provider/extra_headers，
    缺省不写）与调用方额外参数（messages/tools/tool_choice 等）。两处调用
    （_run_react_loop / _stream_final_answer_without_tools）共用，避免重复维护。
    """
    kwargs: Dict[str, Any] = {
        "model": llm_cfg["model"],
        "stream": True,
    }
    if llm_cfg.get("api_key"):
        kwargs["api_key"] = llm_cfg["api_key"]
    if llm_cfg.get("api_base"):
        kwargs["api_base"] = llm_cfg["api_base"]
    if llm_cfg.get("custom_llm_provider"):
        kwargs["custom_llm_provider"] = llm_cfg["custom_llm_provider"]
    if llm_cfg.get("extra_headers"):
        kwargs["extra_headers"] = llm_cfg["extra_headers"]
    kwargs.update(extra)
    return kwargs


async def _flush_substreams(controller: RunController) -> None:
    """Wait for all pending add_stream reader tasks to finish."""
    for task in controller._stream_tasks:
        if not task.done():
            await task


async def _stream_final_answer_without_tools(
    controller: RunController,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
) -> str:
    """Force one final synthesis pass without tool use to avoid silent exits."""
    forced_messages = [
        *messages,
        {
            "role": "system",
            "content": (
                "你已经拿到了前面工具返回的数据。"
                "现在必须直接给出最终分析结论，不要再调用任何工具。"
                "如果信息仍有缺口，要明确说明缺口、风险点和结论置信度。"
            ),
        },
    ]

    kwargs = _build_llm_kwargs(llm_cfg, messages=forced_messages)

    try:
        response = await litellm.acompletion(**kwargs)
    except Exception as e:
        logger.exception("[Agent] Forced final answer failed")
        controller.append_text(
            "\n\n已完成多轮数据查询，但生成最终总结时出错。"
            f"请稍后重试，或缩小问题范围后再问一次。\n\n错误：{e}"
        )
        return ""

    content_text = ""
    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if not delta or not delta.content:
            continue
        content_text += delta.content
        controller.append_text(delta.content)

    if content_text.strip():
        return content_text

    controller.append_text(
        "\n\n已完成多轮数据查询，但模型没有产出最终总结。"
        "建议重试一次，或把问题拆成更小的比较维度来问。"
    )
    return ""


async def _run_react_loop(
    controller: RunController,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    system_prompt: str = "",
) -> str:
    """Execute the ReAct loop: LLM thinks → calls tools → observes → repeats."""
    tools = _registry.get_all_schemas()
    tool_names = set(_registry.get_tool_names())

    full_messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
        *_normalize_incoming_messages(messages),
    ]

    kwargs = _build_llm_kwargs(
        llm_cfg,
        messages=full_messages,
        tools=tools,
        tool_choice="auto",
    )

    controller.append_text("正在理解问题并规划需要查询的数据...\n\n")

    for iteration in range(MAX_REACT_ITERATIONS):
        tool_calls_acc: Dict[int, Dict[str, Any]] = {}
        content_text = ""

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as e:
            logger.exception("[Agent] LLM call failed")
            controller.append_text(f"\n\n分析出错：{e}")
            return ""

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue
            if delta.content:
                content_text += delta.content
                controller.append_text(delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function and tc.function.name:
                        tool_calls_acc[idx]["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_calls_acc[idx]["arguments"] += tc.function.arguments

        if not tool_calls_acc:
            return content_text

        controller.append_text("\n\n正在调用数据工具...\n\n")

        async def _execute_one_tool(tc: Dict[str, Any]) -> Dict[str, Any]:
            """Run a single tool call: concurrent fetch (off the event loop) + UI回填.

            取数（registry.execute + 压缩 + 联网兜底）是同步网络 IO，必须丢进线程池，
            否则会阻塞整个 FastAPI 事件循环。多个工具调用通过 asyncio.gather 并发执行，
            UI 气泡（add_tool_call / set_response）在各自协程里独立回填，作用于互不共享的
            tool 对象，无需加锁；messages 按调用顺序回灌。
            """
            tool_name = tc["name"].strip()
            tool_call_id = tc["id"] or f"call_{uuid.uuid4().hex}"
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"].strip() else {}
            except json.JSONDecodeError:
                args = {}

            tool = await controller.add_tool_call(tool_name, tool_call_id=tool_call_id)
            tool.append_args_text(json.dumps(args, ensure_ascii=False))

            if tool_name not in tool_names:
                result_str = f"工具 '{tool_name}' 不存在，可用工具: {', '.join(sorted(tool_names))}"
                tool.set_response({"error": result_str}, is_error=True)
            else:
                try:
                    def _sync_fetch() -> Any:
                        result = _registry.execute(tool_name, args)
                        llm_result = _compact_tool_result(tool_name, result)
                        return _maybe_attach_search_fallback(tool_name, args, llm_result)

                    llm_result = await asyncio.to_thread(_sync_fetch)
                    result_str = _format_result(llm_result)
                    tool.set_response(llm_result if isinstance(llm_result, dict) else {"result": result_str})
                except Exception as e:
                    logger.exception("[Agent] Tool execution failed: %s", tool_name)
                    result_str = f"工具执行失败: {e}"
                    tool.set_response({"error": result_str}, is_error=True)

            return {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": tc["arguments"],
                "result_str": result_str,
            }

        outcomes = await asyncio.gather(
            *[_execute_one_tool(tc) for tc in tool_calls_acc.values()]
        )

        for oc in outcomes:
            full_messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": oc["tool_call_id"],
                    "type": "function",
                    "function": {"name": oc["tool_name"], "arguments": oc["arguments"]},
                }],
            })
            full_messages.append({
                "role": "tool",
                "tool_call_id": oc["tool_call_id"],
                "content": oc["result_str"],
            })

        await _flush_substreams(controller)

        # 历史轮 tool 结果二次瘦身：本轮刚加的 tool_call_id 跳过，更早轮次的明细数组
        # 已无价值，裁掉可避免 token 随轮次二次方累积。仅影响回灌 LLM 的内容。
        current_tool_ids = {oc["tool_call_id"] for oc in outcomes}
        for msg in full_messages:
            if (
                msg.get("role") == "tool"
                and msg.get("tool_call_id") not in current_tool_ids
            ):
                msg["content"] = _slim_tool_content(msg.get("content", ""))

        kwargs["messages"] = full_messages

    logger.warning("[Agent] ReAct loop hit max iterations without final answer")
    controller.append_text("\n\n已完成多轮数据查询，正在生成最终总结...\n\n")
    return await _stream_final_answer_without_tools(controller, full_messages, llm_cfg)


@router.post("/agent/chat")
async def agent_chat(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """Chat endpoint using assistant-stream DataStream protocol."""
    body = await request.json()
    messages = body.get("messages", [])
    conversation_id = body.get("conversation_id")
    try:
        llm_cfg = _get_llm_config()
    except AgentModelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session_service = ChatSessionService(db_manager)
    conversation = session_service.ensure_conversation(conversation_id)
    final_response_text = ""

    logger.info(
        f"[Agent] Chat request with {len(messages)} messages, "
        f"model={llm_cfg['model']}, conversation_id={conversation['id']}"
    )

    async def run_callback(controller: RunController):
        nonlocal final_response_text
        from src.services.agent_prompt_service import AgentPromptService

        system_prompt, is_fallback = AgentPromptService(db_manager).get_active_system_prompt()
        logger.info(
            "[Agent] system prompt %s",
            "fallback to source default" if is_fallback else f"from template",
        )
        final_response_text = await _run_react_loop(
            controller, messages, llm_cfg, system_prompt
        )

        persisted_messages = list(messages)
        if final_response_text.strip():
            persisted_messages.append(
                {
                    "id": f"assistant-{uuid.uuid4().hex}",
                    "role": "assistant",
                    "content": final_response_text,
                    "created_at": datetime.now().isoformat(),
                }
            )
        session_service.save_conversation_snapshot(conversation["id"], persisted_messages)

    stream = create_run(run_callback)
    return DataStreamResponse(stream)
