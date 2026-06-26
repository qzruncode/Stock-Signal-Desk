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
import uuid
from datetime import datetime
from typing import Any, Dict, List

import litellm
from assistant_stream import RunController, create_run
from assistant_stream.serialization.data_stream import DataStreamResponse
from fastapi import Depends, Request

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.endpoints.agent.tools import (
    _compact_tool_result,
    _format_result,
    _maybe_attach_search_fallback,
)
from src.agent.tool_registry import ToolRegistry
from src.config import get_config, extra_litellm_params, get_api_keys_for_model
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


def _get_llm_config():
    """Get the LLM config for the agent chat endpoint."""
    config = get_config()
    model = config.litellm_model or "gpt-4o"
    api_key = None
    api_base = config.openai_base_url or None

    models = config.llm_model_list if config.llm_model_list else []
    if models:
        first = models[0]
        lp = first.get("litellm_params", {})
        api_key = lp.get("api_key")
        if lp.get("api_base"):
            api_base = lp["api_base"]

    if not api_key:
        keys = get_api_keys_for_model(model, config)
        if keys:
            api_key = keys[0]

    extra = extra_litellm_params(model, config)
    if extra.get("api_base") and not api_base:
        api_base = extra["api_base"]
    extra_headers = extra.get("extra_headers")

    return {
        "model": model,
        "api_key": api_key,
        "api_base": api_base,
        "extra_headers": extra_headers,
    }


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

    kwargs: Dict[str, Any] = {
        "model": llm_cfg["model"],
        "messages": forced_messages,
        "stream": True,
    }
    if llm_cfg.get("api_key"):
        kwargs["api_key"] = llm_cfg["api_key"]
    if llm_cfg.get("api_base"):
        kwargs["api_base"] = llm_cfg["api_base"]
    if llm_cfg.get("extra_headers"):
        kwargs["extra_headers"] = llm_cfg["extra_headers"]

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
) -> str:
    """Execute the ReAct loop: LLM thinks → calls tools → observes → repeats."""
    tools = _registry.get_all_schemas()
    tool_names = set(_registry.get_tool_names())

    full_messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *messages,
    ]

    kwargs: Dict[str, Any] = {
        "model": llm_cfg["model"],
        "messages": full_messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,
    }
    if llm_cfg.get("api_key"):
        kwargs["api_key"] = llm_cfg["api_key"]
    if llm_cfg.get("api_base"):
        kwargs["api_base"] = llm_cfg["api_base"]
    if llm_cfg.get("extra_headers"):
        kwargs["extra_headers"] = llm_cfg["extra_headers"]

    controller.append_text("正在理解问题并规划需要查询的数据...\n\n")

    for iteration in range(MAX_REACT_ITERATIONS):
        chunks: List[Any] = []
        tool_calls_acc: Dict[int, Dict[str, Any]] = {}
        content_text = ""

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as e:
            logger.exception("[Agent] LLM call failed")
            controller.append_text(f"\n\n分析出错：{e}")
            return ""

        async for chunk in response:
            chunks.append(chunk)
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
        for tc in tool_calls_acc.values():
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
                    result = _registry.execute(tool_name, args)
                    llm_result = _compact_tool_result(tool_name, result)
                    llm_result = _maybe_attach_search_fallback(tool_name, args, llm_result)
                    result_str = _format_result(llm_result)
                    tool.set_response(llm_result if isinstance(llm_result, dict) else {"result": result_str})
                except Exception as e:
                    logger.exception("[Agent] Tool execution failed: %s", tool_name)
                    result_str = f"工具执行失败: {e}"
                    tool.set_response({"error": result_str}, is_error=True)

            full_messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": tc["arguments"]},
                }],
            })
            full_messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result_str,
            })

        await _flush_substreams(controller)
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
    llm_cfg = _get_llm_config()
    session_service = ChatSessionService(db_manager)
    conversation = session_service.ensure_conversation(conversation_id)
    final_response_text = ""

    logger.info(
        f"[Agent] Chat request with {len(messages)} messages, "
        f"model={llm_cfg['model']}, conversation_id={conversation['id']}"
    )

    async def run_callback(controller: RunController):
        nonlocal final_response_text
        final_response_text = await _run_react_loop(controller, messages, llm_cfg)

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