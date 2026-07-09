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
from typing import Any, Dict, List, Optional

import litellm
from assistant_stream.serialization.data_stream import DataStreamResponse
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.endpoints.agent.tools import (
    _compact_tool_result,
    _format_result,
    _maybe_attach_search_fallback,
)
from src.agent.run_registry import (
    ActiveRun,
    RunBroadcaster,
    active_run_registry,
)
from src.agent.tool_registry import ToolRegistry
from src.llm.anthropic_gateway import (
    AnthropicGatewayConfigError,
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
)
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

MAX_REACT_ITERATIONS = 10

# controller 产出层:当前生产路径走自建后台运行时的 RunBroadcaster (方法名与
# assistant-stream 的 RunController 对齐:append_text/add_tool_call/add_data/
# append_reasoning,以及 _stream_tasks 属性),不再依赖 create_run 的单连接生命周期。
ControllerLike = RunBroadcaster

_registry = ToolRegistry()

# ── Human-in-the-Loop: 需要人工确认才能执行的工具 ────────────────────────
# get_buy_criteria_analysis 会顺序调用 8 次 LLM,耗时且费 token,执行前需用户确认。
# assistant-stream 0.0.32 无内置 interrupt/resume 原语,这里自建轻量协议:
#   1. _execute_one_tool 检测到 gated 工具 → controller.add_data 发 approval-request
#   2. asyncio.Event 阻塞等待,POST /agent/approve 收到用户决定后 set event
#   3. 批准 → 继续执行;拒绝/超时 → set_response(is_error) 跳过执行
APPROVAL_TIMEOUT_SECONDS = 180
GATED_TOOLS = {"get_buy_criteria_analysis"}


class _ApprovalState:
    """单次工具调用的审批等待状态。"""

    __slots__ = ("event", "approved", "symbol")

    def __init__(self, symbol: str) -> None:
        self.event: asyncio.Event = asyncio.Event()
        self.approved: Optional[bool] = None
        self.symbol = symbol


# tool_call_id -> _ApprovalState。进程级注册表(单进程 FastAPI 足够)。
_pending_approvals: Dict[str, _ApprovalState] = {}

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


# 兼容别名：历史代码与测试以 ``AgentModelConfigError`` 捕获网关配置缺失错误。
# Phase 6 测试迁移后将移除此别名，统一改用 ``AnthropicGatewayConfigError``。
AgentModelConfigError = AnthropicGatewayConfigError


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


# ---------------------------------------------------------------------------
# 模型配置 / litellm kwargs 组装已收敛到共享 helper ``src.llm.anthropic_gateway``。
# 此处保留薄封装别名，便于本文件内调用点维持原读法；``stream`` 由调用方显式传入。
# ---------------------------------------------------------------------------
_get_llm_config = resolve_anthropic_gateway_config


def _build_llm_kwargs(llm_cfg: Dict[str, Any], *, stream: bool, **extra: Any) -> Dict[str, Any]:
    """Assemble litellm kwargs from the gateway config (delegates to shared helper)."""
    return build_litellm_kwargs(llm_cfg, stream=stream, **extra)


# ---------------------------------------------------------------------------
# 上下文窗口管理：token 估算 + 超阈值时自动摘要压缩早期对话
# ---------------------------------------------------------------------------

# 压缩触发阈值 = 窗口 × 0.8（留 20% 给回复，弥补中文 token 估算偏差）
_CONTEXT_COMPACT_RATIO = 0.8
# 压缩时保留最近多少条消息完整（user/assistant/tool 成组，不切断 tool_call↔result 配对）
_KEEP_RECENT_MESSAGES = 6

_COMPACT_SUMMARY_PROMPT = """\
你是对话压缩助手。下面是用户与 A 股分析助手的早期对话（含工具调用与结果）。
请把它压缩成一段紧凑的中文摘要，供后续对话引用。要求：
1. 保留所有出现过的股票代码、公司名称、关键数值（价格、涨跌幅、财务指标、日期）。
2. 保留已得出的分析结论与判断（如"近60天震荡上行""估值偏高""不可买入"等）。
3. 丢弃查询过程、工具调用细节、重复的中间数据明细。
4. 只输出摘要正文，不要加标题或额外说明。\
"""


def _estimate_messages_tokens(messages: List[Dict[str, Any]], model: str) -> int:
    """估算 messages 的 token 数。litellm.token_counter 失败时回退字符粗估。"""
    try:
        return int(litellm.token_counter(model=model, messages=messages))
    except Exception:
        # 回退：网关模型未映射 → 按字符粗估（中文约 1.5 字/token，英文约 4 字符/token，取 2.5 偏保守）
        total_chars = 0
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                total_chars += sum(len(str(p)) for p in content)
            # tool_calls 等结构也计入
            tc = m.get("tool_calls")
            if tc:
                total_chars += sum(len(json.dumps(t, ensure_ascii=False, default=str)) for t in tc)
        return int(total_chars / 2.5) + len(messages) * 4


async def _summarize_for_compaction(
    llm_cfg: Dict[str, Any], to_summarize: List[Dict[str, Any]]
) -> Optional[str]:
    """调一次 LLM 把早期消息压缩成摘要文本。失败返回 None（调用方回退）。"""
    compact_messages = [
        {"role": "system", "content": _COMPACT_SUMMARY_PROMPT},
        {"role": "user", "content": json.dumps(
            [{"role": m.get("role"), "content": m.get("content")} for m in to_summarize],
            ensure_ascii=False, default=str,
        )},
    ]
    kwargs = _build_llm_kwargs(llm_cfg, stream=False, messages=compact_messages)  # 摘要非流式，直接拿完整文本
    try:
        response = await litellm.acompletion(**kwargs)
        text = ""
        for choice in getattr(response, "choices", []) or []:
            msg = getattr(choice, "message", None)
            if msg and getattr(msg, "content", None):
                text += str(msg.content)
        return text.strip() or None
    except Exception:
        logger.warning("[Agent] compaction summary LLM call failed", exc_info=True)
        return None


async def _compact_history_if_needed(
    full_messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """超阈值时把早期对话摘要化，保留最近 _KEEP_RECENT_MESSAGES 条完整。

    返回可能被压缩后的 full_messages。未超阈值或无法压缩时原样返回。
    """
    context_window = llm_cfg.get("context_window") or 200_000
    threshold = int(context_window * _CONTEXT_COMPACT_RATIO)
    tokens_before = _estimate_messages_tokens(full_messages, llm_cfg["model"])
    if tokens_before < threshold:
        return full_messages

    # 至少保留 system + 近期消息；待摘要部分为空（消息太少但单条超长）则无法压缩
    if len(full_messages) <= _KEEP_RECENT_MESSAGES + 1:
        logger.info(
            "[Agent] context over threshold (%d/%d) but too few messages to compact",
            tokens_before, threshold,
        )
        return full_messages

    # system（首条）保留，其后到「倒数 KEEP_RECENT_MESSAGES 条」之间为待摘要部分
    keep_count = _KEEP_RECENT_MESSAGES
    to_summarize = full_messages[1:-keep_count]
    recent = full_messages[-keep_count:]
    # 待摘要太少（<=1）则不再压缩：可能是上一轮刚压缩过只剩摘要，再压缩无意义且会重复。
    if len(to_summarize) <= 1:
        return full_messages

    summary = await _summarize_for_compaction(llm_cfg, to_summarize)
    if not summary:
        # 摘要失败：宁可交给主调用可能超限，也不丢数据、不伪造摘要
        logger.warning(
            "[Agent] compaction skipped (summary empty), tokens=%d threshold=%d",
            tokens_before, threshold,
        )
        return full_messages

    compacted = [
        full_messages[0],  # system
        {"role": "user", "content": f"[早期对话摘要]\n{summary}"},
        *recent,
    ]
    tokens_after = _estimate_messages_tokens(compacted, llm_cfg["model"])
    # 只记日志，不向前端推提示：append_text 会进 assistant 消息正文被持久化和回传 LLM，
    # 污染对话；data-stream 协议又无 onData 钩子接收结构化事件。压缩对用户透明即可。
    logger.info(
        "[Agent] context compacted: %d msgs → summary, tokens %d → %d (threshold %d)",
        len(to_summarize), tokens_before, tokens_after, threshold,
    )
    return compacted


async def _flush_substreams(controller: ControllerLike) -> None:
    """Wait for all pending add_stream reader tasks to finish."""
    for task in controller._stream_tasks:
        if not task.done():
            await task


async def _stream_final_answer_without_tools(
    controller: ControllerLike,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    *,
    state: Optional[Dict[str, str]] = None,
) -> str:
    """Force one final synthesis pass without tool use to avoid silent exits.

    state: 可选共享容器,累积最终答案文本,使外层在取消时能取到已生成内容。
    """
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

    kwargs = _build_llm_kwargs(llm_cfg, stream=True, messages=forced_messages)

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
        if state is not None:
            # 最终答案覆盖此前累积的中间推理文本(它才是该被落定的内容)
            state["assistant_text"] = content_text
            controller.assistant_text_snapshot = content_text

    if content_text.strip():
        return content_text

    controller.append_text(
        "\n\n已完成多轮数据查询，但模型没有产出最终总结。"
        "建议重试一次，或把问题拆成更小的比较维度来问。"
    )
    return ""


async def _run_react_loop(
    controller: ControllerLike,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    system_prompt: str = "",
    on_progress=None,
    *,
    state: Optional[Dict[str, str]] = None,
) -> str:
    """Execute the ReAct loop: LLM thinks → calls tools → observes → repeats.

    on_progress: 可选回调 async (assistant_text_so_far: str) -> None,每轮迭代末尾
    调用,用于增量持久化已生成的 assistant 文本(刷新后可恢复)。
    state: 可选共享容器,实时记录已累积 assistant 文本,使外层在取消时能取到。
    """
    tools = _registry.get_all_schemas()
    tool_names = set(_registry.get_tool_names())

    full_messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
        *_normalize_incoming_messages(messages),
    ]

    assistant_text_acc = ""  # 跨轮累积的 assistant 文本(用于增量持久化)
    if state is not None:
        state["assistant_text"] = ""

    kwargs = _build_llm_kwargs(
        llm_cfg,
        stream=True,
        messages=full_messages,
        tools=tools,
        tool_choice="auto",
    )

    controller.append_text("正在理解问题并规划需要查询的数据...\n\n")

    for iteration in range(MAX_REACT_ITERATIONS):
        tool_calls_acc: Dict[int, Dict[str, Any]] = {}
        content_text = ""

        # 每轮调用前检查上下文是否超阈值，超了就把早期对话摘要压缩（含首轮：前端回传的历史可能已超限）
        full_messages = await _compact_history_if_needed(full_messages, llm_cfg)
        kwargs["messages"] = full_messages

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
                assistant_text_acc += delta.content
                if state is not None:
                    state["assistant_text"] = assistant_text_acc
                    controller.assistant_text_snapshot = assistant_text_acc
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
                # HITL: 需要人工确认的工具,执行前等待用户批准
                gate_approved = True
                if tool_name in GATED_TOOLS:
                    gate_symbol = str(args.get("symbol", ""))
                    approval = _ApprovalState(gate_symbol)
                    _pending_approvals[tool_call_id] = approval
                    controller.add_data({
                        "type": "approval-request",
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "symbol": gate_symbol,
                        "reason": "将调用 8 次 LLM 进行买入判定,预计 ~30s,是否继续?",
                    })
                    try:
                        await asyncio.wait_for(
                            approval.event.wait(), timeout=APPROVAL_TIMEOUT_SECONDS
                        )
                        gate_approved = approval.approved is True
                    except asyncio.TimeoutError:
                        gate_approved = False
                    finally:
                        _pending_approvals.pop(tool_call_id, None)

                    if not gate_approved:
                        result_str = "用户取消买入判定(未执行 8 维分析)"
                        tool.set_response(
                            {"error": result_str, "cancelled": True},
                            is_error=True,
                        )
                        # 跳过下面的实际执行
                        llm_result = None
                        _skip_exec = True
                    else:
                        _skip_exec = False
                else:
                    _skip_exec = False

                if not _skip_exec:
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

        # 增量持久化:本轮已有 assistant 文本,回调通知外层保存(刷新后可恢复)
        if on_progress is not None and assistant_text_acc.strip():
            try:
                await on_progress(assistant_text_acc)
            except Exception:
                logger.debug("[Agent] on_progress save failed (non-fatal)", exc_info=True)

    logger.warning("[Agent] ReAct loop hit max iterations without final answer")
    controller.append_text("\n\n已完成多轮数据查询，正在生成最终总结...\n\n")
    return await _stream_final_answer_without_tools(controller, full_messages, llm_cfg, state=state)


@router.post("/agent/chat")
async def agent_chat(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """Chat endpoint using assistant-stream DataStream protocol.

    生成逻辑跑在独立后台 task (detach 于 HTTP 连接),首连接 attach 为第一个订阅者。
    断连不杀生成 —— 后端继续跑完落库,用户刷新后可通过 /agent/chat/resume 续流。
    """
    body = await request.json()
    messages = body.get("messages", [])
    conversation_id = body.get("conversation_id")
    try:
        llm_cfg = _get_llm_config()
    except AgentModelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session_service = ChatSessionService(db_manager)
    conversation = session_service.ensure_conversation(conversation_id)
    conv_id = conversation["id"]

    # 同一对话已有 run:默认拒绝并发发起;刷新恢复时前端会用
    # resume_existing=true 走同一个 /agent/chat data-stream 管道,这样 tool UI
    # 的解码与初次生成完全一致。即使 run 刚完成但仍在保留期内,也重放
    # chunk 历史,避免竞态下误开第二轮生成。
    active_run = active_run_registry.get(conv_id)
    if body.get("resume_existing") is True:
        try:
            replay_from = int(body.get("after_chunk_index") or 0)
        except (TypeError, ValueError):
            replay_from = 0
        if active_run is not None:
            logger.info(
                "[Agent] chat attach existing run for %s from chunk %s status=%s",
                conv_id,
                replay_from,
                active_run.status,
            )
            return DataStreamResponse(subscriber_stream(active_run, replay_from=replay_from))
        logger.info("[Agent] chat resume requested but no retained run for %s", conv_id)
        return JSONResponse(
            status_code=409,
            content={"error": "run_not_active", "conversation_id": conv_id},
        )

    if active_run_registry.is_active(conv_id):
        logger.info("[Agent] chat rejected: run already in progress for %s", conv_id)
        return JSONResponse(
            status_code=409,
            content={"error": "run_in_progress", "conversation_id": conv_id},
        )

    logger.info(
        f"[Agent] Chat request with {len(messages)} messages, "
        f"model={llm_cfg['model']}, conversation_id={conv_id}"
    )

    # 生成开始前同步落库本次完整 messages(含刚发的 user 消息)。
    # 这一步不能放在后台 task 里:用户一发送就刷新时,conversation detail 会
    # 先于后台 task 执行,如果库里还没有本次 user,前端只能恢复出空白历史。
    await asyncio.to_thread(
        session_service.save_conversation_snapshot,
        conv_id,
        list(messages),
        skip_title=True,
    )

    async def run_callback(controller: RunBroadcaster):
        from src.services.agent_prompt_service import AgentPromptService

        system_prompt, is_fallback = AgentPromptService(db_manager).get_active_system_prompt()
        logger.info(
            "[Agent] system prompt %s",
            "fallback to source default" if is_fallback else f"from template",
        )

        # 增量持久化:节流(>=3s 一次)把已生成 assistant 文本写库,刷新后可恢复
        last_save_ts = 0.0
        state: Dict[str, str] = {"assistant_text": ""}

        async def on_progress(assistant_text_so_far: str) -> None:
            nonlocal last_save_ts
            # 同步镜像到 broadcaster,供续流端点补齐已生成文本
            controller.assistant_text_snapshot = assistant_text_so_far
            now = asyncio.get_event_loop().time()
            if now - last_save_ts < 3.0:
                return
            last_save_ts = now
            await asyncio.to_thread(
                session_service.save_partial_assistant_text,
                conv_id,
                assistant_text_so_far,
            )

        final_response_text = ""
        try:
            final_response_text = await _run_react_loop(
                controller, messages, llm_cfg, system_prompt,
                on_progress=on_progress, state=state,
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
            session_service.save_conversation_snapshot(conv_id, persisted_messages)
            await active_run_registry.mark_done(
                conv_id, "completed", final_text=final_response_text
            )
        except asyncio.CancelledError:
            # 后台 task 不被 HTTP 断连取消,仅进程关闭/显式 cancel 会到这。
            # 把已生成文本落定,避免残留 {conv_id}-assistant-pending 半截消息。
            partial = state.get("assistant_text", "")
            if partial.strip():
                partial = partial.rstrip() + "\n\n[已停止]"
                try:
                    await asyncio.shield(asyncio.to_thread(
                        session_service.save_partial_assistant_text, conv_id, partial,
                    ))
                except asyncio.CancelledError:
                    try:
                        session_service.save_partial_assistant_text(conv_id, partial)
                    except Exception:
                        logger.warning("[Agent] cancel-time partial save failed", exc_info=True)
            await active_run_registry.mark_done(conv_id, "cancelled", final_text=partial)
            raise
        except Exception as exc:
            logger.exception("[Agent] background run failed")
            controller.add_error(str(exc))
            partial = state.get("assistant_text", "")
            if partial.strip():
                try:
                    await asyncio.to_thread(
                        session_service.save_partial_assistant_text, conv_id, partial
                    )
                except Exception:
                    logger.debug("[Agent] failed-run partial save failed", exc_info=True)
            await active_run_registry.mark_done(conv_id, "failed", error=str(exc))

    async def factory(broadcaster: RunBroadcaster) -> "asyncio.Task":
        return asyncio.create_task(run_callback(broadcaster))

    run = await active_run_registry.start_or_get(conv_id)
    # 首连接必须先 subscribe 再启动后台 task,否则 task 可能在首个订阅者
    # subscribe 之前就 emit 完所有 chunk,导致首连收不到内容。
    first_queue = run.broadcaster.subscribe()
    await run.start(factory)
    return DataStreamResponse(subscriber_stream(run, first_queue))


async def subscriber_stream(
    run: ActiveRun,
    queue: "asyncio.Queue | None" = None,
    *,
    replay_from: int | None = None,
):
    """首连/续流共用的订阅流:从指定 chunk 游标继续发送 data-stream。

    queue: 首连传入预先 subscribe 的 queue (确保在后台 task 启动前已订阅);
           续流留空,内部 subscribe。

    刷新恢复时,历史消息由 conversations detail 的 messages/resume_state 恢复;
    resume 只负责从 after_chunk_index 之后继续推增量。生成结束
    broadcaster.mark_finished 向 queue 投 None 哨兵,本 generator 自然结束。
    """
    broadcaster = run.broadcaster
    if queue is None:
        queue = broadcaster.subscribe(replay_from=replay_from)
    try:
        # 先消费游标之后的历史 chunk,再接后续实时 chunk,直到 None 哨兵。
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        broadcaster.unsubscribe(queue)


@router.post("/agent/chat/resume")
async def agent_chat_resume(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """续流端点:attach 到进行中的 run;无活跃 run 返回 {active: false}。"""
    body = await request.json()
    conversation_id = body.get("conversation_id")
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required")
    run = active_run_registry.get(conversation_id)
    if run is None or not run.is_running:
        return JSONResponse(status_code=200, content={"active": False})
    try:
        replay_from = int(body.get("after_chunk_index") or 0)
    except (TypeError, ValueError):
        replay_from = 0
    return DataStreamResponse(subscriber_stream(run, replay_from=replay_from))
