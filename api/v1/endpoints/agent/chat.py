# -*- coding: utf-8 -*-
"""
Agent Chat endpoint — policy-validated standard-task pipeline with
assistant-stream DataStreamResponse.

POST /api/v1/agent/chat

Body: { "messages": [ { "role": "user", "content": "贵州茅台行情" } ] }
Response: DataStreamResponse (line-delimited type-code:json chunks)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

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
    RunCapacityExceeded,
    active_run_registry,
)
from src.agent.runtime_safety import (
    AgentRequestValidationError,
    agent_request_rate_limiter,
    get_agent_runtime_limits,
    validate_chat_request_body,
)
from src.agent.progress import strip_agent_progress
from src.agent.conversation_context import ConversationContext, build_turn_reference
from src.agent.result_contracts import (
    AnalysisPlaybook,
    CollectionFinancialFilterSpec,
    INDUSTRY_CHAIN,
    INVESTMENT_DECISION,
    MappingSelectionContext,
    STOCK_DEEP_RESEARCH,
    THEME_COMPANY_MAPPING,
)
from src.agent.evidence_facts import BoundEvidenceFact
from src.agent.task_executor import PlanExecutionResult, WorkflowExecutor
from src.agent.task_planner import (
    TaskPlanValidationError,
    TaskPlannerUnavailableError,
    resolve_plan_entities,
    resolve_task_plan,
)
from src.agent.task_workflows import (
    StandardTaskKind,
    TaskPlan,
    WorkflowCall,
    workflow_for,
)
from src.tools.registry import ToolRegistry
from src.tools.process_runner import (
    ISOLATED_TOOL_NAMES,
    STATEFUL_TOOL_NAMES,
    execute_tool_isolated,
)
from src.llm.anthropic_gateway import (
    AnthropicGatewayConfigError,
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
)
from src.services.chat_session_service import ChatSessionService
from src.services.buy_criteria.professional_analysis import DIMENSION_DEFINITIONS
from src.storage import DatabaseManager
from src.auth import get_client_ip
from src.tools.symbols import (
    find_securities_in_text,
    normalize_tool_security_arguments,
)

logger = logging.getLogger(__name__)

TOOL_EXECUTION_TIMEOUT_SECONDS = 45.0
PROFESSIONAL_EVIDENCE_TIMEOUT_SECONDS = 90.0
PROFESSIONAL_BUY_ANALYSIS_TIMEOUT_SECONDS = 2400.0
CATALYST_ANALYSIS_TIMEOUT_SECONDS = 180.0
QUANTITATIVE_SCREEN_TIMEOUT_SECONDS = 210.0
FINAL_SYNTHESIS_TIMEOUT_SECONDS = 60.0
STANDARD_TASK_PLAN_TIMEOUT_SECONDS = 310.0

# controller 产出层:当前生产路径走自建后台运行时的 RunBroadcaster (方法名与
# assistant-stream 的 RunController 对齐:append_text/add_tool_call/add_data/
# append_reasoning,以及 _stream_tasks 属性),不再依赖 create_run 的单连接生命周期。
ControllerLike = RunBroadcaster

_registry = ToolRegistry()

SYSTEM_PROMPT = """\
你是可靠的通用 AI 助手和 A 股研究写作助手。任务拆分、工具权限、执行顺序、并发、
确认与交易安全均由程序控制；你只负责根据已经执行完成的标准任务及其证据撰写最终答案，
不得重新规划、选择工具或声称执行了证据中没有的步骤。

## 回答原则
1. 普通知识、写作、解释和计算按用户原意回答；股票问题只覆盖用户本轮明确要求的范围。
2. 价格、行情、财务、估值、预测、资金流、新闻、公告、宏观数字和证券身份，只能使用本轮
   success=true 的证据。没有证据就明确写“缺失”，不得依靠记忆补数字、代码或公司事实。
3. 区分事实、计算结果、机构预测、媒体报道和推断；说明日期、报告期、来源、数据陈旧或降级。
4. 同时呈现支持证据与反证/风险。投资分析给出成立条件、失效条件和跟踪指标，不输出虚假精确目标价。
5. 多任务结果按依赖关系合并；失败或被阻止的任务明确写出，不得把部分成功包装成全部完成。
6. 使用中文和紧凑 Markdown，先给直接结论，再给关键证据和必要风险。不要复述内部任务计划、
   工具名、调用过程或“接下来我会”等过程旁白。
"""

def _strip_progress_markers(text: str) -> str:
    """Remove UI-only progress copy before conversation history returns to the LLM."""
    return strip_agent_progress(text)


def _last_user_text(messages: List[Dict[str, Any]]) -> str:
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return _join_text_parts(content)
    return ""


def _legacy_previous_answer_entities(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Format-agnostic compatibility for conversations without structured state."""
    for message in reversed(messages[:-1]):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else _join_text_parts(content or [])
        return find_securities_in_text(text, limit=300)
    return []


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
# 格式（content 为 part 数组），后端标准任务流水线与 litellm/anthropic 用的是
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
    content_text = _strip_progress_markers(_join_text_parts(parts))
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
    由本轮 Workflow Executor 直接持有（保完整）。
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
                normalized_content = content
                if role == "assistant" and isinstance(content, str):
                    normalized_content = _strip_progress_markers(content)
                normalized.append({
                    "role": role or "user",
                    **{
                        k: (normalized_content if k == "content" else v)
                        for k, v in raw.items()
                        if k != "role"
                    },
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


def _build_synthesis_messages(
    messages: List[Dict[str, Any]],
    evidence: Optional[List[Dict[str, Any]]] = None,
    playbook: Optional[AnalysisPlaybook] = None,
) -> List[Dict[str, Any]]:
    """Build a text-only transcript for the no-tool final synthesis pass.

    Anthropic rejects a request that contains historical ``tool_use`` messages
    when the request omits ``tools``.  The final pass intentionally has no tool
    access, so tool calls/results are converted into one explicit evidence
    packet while normal conversational context remains intact.
    """
    system_parts: List[str] = []
    dialogue: List[Dict[str, str]] = []
    inferred_evidence: List[Dict[str, Any]] = []
    tool_names_by_id: Dict[str, str] = {}

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip()
        if role == "system":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                system_parts.append(content.strip())
            continue
        if role == "assistant" and msg.get("tool_calls"):
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                tool_names_by_id[str(call.get("id") or "")] = str(fn.get("name") or "")
            # Planning prose produced alongside tool calls is internal work, not
            # an answer that should be repeated in the final synthesis.
            continue
        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            inferred_evidence.append({
                "tool": tool_names_by_id.get(call_id) or "unknown_tool",
                "result": msg.get("content"),
            })
            continue
        if role not in {"user", "assistant"}:
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        cleaned = _strip_progress_markers(content) if role == "assistant" else content.strip()
        if cleaned:
            dialogue.append({"role": role, "content": cleaned})

    synthesis_instruction = (
        "你现在处于最终写作阶段，不能再调用工具。请只依据对话与下方工具证据，"
        "直接完成用户当前问题。不要描述检索过程，不要补造证据中没有的事实。"
        "所有价格、涨跌、估值、财务和技术指标必须逐项存在于 success=true 的本轮工具结果中；"
        "即使是常识或记忆中的历史数字，只要本轮证据没有提供就必须省略，禁止写‘历史约为’；"
        "失败、超时、未解析证券或陈旧数据不得被改写成成功事实。"
        "禁止新增未来披露日期、行业阶段或板块整体走势，除非本轮工具证据明确给出。"
        "若不同来源冲突，明确指出冲突；若证据不足，缩小结论并说明缺口。"
        "投资类问题给条件化判断和风险边界，不给脱离期限与风险承受能力的确定性买卖指令。"
        "交易时段内的实时行情只能称为盘中快照或最新价，禁止写成收盘价；"
        "多公司对比表必须同时列出已核验的公司名称和证券代码。"
    )
    if playbook is None:
        synthesis_instruction += (
            "只覆盖用户明确要求的维度；用户没问技术面时，不要添加 RSI、MACD、均线等技术段落。"
            "答案以高信息密度为准：多公司普通初筛严格控制在 900 个汉字以内；先用一张最多五列的紧凑表"
            "完整列完全部公司（公司/代码、关键数据、判断、触发条件），再写至多三条共性结论与风险。"
            "不要逐家公司重复基本面段落，不要复制工具返回的全部字段。必须留足篇幅用完整句子收尾。"
        )
    else:
        synthesis_instruction += (
            "\n\n" + playbook.system_instruction()
            + "\n最终回答必须证明已覆盖上述每个证据维度和输出项；"
            "先用已取得的证据回答用户真正的问题。补证后仍缺少的关键维度只在结尾集中说明一次，"
            "禁止逐段、逐行重复‘证据缺失’，也禁止用大篇幅缺口清单代替结论。"
        )
        if playbook.id == INDUSTRY_CHAIN.id:
            synthesis_instruction += (
                "产业链回答控制在约 2600 个汉字内：优先级、重点环节、反证、跟踪指标和置信度都必须完成后再停止；"
                "不要为每个环节复制同一套大表，也不要输出独立的数据缺口表。"
            )
    system_text = "\n\n".join(system_parts) or SYSTEM_PROMPT
    result: List[Dict[str, Any]] = [
        {"role": "system", "content": f"{system_text}\n\n{synthesis_instruction}"},
        *dialogue,
    ]
    evidence_packet = list(evidence if evidence is not None else inferred_evidence)
    if playbook is not None and playbook.id == THEME_COMPANY_MAPPING.id and evidence_packet:
        # Discovery tools identify candidate names, but final synthesis has no
        # tool access. Resolve every name appearing in this evidence against
        # the local security master before the model writes the table, so it
        # can never guess a code or leave ``代码待核验`` in a deliverable.
        candidate_text = json.dumps(evidence_packet, ensure_ascii=False, default=str)
        candidate_entities = find_securities_in_text(candidate_text, limit=60)
        evidence_packet.append({
            "tool": "runtime_security_entity_map",
            "result": {
                "success": True,
                "resolved_entities": candidate_entities,
                "instruction": (
                    "公司/代码只能从本表选择；未出现在本表中的候选公司不得列入最终公司表。"
                ),
            },
        })
    if evidence_packet:
        result.append({
            "role": "user",
            "content": (
                "[本轮已核验的工具证据]\n"
                + json.dumps(evidence_packet, ensure_ascii=False, default=str)
            ),
        })
    return result


def _build_professional_decision_fallback(
    batch: Dict[str, Any],
    *,
    decision_requested: Optional[bool] = None,
) -> str:
    """Render a complete seven-dimension decision when model synthesis fails."""

    if decision_requested is None:
        decision_requested = False
    heading = "专业买入决策结论" if decision_requested else "综合研究结论"
    intro = (
        "本轮按七个维度逐家公司完成介入条件检查。"
        if decision_requested
        else "本轮按七个维度逐家公司完成基本面与风险研究。"
    )

    def number(value: Any, digits: int = 2, suffix: str = "") -> str:
        try:
            return f"{float(value):,.{digits}f}{suffix}"
        except (TypeError, ValueError):
            return "缺失"

    def short(value: Any, limit: int = 34) -> str:
        text = str(value or "证据缺失").strip()
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def amount_yi(value: Any) -> str:
        try:
            return f"{float(value) / 100000000:,.2f}亿"
        except (TypeError, ValueError):
            return "缺失"

    rows: List[str] = []
    details: List[str] = []
    priority_groups: Dict[str, List[str]] = {}
    for item in batch.get("items") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "—")
        name = str(item.get("name") or symbol)
        snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), dict) else {}
        quote = snapshot.get("quote") if isinstance(snapshot.get("quote"), dict) else {}
        technical = snapshot.get("technical") if isinstance(snapshot.get("technical"), dict) else {}
        indicators = technical.get("indicators") if isinstance(technical.get("indicators"), dict) else {}
        financials = item.get("financials") if isinstance(item.get("financials"), dict) else {}
        periods = financials.get("items") if isinstance(financials.get("items"), list) else []
        latest = periods[-1] if periods and isinstance(periods[-1], dict) else {}
        valuation = item.get("valuation") if isinstance(item.get("valuation"), dict) else {}
        consensus = item.get("consensus") if isinstance(item.get("consensus"), dict) else {}
        estimates = consensus.get("estimates") if isinstance(consensus.get("estimates"), list) else []
        first_estimate = estimates[0] if estimates and isinstance(estimates[0], dict) else {}
        capital = item.get("capital_flow") if isinstance(item.get("capital_flow"), dict) else {}
        flow_10d = ((capital.get("windows") or {}).get("10d") or {}) if isinstance(capital.get("windows"), dict) else {}
        risks = item.get("risk_events") if isinstance(item.get("risk_events"), dict) else {}
        risk_analysis = risks.get("analysis") if isinstance(risks.get("analysis"), dict) else {}
        coverage = item.get("evidence_coverage") if isinstance(item.get("evidence_coverage"), dict) else {}
        profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
        segments = item.get("business_segments") if isinstance(item.get("business_segments"), dict) else {}
        segment_names = [
            str(segment.get("segment_name"))
            for segment in (segments.get("items") or [])[:3]
            if isinstance(segment, dict) and segment.get("segment_name")
        ]

        negative_profit = latest.get("parent_net_profit") is not None and latest.get("parent_net_profit") <= 0
        high_debt = (latest.get("debt_ratio") or 0) >= 80
        high_risk = (risk_analysis.get("active_high_severity_count") or 0) > 0
        weak_growth = latest.get("revenue_yoy") is not None and latest.get("revenue_yoy") < 0
        pe_ttm = valuation.get("pe_ttm")
        industry_pe = (valuation.get("industry_average") or {}).get("pe") if isinstance(valuation.get("industry_average"), dict) else None
        expensive = bool(pe_ttm and industry_pe and pe_ttm > industry_pe * 1.5)
        trend_weak = indicators.get("return_20d_pct") is not None and indicators.get("return_20d_pct") <= -15
        flow_weak = flow_10d.get("main_net_inflow") is not None and flow_10d.get("main_net_inflow") < 0

        if not coverage.get("complete"):
            verdict = "数据源异常（证据链未完成）"
        elif negative_profit or high_debt or high_risk:
            verdict = "风险规避"
        elif expensive and weak_growth:
            verdict = "暂不买入"
        elif expensive or trend_weak or flow_weak:
            verdict = "暂不介入（条件未满足）"
        else:
            verdict = "可研究候选"
        priority_groups.setdefault(verdict, []).append(f"{name} ({symbol})")

        business = short("、".join(segment_names) or profile.get("main_business"), 28)
        financial = (
            f"收入YoY {number(latest.get('revenue_yoy'), 1, '%')}；"
            f"净利YoY {number(latest.get('parent_net_profit_yoy'), 1, '%')}；"
            f"OCF {amount_yi(latest.get('operating_cash_flow'))}"
        )
        expectation = (
            "机构一致预期覆盖0家（查询完成）"
            if consensus.get("coverage_status") == "no_sell_side_coverage"
            else "一致预期来源异常"
        )
        if first_estimate:
            np_forecast = first_estimate.get("net_profit") if isinstance(first_estimate.get("net_profit"), dict) else {}
            expectation = (
                f"{first_estimate.get('year', '—')}净利均值"
                f"{number(np_forecast.get('mean'), 2, '亿')}({first_estimate.get('coverage_count', 0)}家)"
            )
        valuation_text = (
            f"PE(TTM) {number(pe_ttm)}；PB {number(valuation.get('pb_mrq'))}；"
            f"远期PEG {number(valuation.get('peg_forward'))}；{expectation}"
        )
        trading = (
            f"20日 {number(indicators.get('return_20d_pct'), 1, '%')}；"
            f"10日资金 {amount_yi(flow_10d.get('main_net_inflow'))}"
        )
        risk_text = (
            f"规则风险{risks.get('item_count', len(risks.get('items') or []))}项；"
            f"公告覆盖至{(item.get('announcements') or {}).get('data_time') or '缺失'}"
        )
        rows.append(
            f"| {name} ({symbol}) | {business} | {financial} | {valuation_text} | "
            f"{trading} | {risk_text} | **{verdict}** |"
        )

        flags = item.get("screening_flags") if isinstance(item.get("screening_flags"), dict) else {}
        positives = "、".join(str(value) for value in flags.get("positive") or []) or "无明确正面信号"
        negatives = "、".join(str(value) for value in flags.get("negative") or []) or "未识别硬性负面信号"
        missing = "、".join(str(value) for value in coverage.get("missing") or []) or "无"
        details.append(
            f"- **{name} ({symbol})**：支持证据：{positives}。反证/风险：{negatives}。"
            f"数据源执行缺口：{missing}。主题业务订单/收入仍需公司级原文持续核验。"
            "成立条件：主营相关订单或收入可核验、盈利与估值匹配、"
            "交易状态止跌；失效条件：业绩/现金流继续恶化或风险事件升级。"
        )

    priority_order = (
        "可研究候选",
        "暂不介入（条件未满足）",
        "暂不买入",
        "风险规避",
        "数据源异常（证据链未完成）",
    )
    priority_lines = [
        f"- **{verdict}**：{'、'.join(priority_groups[verdict])}"
        for verdict in priority_order
        if priority_groups.get(verdict)
    ]

    return (
        f"## {heading}\n\n"
        f"{intro}结论是研究分层，不是收益承诺。\n\n"
        "| 公司/代码 | 主营与兑现基础 | 财务质量 | 估值与预期 | 交易与资金 | 公告/风险 | 当前结论 |\n"
        "|---|---|---|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n### 逐家公司成立条件与反证\n\n"
        + "\n".join(details)
        + "\n\n### 横向优先级\n\n"
        + ("\n".join(priority_lines) if priority_lines else "本轮没有可排序的公司证据。")
        + "\n\n### 组合层面的共同边界\n\n"
        "- 业务关联必须以财报收入、正式订单或客户验证为准；主营构成只能证明业务基础，不能自动证明主题收入。\n"
        "- 高估值不会单独终止分析，但需要更高的盈利增速和订单兑现来消化；资金流是成交单大小口径，不是机构持仓。\n"
        "- 数据口径："
        + str(batch.get("data_time") or "时间缺失")
        + "，"
        + str(batch.get("quote_basis") or "行情口径缺失")
        + "。完整来源包括公司资料、财报、主营构成、估值、一致预期、同行、公告、风险和资金流。"
    )


def _build_professional_buy_decision_answer(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """Render the validated eight-dimension analyst result without rewriting it."""
    packets = [
        packet
        for packet in evidence or []
        if isinstance(packet, dict)
        and packet.get("tool") == "evaluate_multi_stock_buy_criteria"
    ]
    if not packets:
        return None

    requested: List[str] = []
    items_by_code: Dict[str, Dict[str, Any]] = {}
    batch_errors: List[str] = []
    data_times: List[str] = []
    for packet in packets:
        arguments = packet.get("arguments") if isinstance(packet.get("arguments"), dict) else {}
        for code in re.split(r"[,，、;；]+", str(arguments.get("symbols") or "")):
            code = code.strip()
            if re.fullmatch(r"\d{6}", code) and code not in requested:
                requested.append(code)
        result = packet.get("result")
        if not isinstance(result, dict):
            continue
        batch_errors.extend(str(error) for error in result.get("errors") or [] if error)
        if result.get("data_time"):
            data_times.append(str(result["data_time"]))
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("symbol") or "").strip()
            if re.fullmatch(r"\d{6}", code):
                items_by_code[code] = item

    if not requested:
        requested = list(items_by_code)
    if not requested:
        return (
            "## 专业买入分析未完成\n\n"
            "本轮没有取得通过本地证券库核验的股票代码，因此没有输出买入结论。"
        )

    status_meta = {
        "pass": ("✅", "可以打勾"),
        "partial": ("◐", "只能半打勾"),
        "fail": ("❌", "不能打勾"),
        "insufficient": ("?", "关键取证未完成，暂不打勾"),
    }
    dimension_titles = dict(DIMENSION_DEFINITIONS)

    def clean_inline(value: Any, limit: int = 240) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        return text[:limit]

    def markdown_link(source: Dict[str, Any]) -> str:
        url = str(source.get("url") or "").strip()
        if not re.match(r"^https?://", url, re.I):
            return ""
        title = clean_inline(
            source.get("title") or source.get("source") or "原始资料",
            80,
        ).replace("[", "［").replace("]", "］")
        date = clean_inline(source.get("date"), 24)
        suffix = f"（{date}）" if date else ""
        return f"[{title}]({url}){suffix}"

    def normalized_dimensions(item: Dict[str, Any]) -> List[Dict[str, Any]]:
        by_id = {
            str(value.get("dimension_id") or ""): value
            for value in item.get("dimensions") or []
            if isinstance(value, dict)
        }
        normalized: List[Dict[str, Any]] = []
        for dimension_id, title in DIMENSION_DEFINITIONS:
            value = by_id.get(dimension_id)
            if isinstance(value, dict):
                normalized.append(value)
            else:
                normalized.append({
                    "dimension_id": dimension_id,
                    "status": "insufficient",
                    "headline": "该维度没有返回有效分析",
                    "analysis": "程序未取得这一维度的结构化判断，不能用其他维度代替。",
                    "key_evidence": [],
                    "counter_evidence": [],
                    "monitoring_points": [f"重新核验“{title}”"],
                })
        return normalized

    def item_statistics(item: Dict[str, Any]) -> tuple[Dict[str, int], float]:
        dimensions = normalized_dimensions(item)
        counts = {
            status: sum(
                1 for dimension in dimensions
                if str(dimension.get("status") or "") == status
            )
            for status in status_meta
        }
        return counts, round(counts["pass"] + counts["partial"] * 0.5, 1)

    missing = [code for code in requested if code not in items_by_code]
    overview_rows: List[str] = []
    reports: List[str] = []
    for code in requested:
        item = items_by_code.get(code)
        if not isinstance(item, dict):
            overview_rows.append(
                f"| {code} | ? 8项 | 0/8 | **关键取证未完成，暂停判断** | 结果未返回 |"
            )
            continue
        name = str(item.get("name") or code)
        counts, score = item_statistics(item)
        status_text = (
            f"✅{counts['pass']} / ◐{counts['partial']} / "
            f"❌{counts['fail']} / ?{counts['insufficient']}"
        )
        overview_rows.append(
            f"| {name} ({code}) | {status_text} | {score:g}/8 | "
            f"**{clean_inline(item.get('recommendation') or '关键取证未完成，暂停判断', 40)}** | "
            f"{clean_inline(item.get('biggest_issue') or '未给出', 90)} |"
        )

        as_of = str(item.get("data_time") or "").strip()
        quote_basis = clean_inline(item.get("quote_basis"), 80)
        basis_text = "；".join(value for value in (as_of, quote_basis) if value) or "数据时间未完整返回"
        count_line = (
            f"**✅ {counts['pass']}项｜◐ {counts['partial']}项｜"
            f"❌ {counts['fail']}项"
            + (f"｜? {counts['insufficient']}项" if counts["insufficient"] else "")
            + f"，折算约 {score:g}/8。**"
        )
        sections = [
            f"## {name} ({code})",
            "",
            "### 结论先行",
            "",
            f"截至 **{basis_text}**，{name}的检查结果是：",
            "",
            count_line,
            "",
            str(item.get("overall_summary") or "本轮没有形成完整结论。").strip(),
            "",
            f"- **当前定位**：{item.get('investment_profile') or '待验证'}",
            f"- **当前判断**：{item.get('recommendation') or '关键取证未完成，暂停判断'}。"
            f"{item.get('recommendation_reason') or ''}",
            f"- **最大问题**：{item.get('biggest_issue') or '尚未识别'}",
        ]

        for index, dimension in enumerate(normalized_dimensions(item), 1):
            dimension_id = str(dimension.get("dimension_id") or "")
            status = str(dimension.get("status") or "insufficient")
            icon, label = status_meta.get(status, status_meta["insufficient"])
            sections.extend([
                "",
                f"### {index}. {icon} {dimension_titles.get(dimension_id, dimension_id)}",
                "",
                f"**{label}。{clean_inline(dimension.get('headline') or '', 120)}**",
                "",
                str(dimension.get("analysis") or "该维度没有返回有效分析。").strip(),
            ])
            key_evidence = [
                clean_inline(value, 320)
                for value in dimension.get("key_evidence") or []
                if clean_inline(value, 320)
            ]
            counter_evidence = [
                clean_inline(value, 320)
                for value in dimension.get("counter_evidence") or []
                if clean_inline(value, 320)
            ]
            monitoring = [
                clean_inline(value, 260)
                for value in dimension.get("monitoring_points") or []
                if clean_inline(value, 260)
            ]
            if key_evidence:
                sections.extend(["", "**关键证据：**", ""])
                sections.extend(f"- {value}" for value in key_evidence)
            if counter_evidence:
                sections.extend(["", "**主要反证：**", ""])
                sections.extend(f"- {value}" for value in counter_evidence)
            if monitoring:
                sections.extend(["", "**这一项后续看什么：**", ""])
                sections.extend(f"- {value}" for value in monitoring)

        sections.extend([
            "",
            "### 最终判断",
            "",
            f"**{item.get('recommendation') or '关键取证未完成，暂停判断'}。**"
            f"{item.get('recommendation_reason') or ''}",
            "",
            f"- **核心逻辑**：{item.get('core_thesis') or '待验证'}",
            f"- **看多链条**：{item.get('bull_case_chain') or '待验证'}",
            f"- **风险链条**：{item.get('risk_chain') or '待验证'}",
            "",
            "后续最关键的验证指标是：",
            "",
        ])
        monitoring_points = [
            clean_inline(value, 320)
            for value in item.get("monitoring_points") or []
            if clean_inline(value, 320)
        ]
        sections.extend(
            f"{index}. **{value}**"
            for index, value in enumerate(monitoring_points[:6], 1)
        )

        source_links = [
            markdown_link(source)
            for source in item.get("source_links") or []
            if isinstance(source, dict)
        ]
        source_links = [value for value in source_links if value]
        if source_links:
            sections.extend(["", "### 主要原始来源", ""])
            sections.extend(f"- {value}" for value in source_links[:12])
        evidence_gaps = [
            clean_inline(value, 320)
            for value in item.get("evidence_gaps") or []
            if clean_inline(value, 320)
        ]
        if evidence_gaps:
            sections.extend([
                "",
                "### 证据边界",
                "",
                "以下信息仍需补证，不能被理解为已经确认：",
                "",
            ])
            sections.extend(f"- {value}" for value in evidence_gaps[:8])
        reports.append("\n".join(sections))

    coverage_lines = [
        f"- 请求 **{len(requested)} 只**，返回 **{len(items_by_code)} 只**，缺失 **{len(missing)} 只**。",
        "- 八项均完整分析，不因某一项不通过而停止后续维度。",
        "- 计分口径：通过1分、半通过0.5分、不通过或取证未完成0分；分数用于表达证据强弱，不是收益预测。",
    ]
    if missing:
        coverage_lines.append("- 未返回代码：" + "、".join(missing) + "。")
    if batch_errors:
        coverage_lines.append("- 执行异常：" + "；".join(dict.fromkeys(batch_errors)) + "。")
    if data_times:
        coverage_lines.append("- 集合汇总时间：" + max(data_times) + "。")

    if len(requested) == 1 and reports:
        return reports[0] + "\n\n### 覆盖与口径\n\n" + "\n".join(coverage_lines)
    return (
        "## 专业买入分析总览\n\n"
        "| 公司/代码 | 八项结果 | 得分 | 当前判断 | 最大问题 |\n"
        "|---|---|---:|---|---|\n"
        + "\n".join(overview_rows)
        + "\n\n"
        + "\n\n---\n\n".join(reports)
        + "\n\n## 覆盖与口径\n\n"
        + "\n".join(coverage_lines)
    )


def _build_theme_mapping_fallback(
    result: Dict[str, Any],
    evidence: Optional[List[Dict[str, Any]]] = None,
    semantic_facts: Optional[List[BoundEvidenceFact]] = None,
    semantic_intent: Optional[MappingSelectionContext] = None,
) -> str:
    """Render only semantically bound company facts and the structured recall set."""
    if semantic_facts is None:
        return "公司级证据语义校验未完成，本轮不使用文本关键词替代判断。"

    selected: List[Dict[str, Any]] = []
    boundaries: List[Dict[str, Any]] = []
    for fact_model in semantic_facts:
        fact = (
            fact_model.model_dump()
            if isinstance(fact_model, BoundEvidenceFact)
            else dict(fact_model)
        )
        symbol = str(fact.get("symbol") or "").strip()
        name = str(fact.get("company_name") or symbol).strip()
        stage = str(fact.get("stage") or "").strip()
        if not symbol or not name or stage == "L1":
            continue
        if (
            semantic_intent is not None
            and semantic_intent.selection_mode == "ranked_shortlist"
            and fact.get("thesis_fit") != "exact"
        ):
            continue
        row = {
            "symbol": symbol,
            "name": name,
            "relationship": str(fact.get("relationship") or "相关业务"),
            "stage": stage,
            "fact": str(fact.get("fact") or fact.get("support_quote") or "").replace("|", "／")[:180],
            "source_name": str(fact.get("source_name") or "公开资料"),
            "source_url": str(fact.get("source_url") or ""),
            "source_date": str(fact.get("source_date") or "日期缺失"),
        }
        (boundaries if stage == "boundary" else selected).append(row)

    candidates = [
        item for item in result.get("items") or []
        if isinstance(item, dict)
        and re.fullmatch(r"\d{6}", str(item.get("symbol") or ""))
        and str(item.get("name") or "").strip()
    ]
    if not candidates:
        return "主题候选池未取得本地证券库交叉结果，本轮不能可靠生成公司名单。"

    def render_fact_table(rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return "无。"
        lines = [
            "| 公司/代码 | 对应关系 | 证据等级 | 已验证事实 | 来源日期 |",
            "|---|---|---|---|---|",
        ]
        for row in rows:
            source = (
                f"[{row['source_name']}]({row['source_url']})"
                if row["source_url"] else row["source_name"]
            )
            level = "反证" if row["stage"] == "boundary" else row["stage"]
            lines.append(
                f"| {row['name']} ({row['symbol']}) | {row['relationship']} | {level} | "
                f"{row['fact'] or '事实摘要缺失'} | {source}，{row['source_date']} |"
            )
        return "\n".join(lines)

    grouped: Dict[str, List[str]] = {}
    for item in candidates:
        boards = [str(value) for value in item.get("boards") or [] if value]
        group = boards[0] if boards else "其他结构化候选"
        grouped.setdefault(group, []).append(
            f"{item.get('name')} ({item.get('symbol')})"
        )
    inventory_lines: List[str] = []
    for group, entries in grouped.items():
        for index in range(0, len(entries), 20):
            label = group if index == 0 else f"{group}（续）"
            inventory_lines.append(f"- **{label}**：{'、'.join(entries[index:index + 20])}")

    requirements = ""
    if semantic_intent is not None:
        requirements = "、".join(semantic_intent.thesis_requirements)
    is_ranked_shortlist = bool(
        semantic_intent is not None
        and semantic_intent.selection_mode == "ranked_shortlist"
    )
    heading = (
        "## 与投资命题精确匹配的 A 股短名单"
        if is_ranked_shortlist else "## 产业主题 A 股公司级证据"
    )
    scope_line = f"> 结构化候选池共 **{len(candidates)} 家**。"
    if requirements:
        scope_line += f" 公司级纳入条件：{requirements}。"

    inventory_section = ""
    if not is_ranked_shortlist:
        inventory_section = (
            f"\n\n### 结构化候选池（L1，共 {len(candidates)} 家）\n\n"
            "> L1 只证明结构化板块成员关系和证券身份，不代表订单、收入或买入建议。\n\n"
            + "\n".join(inventory_lines)
        )
    else:
        inventory_section = (
            "\n\n> 排名只采用与命题精确匹配的公司级事实；概念成员关系不参与最终排名。"
        )

    return (
        heading
        + "\n\n"
        + scope_line
        + "\n\n### 已核验正向事实\n\n"
        + render_fact_table(selected)
        + "\n\n### 已核验反证与边界\n\n"
        + render_fact_table(boundaries)
        + inventory_section
        + "\n\n> 公司级事实仅采用语义证据绑定器的结构化结果；未使用关键词扫描升级证据等级。"
    )


def _template_purpose(content: Any) -> str:
    headings = []
    for match in re.findall(r"^###\s+(?:\d+[.、]?\s*)?(.+?)\s*$", str(content or ""), re.MULTILINE):
        heading = match.strip()
        if heading and heading not in headings and "输出" not in heading:
            headings.append(heading)
    if headings:
        return "覆盖" + "、".join(headings[:6])
    return "自定义股票分析框架"


def _build_workflow_evidence_fallback(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """Render successful workflow reads/actions without another model pass."""
    workflow_names = {
        "filter_watchlist_by_theme",
        "manage_watchlist",
        "manage_watchlist_groups",
        "run_stock_analysis",
        "get_analysis_status",
        "search_analysis_history",
        "delete_analysis_history",
        "manage_analysis_templates",
        "run_batch_analysis",
        "manage_batch_run",
        "manage_analysis_schedule",
        "get_notification_status",
        "send_notification",
    }
    for item in reversed(evidence or []):
        if not isinstance(item, dict) or item.get("tool") not in workflow_names:
            continue
        tool_name = str(item.get("tool"))
        result = item.get("result")
        if not isinstance(result, dict) or result.get("success") is False:
            continue

        if tool_name == "filter_watchlist_by_theme":
            items = result.get("items") if isinstance(result.get("items"), list) else []
            group = result.get("group") if isinstance(result.get("group"), dict) else {}
            themes = "、".join(str(theme) for theme in result.get("requested_themes") or []) or "指定主题"
            lines = [
                f"在 **{group.get('name') or '我的自选股'}** 的 "
                f"**{group.get('valid_security_count', group.get('count', 0))} 只有效证券**中，",
                f"按 **{themes}** 主题板块成员关系筛出 **{len(items)} 只**：\n",
            ]
            if items:
                lines.extend([
                    "| 公司/代码 | 匹配主题 | 主题板块 |",
                    "|---|---|---|",
                ])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "未命名")
                    symbol = str(item.get("symbol") or "—")
                    matched = "、".join(str(value) for value in item.get("matched_themes") or []) or "—"
                    boards = "、".join(str(value) for value in item.get("boards") or []) or "—"
                    lines.append(f"| {name}（{symbol}） | {matched} | {boards} |")
            else:
                lines.append("没有找到与这些主题板块相交的自选股。")
            invalid_entries = [str(value) for value in result.get("invalid_entries") or []]
            lines.append(
                "\n> 口径：以上均为 **L1 主题板块成员关系**，不等同已形成相关订单或收入。"
                "如需核验真实业务关联，请继续指定公司。"
            )
            if invalid_entries:
                lines.append("\n已忽略无法识别的自选条目：" + "、".join(invalid_entries))
            return "\n".join(lines)

        if tool_name == "manage_watchlist":
            codes = [str(code) for code in result.get("codes") or []]
            if result.get("action") == "list":
                return (
                    f"当前默认自选股共有 **{len(codes)} 只**："
                    + ("\n\n" + "、".join(codes) if codes else "列表为空。")
                )
            changed = [str(code) for code in result.get("changed") or []]
            verb = "添加" if result.get("action") == "add" else "移除"
            return f"已{verb} **{len(changed)} 只**股票：" + ("、".join(changed) if changed else "没有发生变化。")

        if tool_name == "manage_watchlist_groups":
            if result.get("action") == "list":
                groups = result.get("groups") if isinstance(result.get("groups"), list) else []
                if not groups:
                    return "当前没有自选分组。"
                lines = [
                    f"当前共有 **{len(groups)} 个自选分组**：\n",
                    "| 分组 | 类型 | 股票数量 | 成员 |",
                    "|---|---|---:|---|",
                ]
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    codes = [str(code) for code in group.get("codes") or []]
                    preview = "、".join(codes[:8]) or "—"
                    if len(codes) > 8:
                        preview += f" 等 {len(codes)} 只"
                    lines.append(
                        f"| {group.get('name') or '未命名'} | "
                        f"{'默认' if group.get('is_default') else '自定义'} | "
                        f"{group.get('count', len(codes))} | {preview} |"
                    )
                return "\n".join(lines)
            message = str(result.get("message") or "").strip()
            if message:
                return message

        if tool_name == "manage_analysis_templates":
            action = result.get("action")
            templates = result.get("items") if isinstance(result.get("items"), list) else []
            if action == "list":
                if not templates:
                    return "当前没有分析模板。你可以告诉我模板名称和分析框架，我会在确认后创建。"
                lines = [
                    f"当前共有 **{len(templates)} 个分析模板**：\n",
                    "| 模板 | 状态 | 用途 |",
                    "|---|---|---|",
                ]
                default_name = ""
                for template in templates:
                    if not isinstance(template, dict):
                        continue
                    name = str(template.get("name") or "未命名模板")
                    is_default = bool(template.get("is_default"))
                    if is_default:
                        default_name = name
                    lines.append(
                        f"| {name} | {'默认' if is_default else '可选'} | "
                        f"{_template_purpose(template.get('content'))} |"
                    )
                if default_name:
                    lines.append(f"\n当前默认模板是 **{default_name}**。发起正式分析时未指定模板，就会使用它。")
                return "\n".join(lines)

            template = result.get("template")
            if isinstance(template, dict):
                name = str(template.get("name") or "未命名模板")
                status = "默认模板" if template.get("is_default") else "可选模板"
                content = str(template.get("content") or "").strip()
                answer = f"## {name}\n\n- 状态：{status}\n- 用途：{_template_purpose(content)}"
                if action == "get" and content:
                    answer += f"\n\n### 模板内容\n\n{content}"
                else:
                    answer += "\n\n模板操作已完成。"
                return answer
            if result.get("deleted"):
                return "分析模板已删除。"

        if tool_name == "search_analysis_history":
            records = result.get("items") if isinstance(result.get("items"), list) else []
            if not records:
                return "没有找到符合条件的正式分析报告。"
            lines = [
                f"找到 **{result.get('total', len(records))} 份**正式分析报告，本页显示 {len(records)} 份：\n",
                "| ID | 股票 | 报告时间 | 类型 |",
                "|---|---|---|---|",
            ]
            for record in records:
                if not isinstance(record, dict):
                    continue
                stock = record.get("stock_name") or record.get("stock_code") or "—"
                code = record.get("stock_code") or ""
                lines.append(
                    f"| {record.get('id', '—')} | {stock}{f'（{code}）' if code else ''} | "
                    f"{record.get('created_at', '—')} | {record.get('report_type', '—')} |"
                )
            lines.append("\n告诉我报告 ID 或股票名称，我可以继续读取完整报告。")
            return "\n".join(lines)

        if tool_name == "get_analysis_status":
            if result.get("mode") == "detail" and isinstance(result.get("task"), dict):
                task = result["task"]
                return (
                    f"分析任务 **{task.get('task_id') or task.get('taskId') or '—'}**："
                    f"{task.get('status') or '未知状态'}，进度 {task.get('progress', 0)}%。\n\n"
                    f"{task.get('message') or task.get('error') or ''}"
                ).rstrip()
            stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
            return (
                f"当前共 **{stats.get('total', result.get('item_count', 0))} 个**分析任务："
                f"等待 {stats.get('pending', 0)}、运行中 {stats.get('processing', 0)}、"
                f"已完成 {stats.get('completed', 0)}、失败 {stats.get('failed', 0)}。"
            )

        if tool_name == "manage_analysis_schedule" and isinstance(result.get("schedule"), dict):
            schedule = result["schedule"]
            times = schedule.get("times") if isinstance(schedule.get("times"), list) else []
            return (
                f"自动分析计划当前 **{'已启用' if schedule.get('enabled') else '未启用'}**。\n\n"
                f"- 执行时间：{', '.join(map(str, times)) if times else '未设置'}\n"
                f"- 分析模板：{schedule.get('template_id') or '未设置'}"
            )

        if tool_name == "get_notification_status":
            channels = result.get("channels") if isinstance(result.get("channels"), list) else []
            configured = [str(channel.get("name") or channel.get("channel")) for channel in channels if isinstance(channel, dict) and channel.get("configured")]
            if configured:
                return f"已配置通知渠道：**{'、'.join(configured)}**。只有你明确要求发送时，助手才会推送通知。"
            return "通知渠道尚未配置。请先到设置页的“通知设置”中填写企业微信 Webhook。"

        if tool_name == "manage_batch_run" and result.get("action") == "report" and result.get("markdown"):
            return str(result["markdown"]).strip()

        if tool_name == "manage_batch_run" and result.get("action") == "list":
            count = int(result.get("item_count") or 0)
            return f"当前有 **{count} 个**批量分析任务。" if count else "当前没有批量分析任务。"

        if tool_name == "run_stock_analysis":
            return (
                f"正式分析任务已{'存在并继续运行' if result.get('duplicate') else '提交'}："
                f"股票 **{result.get('stock_code') or '—'}**，任务 ID `{result.get('task_id') or '—'}`，"
                f"当前状态 {result.get('status') or '等待中'}。"
            )

        if tool_name == "run_batch_analysis":
            count = len(result.get("stock_codes") or [])
            return f"批量分析已启动，共 **{count} 只股票**。你可以继续问我批次进度。"

        if tool_name == "delete_analysis_history":
            return f"已删除 **{result.get('deleted_count', 0)} 条**分析历史。"

        if tool_name == "send_notification":
            return f"通知已发送至 **{result.get('channel') or '已配置渠道'}**。"

        message = str(result.get("message") or "").strip()
        if message:
            return message
        action = str(result.get("action") or "操作")
        return f"{action} 已完成。"
    return None


def _build_staged_news_search_answer(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """Render a selectable news list without turning retrieval into research."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for packet in evidence or []:
        if not isinstance(packet, dict) or packet.get("tool") not in {
            "search_news", "search_financial_news",
        }:
            continue
        result = packet.get("result")
        if not isinstance(result, dict) or result.get("success") is False:
            continue
        for raw in result.get("items") or []:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or raw.get("link") or raw.get("id") or "").strip()
            if not title:
                continue
            key = url.rstrip("/").lower() or re.sub(r"\W+", "", title).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            items.append({
                "title": title,
                "url": url,
                "source": str(raw.get("source") or raw.get("author") or "来源未标明").strip(),
                "published": str(raw.get("published") or "时间未标明").strip()[:10],
                "summary": re.sub(r"\s+", " ", str(raw.get("summary") or "").strip())[:220],
                "importance": str(raw.get("importance") or "").strip(),
            })
    if not items:
        return None
    # Precise single-stock news carries an explicit importance tag. Preserve
    # source order within each tier so the tool's recency/relevance sort wins.
    rank = {"high": 0, "medium": 1, "low": 2, "": 1}
    items.sort(key=lambda item: rank.get(item["importance"], 1))
    items = items[:12]
    lines = [
        f"已找到 **{len(items)} 条**近期资讯。请回复编号，我再读取该条完整正文；本轮不提前做投资分析。\n",
        "| 编号 | 标题 | 来源 | 时间 | 简述 |",
        "|---:|---|---|---|---|",
    ]
    for index, item in enumerate(items, 1):
        title = f"[{item['title']}]({item['url']})" if item["url"] else item["title"]
        summary = item["summary"] or "—"
        lines.append(
            f"| {index} | {title} | {item['source']} | {item['published']} | {summary} |"
        )
    lines.append("\n直接回复如“第 2 条”即可。")
    return "\n".join(lines)


def _build_catalyst_analysis_answer(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """Render source-bound catalyst results without a second model call."""
    packets = [
        packet
        for packet in evidence or []
        if isinstance(packet, dict) and packet.get("tool") == "analyze_stock_catalysts"
    ]
    if not packets:
        return None

    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    warnings: List[str] = []
    data_times: List[str] = []
    for packet in packets:
        result = packet.get("result")
        if not isinstance(result, dict):
            continue
        items.extend(item for item in result.get("items") or [] if isinstance(item, dict))
        errors.extend(str(item) for item in result.get("errors") or [] if item)
        warnings.extend(str(item) for item in result.get("warnings") or [] if item)
        if result.get("data_time"):
            data_times.append(str(result["data_time"]))
    if not items:
        return (
            "## 未来6—12个月催化核验未完成\n\n"
            + ("；".join(errors) if errors else "本轮没有取得可用的公司催化证据。")
        )

    def clean(value: Any, limit: int = 300) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().replace("|", "／")[:limit]

    lines: List[str] = []
    for item_index, item in enumerate(items):
        name = clean(item.get("name") or item.get("symbol") or "公司", 80)
        symbol = clean(item.get("symbol"), 20)
        heading = f"{name}（{symbol}）" if symbol and symbol != name else name
        if item_index:
            lines.extend(["", "---", ""])
        lines.extend([f"## {heading}：未来6—12个月催化核验", ""])
        catalysts = [row for row in item.get("catalysts") or [] if isinstance(row, dict)]
        retrieved = (
            item.get("retrieved_evidence")
            if isinstance(item.get("retrieved_evidence"), dict)
            else {}
        )
        formal_windows = [
            row for row in retrieved.get("formal_documents") or []
            if isinstance(row, dict) and row.get("time_window") and row.get("excerpt")
        ]
        if catalysts and item.get("passed") is True:
            lines.append(f"**结论：核验到 {len(catalysts)} 项满足时间窗与来源约束的催化。**")
        elif catalysts:
            lines.append(
                f"**结论：提取到 {len(catalysts)} 项有来源的事件线索，但尚未达到严格催化通过条件。**"
            )
        elif formal_windows:
            lines.append(
                f"**结论：已从正式报告正文核验到 {len(formal_windows)} 项未来经营节点；"
                "本轮模型语义归类未完成，不会将其误报为“没有催化”。**"
            )
        else:
            lines.append("**结论：本轮未核验到同时具备明确时间窗和可回查来源的催化事件。**")
        verdict = clean(item.get("verdict"), 600)
        if formal_windows and not catalysts and (
            "评估失败" in verdict or "AllModelsFailedError" in verdict
        ):
            verdict = (
                "模型语义归类暂时不可用；以下先按正式报告原文列出未来经营节点，"
                "不把模型故障解释为公司没有催化。"
            )
        if verdict:
            lines.extend(["", verdict])

        if catalysts:
            lines.extend(["", "### 事件与证据", ""])
            event_type_labels = {
                "company_milestone": "公司里程碑",
                "financial_validation": "财务核验",
                "sector_mapping": "板块映射",
                "conditional_watch": "条件观察",
            }
            for index, catalyst in enumerate(catalysts, 1):
                event = clean(catalyst.get("event"), 220) or "未命名事件"
                window = clean(catalyst.get("time_window"), 80) or "时间窗缺失"
                status = clean(catalyst.get("verification_status"), 60) or "证据状态未标明"
                confidence = clean(catalyst.get("confidence"), 20)
                event_type = event_type_labels.get(
                    clean(catalyst.get("event_type"), 40),
                    "事件待分类",
                )
                lines.append(
                    f"{index}. **{event}**（{event_type}；{window}；{status}"
                    + (f"；置信度{confidence}" if confidence else "")
                    + "）"
                )
                why = clean(catalyst.get("why_it_matters"), 500)
                if why:
                    lines.append(f"   - 影响：{why}")
                sources = [row for row in catalyst.get("sources") or [] if isinstance(row, dict)]
                for source in sources:
                    evidence_id = clean(source.get("evidence_id"), 10)
                    title = clean(source.get("title"), 220) or "来源标题缺失"
                    source_name = clean(source.get("source"), 80) or "来源未标明"
                    source_date = clean(source.get("date"), 40) or "日期未标明"
                    url = str(source.get("url") or "").strip()
                    linked_title = f"[{title}]({url})" if re.match(r"^https?://", url) else title
                    lines.append(
                        f"   - 证据 {evidence_id}：{linked_title}（{source_name}，{source_date}）"
                    )
                    excerpt = clean(source.get("excerpt"), 420)
                    if excerpt:
                        lines.append(f"     - 正文：{excerpt}")
        else:
            clue_rows: List[Dict[str, Any]] = []
            for dimension, label in (
                ("formal_documents", "正式报告正文"),
                ("report_schedule", "财报预约"),
                ("research", "研报"),
                ("news", "公司新闻"),
                ("announcements", "公司公告"),
            ):
                for source in retrieved.get(dimension) or []:
                    if not isinstance(source, dict) or not source.get("title"):
                        continue
                    clue_rows.append({**source, "dimension_label": label})
                    if len(clue_rows) >= 6:
                        break
                if len(clue_rows) >= 6:
                    break
            if clue_rows:
                lines.extend(["", "### 尚缺明确时间窗的原始线索，或尚未升级为严格催化的证据", ""])
                for source in clue_rows:
                    evidence_id = clean(source.get("evidence_id"), 10)
                    title = clean(source.get("title"), 220)
                    source_name = clean(
                        source.get("source")
                        or source.get("org")
                        or source.get("label")
                        or source.get("dimension_label"),
                        80,
                    )
                    source_date = clean(source.get("date") or source.get("time"), 40) or "日期未标明"
                    url = str(source.get("url") or "").strip()
                    linked_title = f"[{title}]({url})" if re.match(r"^https?://", url) else title
                    lines.append(
                        f"- {evidence_id} · {source.get('dimension_label')}：{linked_title}"
                        f"（{source_name}，{source_date}）"
                    )
                lines.append(
                    "\n以上会保留给用户查看；正式报告中的时间窗必须继续解释其经营含义，财报预约只作为核验节点，"
                    "板块事件也不会冒充公司订单；证据不足时不升级为已核验催化。"
                )

        cited_ids = {
            clean(evidence_id, 12)
            for catalyst in catalysts
            for evidence_id in catalyst.get("evidence_ids") or []
        }
        uncategorized_formal_windows = [
            row for row in formal_windows
            if clean(row.get("evidence_id"), 12) not in cited_ids
        ]
        if uncategorized_formal_windows:
            lines.extend(["", "### 公司正式披露的未来经营节点（原文列示）", ""])
            for row in uncategorized_formal_windows[:8]:
                evidence_id = clean(row.get("evidence_id"), 12)
                window = clean(row.get("time_window"), 80)
                excerpt = clean(row.get("excerpt"), 700)
                title = clean(row.get("title"), 180) or "公司定期报告"
                url = str(row.get("url") or "").strip()
                linked_title = f"[{title}]({url})" if re.match(r"^https?://", url) else title
                lines.append(f"- **{window} · {evidence_id}**：{excerpt}")
                lines.append(f"  - 来源：{linked_title}")
            lines.append(
                "- 上述节点来自正式报告正文；模型暂时不可用时先保留原文事实，不额外推断订单金额、收入或利润贡献。"
            )
        verification_windows = [
            row for row in retrieved.get("report_schedule") or []
            if isinstance(row, dict)
            and clean(row.get("evidence_id"), 12) not in cited_ids
        ]
        if verification_windows:
            lines.extend(["", "### 已知财务核验窗口（不自动等于利好）", ""])
            for row in verification_windows[:4]:
                evidence_id = clean(row.get("evidence_id"), 12)
                title = clean(row.get("title"), 160) or "定期报告预约披露"
                window = clean(row.get("time_window"), 40) or "日期未标明"
                url = str(row.get("url") or "").strip()
                linked_title = f"[{title}]({url})" if re.match(r"^https?://", url) else title
                lines.append(f"- {window} · {evidence_id}：{linked_title}")
            lines.append(
                "- 该日期只说明何时验证收入、毛利率、现金流和新业务兑现，不因预约披露本身判定为正向催化。"
            )

        coverage = item.get("source_coverage") if isinstance(item.get("source_coverage"), dict) else {}
        available = coverage.get("available_count")
        required = coverage.get("required_count")
        if available is not None and required is not None:
            lines.extend(["", f"> 证据源覆盖：{available}/{required}（公告目录、正式报告正文、财报预约、公司新闻、券商研报）。"])
        missing = [clean(value, 180) for value in item.get("missing_evidence") or [] if clean(value, 180)]
        if missing:
            lines.append("> 仍需核验：" + "；".join(missing[:6]))

    lines.extend([
        "",
        "### 判断边界",
        "",
        "- 这里只回答未来催化，不等于现在可以买入；估值、利好是否已被股价反映、买入位置和风险收益比仍需单独核验。",
        "- 没有明确日历时间窗或无法绑定本轮真实来源的线索，不会被列为已核验催化。",
        "- 板块映射、财务核验和公司里程碑会分开标注；行业大会或关键客户事件不会被改写成公司订单。",
        f"- 分析时间：{max(data_times) if data_times else '未标明'}；来源：内部同步公告目录、正式定期报告正文、财报预约、公司新闻、券商研报。",
    ])
    if warnings or errors:
        lines.append("- 数据边界：" + "；".join([*warnings, *errors][:8]))
    return "\n".join(lines)


def _build_domain_candidate_answer(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """Render multi-domain structured candidates without another model pass."""
    result = next(
        (
            packet.get("result")
            for packet in evidence or []
            if isinstance(packet, dict)
            and packet.get("tool") == "get_domain_stock_candidates"
            and isinstance(packet.get("result"), dict)
        ),
        None,
    )
    if not isinstance(result, dict):
        return None

    domain_results = [
        item for item in result.get("domain_results") or [] if isinstance(item, dict)
    ]
    if not domain_results:
        return (
            "## 领域股票候选未完成\n\n"
            "本轮没有取得任何结构化板块结果，因此没有使用网页名单或模型记忆补股票。"
        )

    union: Dict[str, Dict[str, Any]] = {}
    coverage_lines: List[str] = []
    inherited_mapping_parts: List[str] = []
    failed_domains: List[str] = []
    for domain_result in domain_results:
        domain = str(domain_result.get("domain") or "未命名领域")
        themes = [str(value) for value in domain_result.get("lookup_themes") or [] if value]
        boards = list(dict.fromkeys(
            str(board.get("name") or "")
            for board in domain_result.get("matched_boards") or []
            if isinstance(board, dict) and board.get("name")
        ))
        mapping_type = str(domain_result.get("mapping_type") or "")
        basis = {
            "exact_board": "当前目录同名板块",
            "proxy_board": "当前目录最窄代理板块",
            "unresolved": "当前目录未解析",
        }.get(mapping_type, "结构化板块")
        if domain_result.get("context_filter_applied"):
            basis += f"，再与上位主题“{domain_result.get('context_theme') or result.get('context_theme')}”取交集"
        coverage = "完整" if domain_result.get("coverage_complete") else "部分"
        count = int(domain_result.get("candidate_count") or 0)
        rationale = str(domain_result.get("mapping_rationale") or "").strip()
        unresolved_parts = [
            str(value) for value in domain_result.get("unresolved_parts") or [] if value
        ]
        coverage_lines.append(
            f"- **{domain}**：{basis} `{ '、'.join(themes) or '未匹配' }`；"
            f"实际板块 { '、'.join(boards) or '未取得' }；{coverage}覆盖，候选 **{count} 只**。"
            + (f" 映射说明：{rationale}" if rationale else "")
            + (f" 未覆盖子领域：{'、'.join(unresolved_parts)}。" if unresolved_parts else "")
        )
        if themes:
            inherited_mapping_parts.append(f"{domain}→{'、'.join(themes)}")
        if not domain_result.get("success"):
            failed_domains.append(domain)
        for item in domain_result.get("items") or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            name = str(item.get("name") or "").strip()
            if not re.fullmatch(r"\d{6}", symbol) or not name:
                continue
            merged = union.setdefault(symbol, {
                "symbol": symbol,
                "name": name,
                "domains": [],
                "boards": [],
            })
            for value in item.get("matched_domains") or [domain]:
                text = str(value or "").strip()
                if text and text not in merged["domains"]:
                    merged["domains"].append(text)
            for value in item.get("boards") or boards:
                text = str(value or "").strip()
                if text and text not in merged["boards"]:
                    merged["boards"].append(text)

    rows = sorted(
        union.values(),
        key=lambda item: (-len(item["domains"]), item["symbol"]),
    )
    if rows:
        table_lines = [
            "| 公司/代码 | 匹配领域 | 结构化板块依据 | 证据级别 |",
            "|---|---|---|---|",
        ]
        table_lines.extend(
            f"| {item['name']} ({item['symbol']}) | {'、'.join(item['domains'])} | "
            f"{'、'.join(item['boards']) or '板块名称缺失'} | L1 候选 |"
            for item in rows
        )
        candidate_text = "\n".join(table_lines)
    else:
        candidate_text = "没有取得任何本地证券库可核验的候选股票。"

    failure_text = ""
    if failed_domains:
        failure_text = (
            "\n\n> 未完成领域：" + "、".join(failed_domains)
            + "。这些领域没有改用网页搜索或模型记忆补名单。"
        )
    inherited_mapping_text = ""
    if inherited_mapping_parts:
        inherited_mapping_text = (
            "\n\n> 结构化板块映射（后续追问继续沿用）："
            + "；".join(inherited_mapping_parts)
            + "。"
        )
    return (
        "## 按领域匹配的 A 股候选\n\n"
        f"已与本地 **{result.get('local_universe_count') or '全量'} 只**有效证券交叉核验，"
        f"合并去重后共 **{len(rows)} 只**。\n\n"
        "### 领域与板块映射\n\n"
        + "\n".join(coverage_lines)
        + "\n\n### 候选股票\n\n"
        + candidate_text
        + inherited_mapping_text
        + failure_text
        + "\n\n> 口径：以上只证明结构化概念板块成员关系和证券身份有效。"
        "它不是订单、客户验证、收入兑现或买入建议；本轮没有使用通用网页搜索生成候选。"
    )


def _evaluate_collection_financial_filter(
    evidence: Optional[List[Dict[str, Any]]],
    spec: CollectionFinancialFilterSpec,
) -> Optional[Dict[str, Any]]:
    """Aggregate all batches for one typed predicate without writing prose."""
    operator = spec.operator
    threshold = spec.normalized_threshold
    exclude_matching = spec.action == "exclude_matching"
    packets = [
        packet
        for packet in evidence or []
        if isinstance(packet, dict)
        and packet.get("tool") == "get_multi_stock_financials"
    ]
    if not packets:
        return None

    requested: list[str] = []
    rows_by_code: Dict[str, Dict[str, Any]] = {}
    sources: list[str] = []
    data_times: list[str] = []
    for packet in packets:
        arguments = packet.get("arguments") if isinstance(packet.get("arguments"), dict) else {}
        for code in re.split(r"[,，、;；]+", str(arguments.get("symbols") or "")):
            code = code.strip()
            if re.fullmatch(r"\d{6}", code) and code not in requested:
                requested.append(code)
        result = packet.get("result")
        if not isinstance(result, dict) or result.get("success") is False:
            continue
        source = str(result.get("source") or "").strip()
        if source and source not in sources:
            sources.append(source)
        data_time = str(result.get("data_time") or "").strip()
        if data_time:
            data_times.append(data_time)
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("symbol") or "").strip()
            value = item.get("financial_value")
            if not re.fullmatch(r"\d{6}", code) or not isinstance(value, (int, float)):
                continue
            rows_by_code[code] = item

    missing = [code for code in requested if code not in rows_by_code]
    comparator = {
        "gt": lambda value: value > threshold,
        "gte": lambda value: value >= threshold,
        "lt": lambda value: value < threshold,
        "lte": lambda value: value <= threshold,
        "eq": lambda value: value == threshold,
    }[operator]
    operator_label = {
        "gt": "高于", "gte": "不低于", "lt": "低于", "lte": "不高于", "eq": "等于",
    }[operator]
    ordered_rows = [rows_by_code[code] for code in requested if code in rows_by_code]
    matching = [row for row in ordered_rows if comparator(float(row["financial_value"]))]
    non_matching = [row for row in ordered_rows if not comparator(float(row["financial_value"]))]
    excluded = matching if exclude_matching else non_matching
    kept = non_matching if exclude_matching else matching
    threshold_text = {
        "percent": f"{spec.threshold:g}%",
        "cny": f"{spec.threshold:g} 元",
        "wan_cny": f"{spec.threshold:g} 万元",
        "yi_cny": f"{spec.threshold:g} 亿元",
    }[spec.threshold_unit]
    annual_years = sorted({
        str(row.get("report_date") or "")[:4]
        for row in ordered_rows
        if re.fullmatch(r"\d{4}", str(row.get("report_date") or "")[:4])
    })
    period_label = {
        "latest_report": "最新报告期",
        "ttm": "TTM",
        "previous_fiscal_year": (
            f"{annual_years[0]} 年报" if len(annual_years) == 1 else "去年完整年报"
        ),
        "fiscal_year": f"{spec.fiscal_year} 年报",
    }[spec.period_basis]
    action_text = "筛除命中项" if exclude_matching else "只保留命中项"
    return {
        "requested": requested,
        "rows_by_code": rows_by_code,
        "missing": missing,
        "matching": matching,
        "excluded": excluded,
        "kept": kept,
        "sources": sources,
        "data_times": data_times,
        "operator_label": operator_label,
        "threshold_text": threshold_text,
        "period_label": period_label,
        "action_text": action_text,
    }


def _format_collection_financial_value(
    row: Dict[str, Any],
    spec: CollectionFinancialFilterSpec,
) -> str:
    value = float(row["financial_value"])
    if spec.metric == "debt_ratio":
        return f"{value:.2f}%"
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿元"
    if abs(value) >= 10_000:
        return f"{value / 10_000:.2f} 万元"
    return f"{value:.2f} 元"


def _collection_financial_rule_text(
    spec: CollectionFinancialFilterSpec,
    evaluation: Dict[str, Any],
) -> str:
    return (
        f"{evaluation['period_label']}{spec.metric_label} "
        f"{evaluation['operator_label']} {evaluation['threshold_text']}，"
        f"{evaluation['action_text']}"
    )


def _build_collection_financial_filter_answer(
    evidence: Optional[List[Dict[str, Any]]],
    spec: CollectionFinancialFilterSpec,
) -> Optional[str]:
    """Render a validated single-predicate collection filter."""
    evaluation = _evaluate_collection_financial_filter(evidence, spec)
    if evaluation is None:
        return None
    requested = evaluation["requested"]
    rows_by_code = evaluation["rows_by_code"]
    missing = evaluation["missing"]
    excluded = evaluation["excluded"]
    kept = evaluation["kept"]
    sources = evaluation["sources"]
    data_times = evaluation["data_times"]
    period_label = evaluation["period_label"]
    operator_label = evaluation["operator_label"]
    threshold_text = evaluation["threshold_text"]
    action_text = evaluation["action_text"]
    lines = [
        f"## 上文股票{period_label}{spec.metric_label}筛选",
        "",
        f"规则：{period_label}{spec.metric_label} **{operator_label} {threshold_text}**，{action_text}。",
        f"原集合 **{len(requested)} 只**，成功覆盖 **{len(rows_by_code)} 只**，缺失 **{len(missing)} 只**。",
    ]
    if missing:
        lines.extend([
            "",
            "> 本轮筛选未完成，不能把已覆盖的部分结果当作完整名单。",
            "> 未取得数据或工具批次失败的代码：" + "、".join(missing),
        ])
    else:
        lines.append(
            f"完整筛选结果：筛除 **{len(excluded)} 只**，筛选后保留 **{len(kept)} 只**。"
        )

    def append_table(title: str, rows: list[dict[str, Any]]) -> None:
        lines.extend(["", f"### {title}", ""])
        if not rows:
            lines.append("无。")
            return
        lines.extend([
            f"| 公司/代码 | {spec.metric_label} | 报告期 |",
            "|---|---:|---|",
        ])
        for row in rows:
            value_text = _format_collection_financial_value(row, spec)
            lines.append(
                f"| {row.get('name') or '未命名'} ({row.get('symbol')}) | "
                f"{value_text} | {row.get('report_date') or '未标明'} |"
            )

    append_table("筛除项", excluded)
    append_table("筛选后保留项" if not missing else "已覆盖范围内的暂定保留项", kept)
    lines.extend([
        "",
        "> 数据来源：" + ("；".join(sources) or "本地已同步财务库")
        + (f"；同步时间 {max(data_times)}" if data_times else "；同步时间未标明")
        + "。本轮未调用实时行情、K线或技术指标。",
    ])
    return "\n".join(lines)


def _build_compound_collection_financial_filter_answer(
    plan: TaskPlan,
    execution: PlanExecutionResult,
) -> Optional[str]:
    """Combine multiple independent predicates into one exact set result."""
    if len(plan.tasks) < 2 or not all(
        task.kind == StandardTaskKind.COLLECTION_FINANCIAL_FILTER
        for task in plan.tasks
    ):
        return None

    results_by_task = {result.task.task_id: result for result in execution.tasks}
    evaluated: list[tuple[CollectionFinancialFilterSpec, Dict[str, Any]]] = []
    for task in plan.tasks:
        result = results_by_task.get(task.task_id)
        if result is None:
            return None
        try:
            spec = CollectionFinancialFilterSpec.model_validate(task.parameters)
        except Exception:
            return None
        evidence = [call.evidence() for call in result.calls]
        evaluation = _evaluate_collection_financial_filter(evidence, spec)
        if evaluation is None:
            return None
        evaluated.append((spec, evaluation))

    requested = list(evaluated[0][1]["requested"])
    requested_set = set(requested)
    missing_by_rule: list[tuple[str, list[str]]] = []
    final_kept = set(requested)
    rows_by_code: Dict[str, Dict[str, Any]] = {}
    excluded_reasons: Dict[str, List[str]] = {}
    sources: list[str] = []
    data_times: list[str] = []

    for spec, evaluation in evaluated:
        rule_text = _collection_financial_rule_text(spec, evaluation)
        rule_requested = set(evaluation["requested"])
        missing = sorted(
            set(evaluation["missing"]) | (requested_set - rule_requested)
        )
        if missing:
            missing_by_rule.append((rule_text, missing))
        kept_codes = {
            str(row.get("symbol") or "") for row in evaluation["kept"]
        }
        final_kept &= kept_codes
        for code, row in evaluation["rows_by_code"].items():
            rows_by_code.setdefault(code, row)
        for row in evaluation["excluded"]:
            code = str(row.get("symbol") or "")
            if not code:
                continue
            reason = (
                f"{evaluation['period_label']}{spec.metric_label} "
                f"{_format_collection_financial_value(row, spec)}"
            )
            excluded_reasons.setdefault(code, []).append(reason)
        for source in evaluation["sources"]:
            if source not in sources:
                sources.append(source)
        data_times.extend(evaluation["data_times"])

    final_excluded = [code for code in requested if code not in final_kept]
    final_kept_ordered = [code for code in requested if code in final_kept]
    lines = [
        "## 上文股票复合财务筛选",
        "",
        f"原集合 **{len(requested)} 只**，本轮同时执行 **{len(evaluated)} 项**财务条件。",
        "",
        "### 筛选规则",
        "",
    ]
    lines.extend(
        f"{index}. {_collection_financial_rule_text(spec, evaluation)}。"
        for index, (spec, evaluation) in enumerate(evaluated, 1)
    )
    if missing_by_rule:
        lines.extend([
            "",
            "> 本轮复合筛选未完成，不能把部分覆盖结果当作最终名单。",
        ])
        for rule_text, missing in missing_by_rule:
            lines.append(f"> {rule_text}：缺失 {'、'.join(missing)}。")
    else:
        lines.extend([
            "",
            f"全部条件均完整覆盖 **{len(requested)} 只**；合并后筛除 "
            f"**{len(final_excluded)} 只**，最终保留 **{len(final_kept_ordered)} 只**。",
        ])

    lines.extend(["", "### 筛除项", ""])
    if not final_excluded:
        lines.append("无。")
    else:
        lines.extend(["| 公司/代码 | 命中或未满足的条件 |", "|---|---|"])
        for code in final_excluded:
            row = rows_by_code.get(code, {})
            reasons = excluded_reasons.get(code) or ["未满足全部保留条件"]
            lines.append(
                f"| {row.get('name') or '未命名'} ({code}) | {'；'.join(reasons)} |"
            )

    kept_title = "最终保留项" if not missing_by_rule else "已覆盖范围内的暂定保留项"
    lines.extend(["", f"### {kept_title}", ""])
    if not final_kept_ordered:
        lines.append("无。")
    else:
        lines.extend(["| 公司/代码 | 结果 |", "|---|---|"])
        for code in final_kept_ordered:
            row = rows_by_code.get(code, {})
            lines.append(f"| {row.get('name') or '未命名'} ({code}) | 全部条件通过 |")

    lines.extend([
        "",
        "> 数据来源：" + ("；".join(sources) or "本地已同步财务库")
        + (f"；同步时间 {max(data_times)}" if data_times else "；同步时间未标明")
        + "。多条件结果由程序按集合交集计算，未交给模型改写名单。",
    ])
    return "\n".join(lines)


def _build_verified_evidence_fallback(
    evidence: Optional[List[Dict[str, Any]]],
    *,
    professional_decision_requested: Optional[bool] = None,
    semantic_facts: Optional[List[BoundEvidenceFact]] = None,
    semantic_intent: Optional[MappingSelectionContext] = None,
) -> str:
    """Return a complete deterministic answer when final model text is empty.

    A provider can occasionally finish a streaming request without emitting a
    text delta.  Dropping the already verified tool result leaves the user with
    a blank assistant turn.  For the high-value multi-stock path we can still
    provide a compact, auditable screen directly from the batch payload without
    inventing business facts or pretending this mechanical screen is advice.
    """
    batch: Optional[Dict[str, Any]] = None

    professional_buy_answer = _build_professional_buy_decision_answer(evidence)
    if professional_buy_answer:
        return professional_buy_answer

    # Reading a persisted report is retrieval, not a new model judgement.  If
    # the provider emits no final text after the tool succeeds, return the
    # stored report itself instead of asking the user to retry.  The LLM-facing
    # tool payload may contain only a Markdown excerpt, so reload the complete
    # local report when necessary; this path never invents or rewrites facts.
    for item in reversed(evidence or []):
        if not isinstance(item, dict) or item.get("tool") != "read_analysis_report":
            continue
        result = item.get("result")
        if not isinstance(result, dict) or result.get("success") is False:
            continue

        markdown = str(result.get("markdown") or "").strip()
        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        record_id = str(
            result.get("record_id")
            or arguments.get("record_id")
            or ""
        ).strip()
        complete_report_reloaded = False
        if result.get("markdown_excerpt") and record_id:
            try:
                from src.services.history_service import HistoryService

                complete_markdown = HistoryService().get_markdown_report(record_id)
                if complete_markdown and complete_markdown.strip():
                    markdown = complete_markdown.strip()
                    complete_report_reloaded = True
            except Exception:
                logger.exception(
                    "[Agent] failed to reload complete persisted report record_id=%s",
                    record_id,
                )

        if markdown:
            try:
                expected_length = int(result.get("markdown_length") or 0)
            except (TypeError, ValueError):
                expected_length = 0
            if (
                result.get("markdown_excerpt")
                and not complete_report_reloaded
                and len(markdown) < expected_length
            ):
                markdown += (
                    "\n\n> 报告正文较长，当前只取得工具上下文中的节选；"
                    "请指定报告章节继续读取。"
                )
            return markdown

        report = result.get("report")
        if isinstance(report, dict):
            meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
            summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
            strategy = report.get("strategy") if isinstance(report.get("strategy"), dict) else {}
            stock_name = meta.get("stock_name") or meta.get("stock_code") or "股票"
            stock_code = meta.get("stock_code") or ""
            title = f"# {stock_name}{f'（{stock_code}）' if stock_code else ''}正式分析报告"
            lines = [title]
            if meta.get("created_at"):
                lines.append(f"\n> 报告时间：{meta['created_at']}")
            fields = (
                ("关键结论", summary.get("analysis_summary")),
                ("操作建议", summary.get("operation_advice")),
                ("趋势判断", summary.get("trend_prediction")),
                ("情绪", summary.get("sentiment_label")),
                ("理想买入价", strategy.get("ideal_buy")),
                ("第二买入价", strategy.get("secondary_buy")),
                ("止损价", strategy.get("stop_loss")),
                ("止盈价", strategy.get("take_profit")),
            )
            for label, value in fields:
                if value not in (None, ""):
                    lines.append(f"\n## {label}\n\n{value}")
            if len(lines) > 1:
                return "".join(lines)

    workflow_answer = _build_workflow_evidence_fallback(evidence)
    if workflow_answer:
        return workflow_answer

    domain_answer = _build_domain_candidate_answer(evidence)
    if domain_answer:
        return domain_answer

    for item in evidence or []:
        if not isinstance(item, dict) or item.get("tool") != "get_theme_stock_candidates":
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("success") is not False:
            return _build_theme_mapping_fallback(
                result,
                evidence,
                semantic_facts=semantic_facts,
                semantic_intent=semantic_intent,
            )

    for item in evidence or []:
        if not isinstance(item, dict) or item.get("tool") != "get_multi_stock_decision_evidence":
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("success") is not False:
            answer = _build_professional_decision_fallback(
                result,
                decision_requested=professional_decision_requested,
            )
            market = next(
                (
                    packet.get("result")
                    for packet in evidence or []
                    if isinstance(packet, dict)
                    and packet.get("tool") == "get_market_breadth"
                    and isinstance(packet.get("result"), dict)
                    and packet["result"].get("success") is not False
                ),
                None,
            )
            if isinstance(market, dict):
                answer += (
                    "\n\n### 市场宽度\n\n"
                    f"- 上涨 **{market.get('up_count', '缺失')}** 家，下跌 **{market.get('down_count', '缺失')}** 家，"
                    f"涨跌比 **{market.get('advance_decline_ratio', '缺失')}**；"
                    f"成交额 **{market.get('total_amount', '缺失')} {market.get('total_amount_unit') or ''}**。\n"
                    f"- 数据时间：{market.get('data_time') or market.get('market_date') or '缺失'}；"
                    "市场宽度只用于判断介入环境，不改变单家公司基本面结论。"
                )
            return answer

    if professional_decision_requested is not None:
        return (
            "## 专业决策证据未完成\n\n"
            "本轮未成功取得覆盖全部公司的专业决策证据，因此停止买入判断。"
            "请重试本轮查询；在专业证据工具成功前，不输出买入、持有或卖出结论。"
        )

    for item in evidence or []:
        if not isinstance(item, dict) or item.get("tool") != "get_multi_stock_snapshot":
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("success") is not False:
            batch = result
            break

    if not batch or not isinstance(batch.get("items"), list):
        return (
            "已取得工具证据，但模型本次没有返回最终文本。为避免编造结论，本轮不补写未经"
            "核验的判断；请直接重试当前问题，已取得的证据仍保留在工具卡片中。"
        )

    def number(value: Any, digits: int = 2) -> str:
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "缺失"

    rows: List[str] = []
    stale_names: List[str] = []
    for item in batch["items"]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("symbol") or "未知")
        symbol = str(item.get("symbol") or "—")
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
        financial = item.get("financial") if isinstance(item.get("financial"), dict) else {}
        technical = item.get("technical") if isinstance(item.get("technical"), dict) else {}
        pe = quote.get("pe_dynamic")
        debt = financial.get("debt_ratio_pct")
        profit = financial.get("net_profit")
        if technical.get("is_stale"):
            stale_names.append(f"{name}({symbol})")

        try:
            pe_value = float(pe)
        except (TypeError, ValueError):
            pe_value = 0.0
        try:
            debt_value = float(debt)
        except (TypeError, ValueError):
            debt_value = 0.0
        try:
            profit_value = float(profit)
        except (TypeError, ValueError):
            profit_value = 0.0

        if profit_value < 0 or pe_value <= 0:
            screen = "亏损，先观察"
        elif debt_value >= 70:
            screen = "高负债，先观察"
        elif pe_value >= 100:
            screen = "估值高，等业绩兑现"
        elif pe_value >= 60:
            screen = "估值偏高，谨慎观察"
        else:
            screen = "先核验业务兑现"
        rows.append(
            f"| {name} ({symbol}) | {number(quote.get('price'))} / "
            f"{number(quote.get('change_pct'))}% | {number(pe)} / "
            f"{number(quote.get('pb_ratio'))} | {screen} |"
        )

    warning = ""
    if stale_names:
        warning = "\n- 技术数据陈旧：" + "、".join(stale_names) + "，未据此作判断。"
    warnings = batch.get("warnings")
    warning_text = "；".join(str(value) for value in warnings or [] if value)
    if warning_text and not warning:
        warning = f"\n- 数据警示：{warning_text}。"

    basis = str(batch.get("quote_basis") or "最新行情快照")
    data_time = str(batch.get("data_time") or "时间缺失")
    return (
        "## 多股数据快照\n\n"
        "下表只是基于本轮已核验行情、动态 PE/PB 与最新报告期财务的机械数据整理。"
        "用户未明确要求买卖判断时，不据此推导整组股票适合买入或不适合买入。\n\n"
        "| 公司/代码 | 最新价/涨跌 | 动态PE/PB | 初筛 |\n"
        "|---|---:|---:|---|\n"
        + "\n".join(rows)
        + "\n\n- 后续如需投资判断，应另外核验相关业务订单、收入、现金流及估值匹配度。"
        f"{warning}\n- 数据口径：{data_time}，{basis}；PE 为动态市盈率，不是 PE(TTM)。"
    )


def _build_realtime_quote_answer(
    evidence: Optional[List[Dict[str, Any]]],
    intent_kind: Optional[str] = None,
) -> str:
    """Render quote-only turns without letting synthesis invent market context."""
    if intent_kind != "market_snapshot":
        return ""
    packets = [item for item in evidence or [] if isinstance(item, dict)]
    quote_only_support_tools = {
        "get_realtime_quotes", "get_market_status", "get_market_breadth",
    }
    if not packets or any(item.get("tool") not in quote_only_support_tools for item in packets):
        return ""
    results = [
        item.get("result")
        for item in packets
        if item.get("tool") == "get_realtime_quotes" and isinstance(item.get("result"), dict)
    ]
    if not results:
        return ""
    result = results[-1]
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    if not items:
        return ""

    def number(value: Any, digits: int = 2) -> str:
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "—"

    rows: List[str] = []
    for item in items:
        symbol = str(item.get("symbol") or item.get("code") or "")
        name = str(item.get("name") or symbol)
        pct = number(item.get("pct_chg", item.get("change_pct")))
        rows.append(
            f"| {name} ({symbol}) | {number(item.get('price'))} 元 | {pct}% | "
            f"{number(item.get('high'))} / {number(item.get('low'))} | "
            f"{number(item.get('amount'), 0)} 元 |"
        )

    quote_mode = str(result.get("quote_mode") or "")
    is_live = quote_mode == "live" or result.get("is_trading_session") is True
    mode_label = str(result.get("quote_mode_label") or "").strip() or (
        "交易时段实时行情" if is_live else "最近交易日行情快照"
    )
    data_time = str(result.get("data_time") or "时间未知").replace("T", " ")
    sources = result.get("source") or []
    source_text = "、".join(map(str, sources)) if isinstance(sources, list) else str(sources)
    stale_note = "；数据已陈旧，请勿据此判断当前价格" if result.get("is_stale") is True else ""
    return (
        "## 最新行情\n\n"
        "| 股票 | 最新价 | 涨跌幅 | 最高 / 最低 | 成交额 |\n"
        "|---|---:|---:|---:|---:|\n"
        + "\n".join(rows)
        + f"\n\n- 数据时间：{data_time}\n"
        + f"- 行情口径：{mode_label}{stale_note}\n"
        + f"- 数据来源：{source_text or '行情工具返回来源'}\n"
        + ("" if is_live else "- 当前为非交易时段；以上不是当前时刻的实时成交，也不等同于收盘价。\n")
    )


def _build_quantitative_screen_answer(evidence: Optional[List[Dict[str, Any]]]) -> str:
    """Render a validated screen verbatim without changing its conditions."""
    results = [
        item.get("result")
        for item in evidence or []
        if isinstance(item, dict)
        and item.get("tool") == "screen_atr_volatility_stocks"
        and isinstance(item.get("result"), dict)
    ]
    if not results:
        return "## 筛选未完成\n\n量化筛选工具没有返回结构化结果，因此本轮不输出股票结论。"
    result = results[-1]
    coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else {}
    coverage_parts = [f"本轮股票范围 {coverage.get('universe', '—')} 只"]
    if coverage.get("history_preexcluded") is not None:
        coverage_parts.append(f"上市历史确定不足预排除 {coverage.get('history_preexcluded')} 只")
    if coverage.get("financial_covered") is not None:
        coverage_parts.append(f"必需财务字段覆盖 {coverage.get('financial_covered')} 只")
    if coverage.get("financial_eligible") is not None:
        coverage_parts.append(f"财务条件后候选 {coverage.get('financial_eligible')} 只")
    if coverage.get("fresh_kline") is not None:
        coverage_parts.append(f"取得行情 {coverage.get('fresh_kline')} 只")
    fallback_count = int(coverage.get("financial_fallback_count") or 0)
    cache_count = int(coverage.get("financial_cache_count") or 0)
    if cache_count:
        coverage_parts.append(f"本日财务快照接管 {cache_count} 只")
    if fallback_count:
        coverage_parts.append(f"财务补源 {fallback_count} 只")
    coverage_text = "；".join(coverage_parts) + "。"
    if result.get("success") is not True:
        errors = [str(item) for item in result.get("errors") or [] if item]
        failed = [str(item) for item in result.get("failed_symbols") or [] if item]
        detail = "\n".join(f"- {item}" for item in errors) or "- 工具没有提供失败原因。"
        failed_text = ("\n- 失败样例：" + "；".join(failed[:10])) if failed else ""
        stage = str(result.get("failure_stage") or "unknown")
        return (
            "## 筛选未完成\n\n"
            "**本轮不输出任何股票结论。** 条件、数据刷新或全市场覆盖没有通过硬校验。\n\n"
            f"- 失败阶段：`{stage}`\n{detail}{failed_text}\n\n- 覆盖校验：{coverage_text}"
        )

    screen_spec = result.get("screen_spec")
    columns = result.get("columns")
    applied_rules = result.get("applied_rules")
    if (
        not isinstance(screen_spec, dict)
        or not isinstance(columns, list)
        or not columns
        or not isinstance(applied_rules, list)
        or not applied_rules
        or coverage.get("complete") is not True
    ):
        return (
            "## 筛选未完成\n\n**本轮不输出任何股票结论。** 工具虽返回成功，"
            "但缺少实际执行规格、动态列定义、规则回显或完整覆盖证明。"
        )

    def number(value: Any, digits: int = 2) -> str:
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "—"

    def format_value(value: Any, format_name: str) -> str:
        if value is None:
            return "—"
        if format_name == "currency_yuan":
            try:
                return f"{float(value) / 100_000_000:,.2f}亿"
            except (TypeError, ValueError):
                return "—"
        if format_name == "percent":
            return number(value) + "%"
        if format_name == "integer":
            try:
                return f"{int(value):,}"
            except (TypeError, ValueError):
                return "—"
        return str(value)

    normalized_columns: List[Dict[str, str]] = []
    for column in columns:
        if not isinstance(column, dict):
            continue
        field = str(column.get("field") or "").strip()
        label = str(column.get("label") or "").strip()
        if field and label:
            normalized_columns.append({
                "field": field,
                "label": label,
                "format": str(column.get("format") or "text"),
            })
    if not normalized_columns:
        return "## 筛选未完成\n\n工具返回的结果列合同无效，本轮不输出股票结论。"

    preview_items = result.get("items") or []
    for item in preview_items:
        if not isinstance(item, dict) or any(
            column["field"] not in item for column in normalized_columns
        ):
            return (
                "## 筛选未完成\n\n工具返回的预览行缺少请求字段，"
                "结果合同不完整，因此本轮不输出股票结论。"
            )

    rows: List[str] = []
    for item in preview_items:
        cells = [
            format_value(item.get(column["field"]), column["format"])
            for column in normalized_columns
        ]
        rows.append("| " + " | ".join(cells) + " |")
    total = int(result.get("total") or 0)
    preview_limit = int(screen_spec.get("preview_limit") or 10)
    download_url = str(result.get("download_url") or "").strip()
    if total > preview_limit and not download_url:
        return (
            "## 筛选未完成\n\n完整结果超过页面预览上限，但工具没有生成下载文件；"
            "为避免交付不完整名单，本轮不输出股票结论。"
        )
    if rows:
        table = (
            "| " + " | ".join(column["label"] for column in normalized_columns) + " |\n"
            "| " + " | ".join("---" for _ in normalized_columns) + " |\n"
            + "\n".join(rows)
        )
    else:
        table = "完整执行本轮全部条件后，合格股票为 **0 只**。"
    sort_spec = screen_spec.get("sort") if isinstance(screen_spec.get("sort"), dict) else {}
    sort_text = f"{sort_spec.get('field', '工具指定字段')} {sort_spec.get('order', '—')}"
    download = f"\n\n[下载完整 {total} 只筛选结果（CSV）]({download_url})" if download_url else ""
    rules_text = "\n".join(f"- {item}" for item in applied_rules if str(item).strip())
    fingerprint = str(result.get("spec_fingerprint") or "—")
    data_times = result.get("data_times") if isinstance(result.get("data_times"), dict) else {}
    kline_time = str(data_times.get("kline_expected_date") or "").strip()
    financial_period = str(
        data_times.get("financial_report_period")
        or result.get("financial_report_period")
        or ""
    ).strip()
    time_parts: List[str] = []
    if kline_time:
        time_parts.append(f"行情刷新基准日 {kline_time}")
    if financial_period:
        time_parts.append(f"主财务报告期 {financial_period}")
    if not time_parts:
        time_parts.append(f"数据日期 {result.get('data_time', '—')}")
    warnings = [str(item) for item in result.get("warnings") or [] if item]
    warnings_text = ""
    if warnings:
        warnings_text = "\n- 数据源切换：" + "；".join(warnings)
    saved_group = result.get("saved_group") if isinstance(result.get("saved_group"), dict) else None
    saved_group_text = ""
    if saved_group:
        saved_group_text = (
            f"\n\n### 已保存到自选分组\n\n"
            f"完整筛选结果已保存为 **{saved_group.get('name') or '未命名分组'}**，"
            f"共 {saved_group.get('count', total)} 只股票。"
        )
    return (
        f"## 筛选结论\n\n共 **{total} 只**股票满足本轮完整规格，排序为 `{sort_text}`。"
        + (f"下表展示前{preview_limit}只。\n\n" if total > preview_limit else "\n\n")
        + table
        + download
        + "\n\n### 本轮实际执行规格\n\n"
        + rules_text
        + f"\n- 规格指纹：`{fingerprint}`"
        + "\n\n### 数据与覆盖\n\n"
        + f"- 数据日期：{'；'.join(time_parts)}。\n"
        + f"- 覆盖：{coverage_text}\n"
        + f"- 来源：{result.get('source', '工具返回来源')}。"
        + warnings_text
        + saved_group_text
    )


def _build_atr_screen_answer(evidence: Optional[List[Dict[str, Any]]]) -> str:
    """Compatibility alias for existing imports while the tool keeps its name."""
    return _build_quantitative_screen_answer(evidence)


def _unsupported_final_claims(
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Catch high-risk claims whose required evidence dimension is absent."""
    tool_names = {
        str(item.get("tool") or "")
        for item in evidence or []
        if isinstance(item, dict)
    }
    reasons: List[str] = []
    has_flow_evidence = (
        any("flow" in name for name in tool_names)
        or "get_multi_stock_decision_evidence" in tool_names
    )
    if not has_flow_evidence and re.search(
        r"(?:主力资金|资金(?:仍在|持续|明显|大幅)?(?:净)?流(?:入|出))",
        content,
    ):
        reasons.append("资金流结论没有资金流工具证据")

    has_channel_price_evidence = bool(tool_names.intersection({
        "search_news", "search_financial_news", "search_research_library", "websearch", "webfetch",
    }))
    if not has_channel_price_evidence and re.search(r"(?:一)?批价", content):
        reasons.append("白酒批价或阈值没有新闻、公告或网页证据")

    has_market_evidence = bool(
        tool_names.intersection({"get_market_status", "get_market_breadth", "get_index_data"})
    )
    if not has_market_evidence and re.search(
        r"(?:上证指数|深证成指|创业板指)[^。\n]{0,30}\d+(?:\.\d+)?%|"
        r"(?:上涨|下跌)(?:个股)?\s*\d+\s*家|"
        r"(?:两市|沪深两市)成交额[^。\n]{0,20}\d|"
        r"抗跌(?:性)?|跑赢大盘|弱于大盘",
        content,
    ):
        reasons.append("大盘涨跌、市场宽度或相对强弱结论没有市场工具证据")

    weekday_labels = "一二三四五六日"
    for match in re.finditer(
        r"(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?\s*[（(]周([一二三四五六日天])[）)]",
        content,
    ):
        try:
            stated_date = datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            ).date()
        except ValueError:
            continue
        stated_weekday = "日" if match.group(4) == "天" else match.group(4)
        actual_weekday = weekday_labels[stated_date.weekday()]
        if stated_weekday != actual_weekday:
            reasons.append(
                f"日期星期不一致：{stated_date.isoformat()} 应为周{actual_weekday}"
            )

    market_snapshot_tools = {
        "get_realtime_quotes", "get_kline", "get_history_data",
        "get_technical_indicators", "get_market_status", "get_market_breadth",
        "get_index_data", "get_multi_stock_snapshot",
    }
    if tool_names and tool_names.issubset(market_snapshot_tools) and re.search(r"今日|当天", content):
        evidence_dates = []
        for packet in evidence or []:
            result = packet.get("result") if isinstance(packet, dict) else None
            if not isinstance(result, dict):
                continue
            for raw_date in (result.get("data_time"), result.get("latest_trade_date")):
                match = re.search(r"20\d{2}-\d{2}-\d{2}", str(raw_date or ""))
                if match:
                    try:
                        evidence_dates.append(datetime.fromisoformat(match.group(0)).date())
                    except ValueError:
                        pass
        if evidence_dates and max(evidence_dates) < datetime.now().astimezone().date():
            reasons.append("行情证据全部来自之前的交易日，不能称为今日或当天数据")

    # Eastmoney single-quarter statements label Q4 as ``flow_basis=single_quarter``.
    # A model must not silently promote that Q4 cash flow into a full-year value.
    for packet in evidence or []:
        result = packet.get("result") if isinstance(packet, dict) else None
        if not isinstance(result, dict):
            continue
        for company in result.get("items") or []:
            if not isinstance(company, dict):
                continue
            financials = company.get("financials")
            if not isinstance(financials, dict):
                continue
            for period in financials.get("items") or []:
                if not isinstance(period, dict):
                    continue
                if period.get("flow_basis") != "single_quarter" or not str(period.get("report_period") or "").endswith("Q4"):
                    continue
                cash_flow = period.get("operating_cash_flow")
                try:
                    amount_yi = f"{float(cash_flow) / 100000000:.2f}"
                except (TypeError, ValueError):
                    continue
                if re.search(
                    rf"(?:全年|年度)[^。\n]{{0,24}}(?:经营现金流|OCF)[^。\n]{{0,16}}{re.escape(amount_yi)}|"
                    rf"(?:经营现金流|OCF)[^。\n]{{0,16}}{re.escape(amount_yi)}[^。\n]{{0,24}}(?:全年|年度)",
                    content,
                    flags=re.I,
                ):
                    reasons.append(
                        f"{period.get('report_period')} 经营现金流是单季度值，不能写成全年数据"
                    )

    batch_results = [
        item.get("result")
        for item in evidence or []
        if isinstance(item, dict)
        and item.get("tool") in {
            "get_multi_stock_snapshot",
            "get_multi_stock_decision_evidence",
        }
        and isinstance(item.get("result"), dict)
    ]
    only_simple_snapshots = batch_results and all(
        result.get("playbook") != "professional_investment_decision"
        for result in batch_results
    )
    has_structured_valuation = "get_valuation_ratios" in tool_names
    only_dynamic_quote_pe = (
        "get_realtime_quotes" in tool_names or bool(only_simple_snapshots)
    ) and not has_structured_valuation
    if only_dynamic_quote_pe and "PE(TTM)" in content:
        reasons.append("实时行情或批量快照只提供动态 PE，不能写成 PE(TTM)")
    if any(result.get("quote_is_intraday") for result in batch_results):
        cleaned = content.replace("不是收盘价", "")
        if re.search(r"(?:今日|当日|截至[^，。；]{0,12})?收盘价", cleaned):
            reasons.append("盘中快照不能写成收盘价")
    return reasons


def _professional_answer_contract_issues(
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Prove that a professional decision answer covered every required axis."""
    professional_buy_answer = _build_professional_buy_decision_answer(evidence)
    if professional_buy_answer is not None:
        return [] if content.strip() == professional_buy_answer.strip() else [
            "专业买入分析必须使用程序校验后的八维结果，不能由最终写作模型改写"
        ]
    result = next(
        (
            item.get("result")
            for item in evidence or []
            if isinstance(item, dict)
            and item.get("tool") == "get_multi_stock_decision_evidence"
            and isinstance(item.get("result"), dict)
            and item["result"].get("success") is not False
        ),
        None,
    )
    if not isinstance(result, dict):
        # A buy/hold/sell answer without the comprehensive evidence packet is
        # not allowed to fall back to model memory.  It may only fail closed
        # and explain that no decision can be made yet.
        failure_markers = ("证据不足", "证据缺失", "无法完成", "无法确认", "暂不做买入判断", "不提供买入结论")
        if any(marker in content for marker in failure_markers):
            return []
        return ["专业决策证据未成功取得，必须停止买入判断并说明证据缺口"]
    issues: List[str] = []
    for entity in result.get("resolved_entities") or []:
        if isinstance(entity, dict) and str(entity.get("symbol") or "") not in content:
            issues.append(f"遗漏公司 {entity.get('name') or entity.get('symbol')}")
    required_groups = {
        "业务兑现": ("业务", "主营", "兑现"),
        "财务质量": ("财务", "营收", "净利", "现金流"),
        "估值预期": ("估值", "PE", "PEG", "一致预期"),
        "交易状态": ("交易", "趋势", "技术", "资金"),
        "风险催化": ("风险", "公告", "催化"),
        "决策边界": (
            "成立条件", "失效条件", "等待验证", "暂不介入", "暂不买入", "风险规避",
        ),
    }
    for label, markers in required_groups.items():
        if not any(marker in content for marker in markers):
            issues.append(f"缺少{label}")
    return issues


def _sanitize_mapping_answer(content: str) -> str:
    """Drop unsupported company rows while preserving a valid mapping report.

    A model may include five properly sourced companies and one remembered
    concept stock.  Rejecting the whole report wastes good evidence; keeping
    the bad row violates the Playbook.  This deterministic pass removes only
    rows that fail code/name/source/date verification and records the omission.
    """
    lines = content.splitlines()
    in_company_table = False
    valid_row_count = 0
    removed: List[str] = []
    kept: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_company_table = False
            kept.append(line)
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            kept.append(line)
            continue
        if "公司" in cells[0] and "代码" in cells[0]:
            in_company_table = True
            kept.append(line)
            continue
        if not in_company_table or re.fullmatch(r"[:\- ]+", cells[0] or ""):
            kept.append(line)
            continue

        first_cell = cells[0]
        code_match = re.search(r"(?<!\d)(\d{6})(?!\d)", first_cell)
        resolved = find_securities_in_text(first_cell, limit=5) if code_match else []
        entity_matches = bool(
            code_match
            and any(item.get("symbol") == code_match.group(1) for item in resolved)
        )
        has_source = "http://" in stripped or "https://" in stripped
        has_date = bool(re.search(r"20\d{2}[-年/.]\d{1,2}", stripped))
        if entity_matches and has_source and has_date and "代码待核验" not in first_cell:
            kept.append(line)
            valid_row_count += 1
            continue
        removed.append(re.sub(r"[*_`]", "", first_cell).strip())

    if valid_row_count == 0:
        return content
    # Once rows are removed, any model-written count summary can become false
    # (for example “2 L2 + 2 L1” above a one-row table). Replace it with the
    # count proven by the surviving rows.
    normalized = [
        line
        for line in kept
        if not (
            line.lstrip().startswith("> 运行时逐行复核后")
            or (
                "本轮证据" in line
                and re.search(r"\d+\s*家", line)
                and re.search(r"L[123]", line)
            )
        )
    ]
    note = f"> 运行时逐行复核后，最终保留 **{valid_row_count} 家**代码、来源与日期均完整的代表公司。"
    insert_at = next(
        (
            index
            for index, line in enumerate(normalized)
            if line.startswith("### L") or line.lstrip().startswith("| 公司/代码")
        ),
        0,
    )
    normalized[insert_at:insert_at] = [note, ""]
    suffix = ""
    if removed:
        suffix = (
            "\n\n### 因证据校验未通过而未列入\n\n"
            + "、".join(dict.fromkeys(removed))
            + "：公司/代码、可点击来源或来源日期未同时通过本轮核验，故未列入代表公司；"
            "如需覆盖这些公司，应进一步核验公告、财报或公司级证据。"
        )
    return "\n".join(normalized).rstrip() + suffix


def _prepare_playbook_answer(playbook: Optional[AnalysisPlaybook], content: str) -> str:
    if playbook is not None and playbook.id == THEME_COMPANY_MAPPING.id:
        return _sanitize_mapping_answer(content)
    return content


def _playbook_answer_contract_issues(
    playbook: Optional[AnalysisPlaybook],
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Validate the final prose against the runtime-selected Playbook.

    Tool routing alone is not sufficient: a model can retrieve the right
    evidence and still collapse the answer into a loose opinion.  These checks
    are deliberately structural and conservative; they do not pretend to
    judge investment correctness, but they prevent required dimensions from
    disappearing during synthesis.
    """
    if playbook is None:
        return []
    if playbook.id == INVESTMENT_DECISION.id:
        professional_buy_answer = _build_professional_buy_decision_answer(evidence)
        if professional_buy_answer is None:
            return ["八维专业买入分析结果未成功取得，必须停止买入判断"]
        return [] if content.strip() == professional_buy_answer.strip() else [
            "八维专业买入分析只能由程序按已校验结构生成，不能由最终写作模型改写"
        ]
    if playbook.id == STOCK_DEEP_RESEARCH.id:
        return _professional_answer_contract_issues(content, evidence)

    issues: List[str] = []
    if playbook.id == INDUSTRY_CHAIN.id:
        required_groups = {
            "受益优先级": ("优先级", "排序", "最受益"),
            "产业链拆解": ("上游", "中游", "下游", "产业链"),
            "受益机制与兑现指标": ("受益机制", "价值量", "兑现指标", "订单", "产能"),
            "反证与风险": ("反证", "风险", "不及预期"),
            "持续跟踪项": ("跟踪", "量化指标", "观察指标"),
            "证据时间与置信度": ("来源", "截至", "数据时间", "置信度"),
        }
        for label, markers in required_groups.items():
            if not any(marker in content for marker in markers):
                issues.append(f"缺少{label}")
        if not (
            (
                "持续跟踪" in content
                or "后续跟踪" in content
                or re.search(r"^#{2,4}\s+.*跟踪", content, re.MULTILINE)
            )
            and ("指标" in content or "观察项" in content)
        ):
            issues.append("缺少独立的持续跟踪指标结尾")
        if "置信度" not in content:
            issues.append("缺少明确的整体置信度")
        # Source-link formatting is a presentation quality signal, not a fact
        # safety boundary.  The complete source packet remains visible in the
        # tool cards and evidence context.  Reject unsupported securities and
        # numbers below, but never discard an otherwise grounded industry
        # answer solely because the provider omitted a second Markdown link.
        return issues

    if playbook.id == THEME_COMPANY_MAPPING.id:
        valid_entities: List[Dict[str, str]] = []
        valid_codes: set[str] = set()
        data_rows: List[tuple[str, str, List[str]]] = []
        in_company_table = False
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                if in_company_table:
                    in_company_table = False
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not cells:
                continue
            if "公司" in cells[0] and "代码" in cells[0]:
                in_company_table = True
                continue
            if not in_company_table or re.fullmatch(r"[:\- ]+", cells[0] or ""):
                continue
            data_rows.append((cells[0], stripped, cells))
        for first_cell, row, cells in data_rows:
            code_match = re.search(r"(?<!\d)(\d{6})(?!\d)", first_cell)
            if not code_match:
                issues.append(f"公司第一列缺少六位代码: {first_cell}")
                continue
            code = code_match.group(1)
            resolved = find_securities_in_text(first_cell, limit=5)
            entity = next((item for item in resolved if item.get("symbol") == code), None)
            if entity is None:
                issues.append(f"公司名称与本地证券代码不匹配: {first_cell}")
                continue
            if "http://" not in row and "https://" not in row:
                issues.append(f"公司行缺少可点击来源: {first_cell}")
            if not re.search(r"20\d{2}[-年/.]\d{1,2}", row):
                issues.append(f"公司行缺少来源日期: {first_cell}")
            board_only_source = bool(re.search(
                r"(?:vip\.stock\.finance\.sina\.com\.cn/mkt|q\.10jqka\.com\.cn/gn/detail)",
                row,
            ))
            if board_only_source:
                segment = cells[1] if len(cells) > 1 else ""
                verified_fact = cells[3] if len(cells) > 3 else ""
                if not any(marker in segment for marker in ("待公司级", "概念关联", "主题候选")):
                    issues.append(f"板块证据不能直接确定产业链环节: {first_cell}")
                unsupported_business_markers = (
                    "龙头", "主营", "产品", "用于", "应用于", "订单", "收入", "客户",
                    "伺服", "减速器", "丝杠", "执行器", "整机", "系统集成", "营收", "净利润", "亏损",
                )
                if any(marker in verified_fact for marker in unsupported_business_markers):
                    issues.append(f"板块成员证据被扩写成未经核验的公司业务事实: {first_cell}")
            valid_entities.append(entity)
            valid_codes.add(code)
        candidate_result = next(
            (
                item.get("result")
                for item in evidence or []
                if isinstance(item, dict)
                and item.get("tool") == "get_theme_stock_candidates"
                and isinstance(item.get("result"), dict)
                and item["result"].get("success") is not False
            ),
            None,
        )
        candidate_items = (
            candidate_result.get("items")
            if isinstance(candidate_result, dict) and isinstance(candidate_result.get("items"), list)
            else []
        )
        candidate_codes = {
            str(item.get("symbol") or "")
            for item in candidate_items
            if isinstance(item, dict) and re.fullmatch(r"\d{6}", str(item.get("symbol") or ""))
        }
        codes_in_answer = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", content))
        missing_candidate_codes = sorted(candidate_codes - codes_in_answer)
        if missing_candidate_codes:
            issues.append(
                f"主题候选池返回{len(candidate_codes)}家公司，最终答案遗漏{len(missing_candidate_codes)}家；"
                "必须在完整候选索引中逐一列出公司/代码"
            )
        if not valid_entities and not (
            candidate_codes
            and not missing_candidate_codes
            and "L2/L3" in content
            and any(marker in content for marker in ("没有形成", "未形成", "0 家"))
        ):
            issues.append("没有把取得公司级直接证据的公司/六位代码放入Markdown表格第一列")
        if "代码待核验" in content or "代码缺失" in content:
            issues.append("存在未核验证券代码")
        required_groups = {
            "产业链环节": ("环节", "上游", "中游", "下游"),
            "L1/L2/L3证据等级": ("L1", "L2", "L3"),
            "已验证事实": ("已验证", "披露", "订单", "送样", "收入"),
            "证据边界": ("待核验", "仅概念", "不等同", "证据口径"),
            "来源与日期": ("来源", "公告", "财报", "日期", "截至", "20"),
        }
        if candidate_codes:
            required_groups["完整候选范围"] = ("完整候选池", "完整候选索引", "全部候选")
        for label, markers in required_groups.items():
            if not any(marker in content for marker in markers):
                issues.append(f"缺少{label}")
        return issues
    return []


def _generic_answer_contract_issues(
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Reject structurally incomplete answers even without a Playbook.

    A provider can terminate normally after emitting only a Markdown table
    header.  That is not a valid answer, especially when a successful batch
    tool already returned every requested company.
    """
    issues: List[str] = []
    lines = content.splitlines()
    for index in range(len(lines) - 1):
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        if not header.startswith("|") or not separator.startswith("|"):
            continue
        separator_cells = [cell.strip() for cell in separator.strip("|").split("|")]
        if not separator_cells or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator_cells):
            continue
        data_row_count = 0
        for row in lines[index + 2:]:
            stripped = row.strip()
            if not stripped:
                break
            if not stripped.startswith("|"):
                break
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and not all(re.fullmatch(r"[:\- ]+", cell or "") for cell in cells):
                data_row_count += 1
        if data_row_count == 0:
            issues.append("Markdown表格只有表头，没有任何数据行")

    batch_result = next(
        (
            packet.get("result")
            for packet in evidence or []
            if isinstance(packet, dict)
            and packet.get("tool") == "get_multi_stock_snapshot"
            and isinstance(packet.get("result"), dict)
            and packet["result"].get("success") is not False
        ),
        None,
    )
    batch_items = batch_result.get("items") if isinstance(batch_result, dict) else None
    if isinstance(batch_items, list):
        expected_codes = {
            str(item.get("symbol") or "")
            for item in batch_items
            if isinstance(item, dict) and re.fullmatch(r"\d{6}", str(item.get("symbol") or ""))
        }
        answer_codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", content))
        missing_codes = sorted(expected_codes - answer_codes)
        if missing_codes:
            issues.append(
                f"多股快照返回{len(expected_codes)}家公司，最终答案遗漏{len(missing_codes)}家："
                + "、".join(missing_codes)
            )
    return issues


def _final_answer_contract_issues(
    playbook: Optional[AnalysisPlaybook],
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    return (
        _unsupported_final_claims(content, evidence)
        + _generic_answer_contract_issues(content, evidence)
        + _playbook_answer_contract_issues(playbook, content, evidence)
    )


def _extract_model_reasoning_delta(delta: Any) -> str:
    """Read provider reasoning text without treating arbitrary model metadata as text."""
    if delta is None:
        return ""

    values: List[str] = []
    for field in ("reasoning_content", "reasoning"):
        value = delta.get(field) if isinstance(delta, dict) else getattr(delta, field, None)
        if isinstance(value, str) and value:
            values.append(value)

    # LiteLLM keeps provider-specific fields in model_extra for some response
    # models instead of exposing them as normal attributes.
    model_extra = delta.get("model_extra") if isinstance(delta, dict) else getattr(delta, "model_extra", None)
    if isinstance(model_extra, dict):
        for field in ("reasoning_content", "reasoning"):
            value = model_extra.get(field)
            if isinstance(value, str) and value and value not in values:
                values.append(value)
    return "".join(values)


def _append_model_reasoning(controller: ControllerLike, reasoning_delta: str) -> None:
    """Emit model-provided reasoning when the active stream controller supports it."""
    if not reasoning_delta:
        return
    append_reasoning = getattr(controller, "append_reasoning", None)
    if callable(append_reasoning):
        append_reasoning(reasoning_delta)


async def _stream_final_answer_without_tools(
    controller: ControllerLike,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    *,
    state: Optional[Dict[str, str]] = None,
    evidence: Optional[List[Dict[str, Any]]] = None,
    playbook: Optional[AnalysisPlaybook] = None,
    answer_validator: Optional[
        Callable[[str, Optional[List[Dict[str, Any]]]], List[str]]
    ] = None,
) -> str:
    """Force one final synthesis pass without tool use to avoid silent exits.

    state: 可选共享容器,累积最终答案文本,使外层在取消时能取到已生成内容。
    """
    forced_messages = _build_synthesis_messages(messages, evidence, playbook)

    def contract_issues(content: str) -> List[str]:
        issues = _final_answer_contract_issues(playbook, content, evidence)
        if answer_validator is not None:
            issues.extend(answer_validator(content, evidence))
        return list(dict.fromkeys(issues))

    is_professional_decision = playbook is not None and playbook.id == INVESTMENT_DECISION.id
    is_playbook_answer = playbook is not None

    kwargs = _build_llm_kwargs(
        llm_cfg,
        stream=True,
        messages=forced_messages,
        max_tokens=4200 if is_professional_decision else (3200 if is_playbook_answer else 2600),
        temperature=0.1,
    )

    content_text = ""
    finish_reason = ""
    try:
        synthesis_timeout = 120.0 if is_playbook_answer else FINAL_SYNTHESIS_TIMEOUT_SECONDS
        async with asyncio.timeout(synthesis_timeout):
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                choice = chunk.choices[0] if chunk.choices else None
                if choice and getattr(choice, "finish_reason", None):
                    finish_reason = str(choice.finish_reason)
                delta = choice.delta if choice else None
                _append_model_reasoning(controller, _extract_model_reasoning_delta(delta))
                if not delta or not delta.content:
                    continue
                content_text += delta.content
    except TimeoutError:
        content_text = _prepare_playbook_answer(playbook, content_text)
        logger.warning("[Agent] final synthesis timed out after buffering %d chars", len(content_text))
        # The provider can stop delivering the terminal frame after already
        # streaming a complete answer.  Because output is buffered, accept it
        # only if the same evidence and Playbook validators prove the required
        # structure is complete; otherwise fail closed with the deterministic
        # evidence fallback.
        timed_out_issues = contract_issues(content_text)
        if not content_text.strip() or timed_out_issues:
            logger.warning(
                "[Agent] timed-out synthesis incomplete: %s",
                "; ".join(timed_out_issues) if timed_out_issues else "empty response",
            )
            retry_messages = [*forced_messages]
            if content_text.strip():
                retry_messages.append({"role": "assistant", "content": content_text})
            retry_messages.append({
                "role": "user",
                "content": (
                    "[上一次综合超时]\n请基于同一份已核验证据直接给出更紧凑的完整最终答案，"
                    "不要复述任务或调用工具。必须满足 Playbook 输出合同，关键事实保留来源链接；"
                    "缺失证据明确标注，不得猜测。"
                    + (("当前缺项：" + "；".join(timed_out_issues)) if timed_out_issues else "")
                ),
            })
            retry_kwargs = _build_llm_kwargs(
                llm_cfg,
                stream=True,
                messages=retry_messages,
                max_tokens=4200 if is_professional_decision else 2800,
                temperature=0.0,
            )
            retry_text = ""
            retry_finish_reason = ""
            try:
                async with asyncio.timeout(120.0 if is_playbook_answer else FINAL_SYNTHESIS_TIMEOUT_SECONDS):
                    retry_response = await litellm.acompletion(**retry_kwargs)
                    async for chunk in retry_response:
                        choice = chunk.choices[0] if chunk.choices else None
                        if choice and getattr(choice, "finish_reason", None):
                            retry_finish_reason = str(choice.finish_reason)
                        delta = choice.delta if choice else None
                        _append_model_reasoning(controller, _extract_model_reasoning_delta(delta))
                        if delta and delta.content:
                            retry_text += delta.content
            except Exception:
                logger.exception("[Agent] timed-out synthesis retry failed")
            retry_text = _prepare_playbook_answer(playbook, retry_text)
            # Row-level evidence failures in mapping answers are removed
            # deterministically before the whole report is judged.
            retry_issues = contract_issues(retry_text)
            if (
                retry_text.strip()
                and retry_finish_reason.lower() not in {"length", "max_tokens"}
                and not retry_issues
            ):
                content_text = retry_text
            else:
                if retry_issues:
                    logger.warning("[Agent] rejected timeout retry: %s", "; ".join(retry_issues))
                content_text = _build_verified_evidence_fallback(evidence)
        controller.append_text(content_text)
        if state is not None:
            state["assistant_text"] = content_text
            controller.assistant_text_snapshot = content_text
        return content_text
    except Exception as e:
        logger.exception("[Agent] Forced final answer failed")
        content_text = _build_verified_evidence_fallback(evidence)
        controller.append_text(content_text)
        if state is not None:
            state["assistant_text"] = content_text
            controller.assistant_text_snapshot = content_text
        return content_text

    content_text = _prepare_playbook_answer(playbook, content_text)
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
            repair_kwargs = _build_llm_kwargs(
                llm_cfg,
                stream=True,
                messages=repair_messages,
                max_tokens=4200 if is_professional_decision else 3600,
                temperature=0.0,
            )
            repaired_text = ""
            repaired_finish_reason = ""
            try:
                repair_timeout = 120.0 if is_playbook_answer else FINAL_SYNTHESIS_TIMEOUT_SECONDS
                async with asyncio.timeout(repair_timeout):
                    repair_response = await litellm.acompletion(**repair_kwargs)
                    async for chunk in repair_response:
                        choice = chunk.choices[0] if chunk.choices else None
                        if choice and getattr(choice, "finish_reason", None):
                            repaired_finish_reason = str(choice.finish_reason)
                        delta = choice.delta if choice else None
                        _append_model_reasoning(controller, _extract_model_reasoning_delta(delta))
                        if delta and delta.content:
                            repaired_text += delta.content
            except Exception:
                logger.exception("[Agent] final answer repair failed")

            repaired_text = _prepare_playbook_answer(playbook, repaired_text)
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
                content_text = _build_verified_evidence_fallback(evidence)
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
        retry_kwargs = _build_llm_kwargs(
            llm_cfg,
            stream=True,
            messages=retry_messages,
            max_tokens=4200 if is_professional_decision else 2800,
            temperature=0.0,
        )
        retry_text = ""
        retry_finish_reason = ""
        try:
            async with asyncio.timeout(120.0):
                retry_response = await litellm.acompletion(**retry_kwargs)
                async for chunk in retry_response:
                    choice = chunk.choices[0] if chunk.choices else None
                    if choice and getattr(choice, "finish_reason", None):
                        retry_finish_reason = str(choice.finish_reason)
                    delta = choice.delta if choice else None
                    _append_model_reasoning(controller, _extract_model_reasoning_delta(delta))
                    if delta and delta.content:
                        retry_text += delta.content
        except Exception:
            logger.exception("[Agent] empty/truncated synthesis retry failed")
        retry_text = _prepare_playbook_answer(playbook, retry_text)
        retry_issues = contract_issues(retry_text)
        if (
            retry_text.strip()
            and retry_finish_reason.lower() not in {"length", "max_tokens"}
            and not retry_issues
        ):
            content_text = retry_text
            controller.append_text(content_text)
            if state is not None:
                state["assistant_text"] = content_text
                controller.assistant_text_snapshot = content_text
            return content_text
        if retry_issues:
            logger.warning("[Agent] rejected empty/truncated retry: %s", "; ".join(retry_issues))

    content_text = _build_verified_evidence_fallback(evidence)
    controller.append_text(content_text)
    if state is not None:
        state["assistant_text"] = content_text
        controller.assistant_text_snapshot = content_text
    return content_text


def _task_status_evidence(
    plan: TaskPlan,
    execution: PlanExecutionResult,
) -> Dict[str, Any]:
    return {
        "tool": "runtime_standard_task_status",
        "arguments": {},
        "result": {
            "success": execution.success,
            "tasks": [
                {
                    "task_id": result.task.task_id,
                    "kind": result.task.kind.value,
                    "objective": result.task.candidate.objective,
                    "status": result.status,
                    "blocked_reason": result.blocked_reason,
                    "errors": result.errors,
                    "executed_calls": sum(1 for call in result.calls if call.executed),
                    "reused_calls": sum(1 for call in result.calls if call.reused),
                }
                for result in execution.tasks
            ],
            "dependencies": {
                task.task_id: task.depends_on
                for task in plan.tasks
            },
        },
    }


def _blocked_task_answer(execution: PlanExecutionResult) -> Optional[str]:
    blocked = [task for task in execution.tasks if task.status == "blocked"]
    completed_with_evidence = any(task.calls for task in execution.tasks if task.status == "completed")
    if not blocked or completed_with_evidence:
        return None
    lines = ["## 本轮执行已由流程校验层停止", ""]
    for result in blocked:
        spec = workflow_for(result.task.kind)
        lines.append(f"- **{spec.title}**：{'；'.join(result.errors) or '不满足执行条件'}")
    lines.extend([
        "",
        "没有调用任何越权工具，也没有执行账户、删除、通知或其他高影响操作。",
    ])
    return "\n".join(lines)


def _exact_result_contract_answer(
    plan: TaskPlan,
    execution: PlanExecutionResult,
) -> Optional[str]:
    """Render only workflows whose exhaustive or safety result is machine-owned."""
    compound_filter_answer = _build_compound_collection_financial_filter_answer(
        plan,
        execution,
    )
    if compound_filter_answer:
        return compound_filter_answer
    if len(plan.tasks) != 1:
        return None
    task = plan.tasks[0]
    evidence = execution.evidence
    if task.kind == StandardTaskKind.THEME_STOCK_DISCOVERY:
        return _build_domain_candidate_answer(evidence)
    if task.kind == StandardTaskKind.COLLECTION_FINANCIAL_FILTER:
        try:
            spec = CollectionFinancialFilterSpec.model_validate(task.parameters)
        except Exception:
            return None
        return _build_collection_financial_filter_answer(evidence, spec)
    if task.kind == StandardTaskKind.STOCK_SCREENING:
        return _build_quantitative_screen_answer(evidence)
    if task.kind == StandardTaskKind.INVESTMENT_DECISION:
        return _build_professional_buy_decision_answer(evidence) or (
            "## 专业买入分析未完成\n\n"
            "本轮没有成功取得八维专业分析结果，因此没有输出任何买入结论。请重试本轮问题。"
        )
    if task.kind in {
        StandardTaskKind.WATCHLIST_QUERY,
        StandardTaskKind.WATCHLIST_MUTATION,
        StandardTaskKind.WATCHLIST_GROUP_MANAGEMENT,
        StandardTaskKind.FORMAL_ANALYSIS,
        StandardTaskKind.ANALYSIS_HISTORY,
        StandardTaskKind.ANALYSIS_TEMPLATE_MANAGEMENT,
        StandardTaskKind.BATCH_ANALYSIS,
        StandardTaskKind.BATCH_RUN_MANAGEMENT,
        StandardTaskKind.ANALYSIS_SCHEDULE_MANAGEMENT,
        StandardTaskKind.NOTIFICATION,
    }:
        return _build_workflow_evidence_fallback(evidence)
    return None


def _standard_task_answer_issues(
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Reject securities and material numeric claims absent from task evidence."""
    evidence_text = json.dumps(evidence or [], ensure_ascii=False, default=str)
    issues: List[str] = []

    evidence_codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", evidence_text))
    answer_codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", content))
    unsupported_codes = sorted(answer_codes - evidence_codes)
    if unsupported_codes:
        issues.append(
            "最终答案出现本轮证据未提供的证券代码：" + "、".join(unsupported_codes[:12])
        )
    evidence_symbols = {
        item["symbol"]
        for item in find_securities_in_text(evidence_text, limit=300)
        if item.get("symbol")
    }
    answer_entities = find_securities_in_text(content, limit=300)
    unsupported_entities = [
        item
        for item in answer_entities
        if item.get("symbol") not in evidence_symbols
    ]
    if unsupported_entities:
        issues.append(
            "最终答案出现本轮证据未提供的证券实体："
            + "、".join(
                f"{item.get('name')}({item.get('symbol')})"
                for item in unsupported_entities[:12]
            )
        )

    material_claim_pattern = re.compile(
        r"(?<![\d.])\d+(?:\.\d+)?(?:\s*[-—~至]\s*\d+(?:\.\d+)?)?\s*"
        r"(?:%|亿元|万元|万台|台|个|倍|家)"
    )
    missing_claims: List[str] = []
    for match in material_claim_pattern.finditer(content):
        claim = match.group(0)
        numbers = re.findall(r"\d+(?:\.\d+)?", claim)
        if all(re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])", evidence_text) for number in numbers):
            continue
        normalized = re.sub(r"\s+", "", claim)
        if normalized not in missing_claims:
            missing_claims.append(normalized)
    if missing_claims:
        issues.append(
            "最终答案出现本轮证据未提供的数量或比例：" + "、".join(missing_claims[:12])
        )
    return issues


async def _run_standard_task_pipeline(
    controller: ControllerLike,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    system_prompt: str = "",
    on_progress=None,
    *,
    state: Optional[Dict[str, Any]] = None,
    conversation_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Planner → fixed Workflow → policy validator → executor → aggregator."""
    latest_user_text = _last_user_text(messages)
    latest_user_entities = find_securities_in_text(latest_user_text, limit=100)
    semantic_context = ConversationContext.from_value(
        conversation_context
    ).before_request(latest_user_text)
    previous_answer_entities = (
        semantic_context.latest_entities()
        or _legacy_previous_answer_entities(messages)
    )
    controller.append_text("正在拆分标准任务并校验执行流程...\n\n")

    try:
        async with asyncio.timeout(STANDARD_TASK_PLAN_TIMEOUT_SECONDS):
            plan = await resolve_task_plan(
                messages,
                llm_cfg,
                completion=litellm.acompletion,
                current_entities=latest_user_entities,
                previous_answer_entities=previous_answer_entities,
                conversation_context=semantic_context,
            )
        logger.info(
            "[TaskPlanner] source=%s tasks=%s dependencies=%s",
            plan.source,
            [task.kind.value for task in plan.tasks],
            {task.task_id: task.depends_on for task in plan.tasks},
        )
    except TaskPlannerUnavailableError as exc:
        logger.exception("[TaskPlanner] semantic planner unavailable after recovery")
        reason_text = {
            "timeout": "模型规划响应超过当前等待时间",
            "connection": "模型规划服务连接失败",
            "provider": "模型规划服务暂时不可用",
        }.get(exc.reason, "模型规划服务暂时不可用")
        failure_text = (
            f"{reason_text}，本轮没有开放或调用任何数据工具。"
            "重新生成会再次尝试；只有已经通过程序校验的相同任务计划才会复用缓存。"
        )
        controller.append_text(failure_text)
        if state is not None:
            state["assistant_text"] = failure_text
            controller.assistant_text_snapshot = failure_text
        return failure_text
    except TimeoutError:
        logger.exception("[TaskPlanner] orchestration deadline exceeded")
        failure_text = (
            "模型规划响应超过本轮总等待时间，本轮没有开放或调用任何数据工具。"
            "重新生成会再次尝试；只有已经通过程序校验的相同任务计划才会复用缓存。"
        )
        controller.append_text(failure_text)
        if state is not None:
            state["assistant_text"] = failure_text
            controller.assistant_text_snapshot = failure_text
        return failure_text
    except Exception:
        logger.exception("[TaskPlanner] failed closed")
        failure_text = (
            "标准任务计划未通过程序校验，因此本轮没有调用任何数据工具。"
            "请补充具体对象或条件后重试。"
        )
        controller.append_text(failure_text)
        if state is not None:
            state["assistant_text"] = failure_text
            controller.assistant_text_snapshot = failure_text
        return failure_text

    if plan.needs_clarification:
        clarification = plan.clarification_question or "请补充本轮要执行的对象或条件。"
        controller.append_text(clarification)
        if state is not None:
            state["assistant_text"] = clarification
            controller.assistant_text_snapshot = clarification
        return clarification

    try:
        resolved_tasks = resolve_plan_entities(
            plan,
            current_entities=latest_user_entities,
            previous_answer_entities=previous_answer_entities,
            conversation_entities=semantic_context.all_entities(),
        )
    except TaskPlanValidationError as exc:
        logger.warning("[TaskPlanner] entity resolution blocked plan: %s", exc)
        failure_text = (
            "任务已经拆分，但所需证券对象未能通过本地 A 股证券库核验，"
            "所以没有调用数据工具。请明确公司名称或代码。"
        )
        controller.append_text(failure_text)
        if state is not None:
            state["assistant_text"] = failure_text
            controller.assistant_text_snapshot = failure_text
        return failure_text

    controller.append_text("正在执行已校验的标准任务...\n\n")
    workflow_specs_by_task = {
        task.task_id: workflow_for(task.kind)
        for task in resolved_tasks
    }

    async def run_workflow_call(
        call: WorkflowCall,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        call_id = f"workflow_{uuid.uuid4().hex}"
        tool = await controller.add_tool_call(call.tool_name, tool_call_id=call_id)
        tool.append_args_text(json.dumps(arguments, ensure_ascii=False))
        timeout_seconds = (
            QUANTITATIVE_SCREEN_TIMEOUT_SECONDS
            if call.tool_name == "screen_atr_volatility_stocks"
            else PROFESSIONAL_BUY_ANALYSIS_TIMEOUT_SECONDS
            if call.tool_name == "evaluate_multi_stock_buy_criteria"
            else CATALYST_ANALYSIS_TIMEOUT_SECONDS
            if call.tool_name == "analyze_stock_catalysts"
            else PROFESSIONAL_EVIDENCE_TIMEOUT_SECONDS
            if call.tool_name in {
                "get_multi_stock_snapshot",
                "get_multi_stock_decision_evidence",
                "get_domain_stock_candidates",
                "get_theme_stock_candidates",
            }
            else TOOL_EXECUTION_TIMEOUT_SECONDS
        )
        started_at = time.monotonic()
        max_attempts = workflow_specs_by_task[call.task_id].max_attempts
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            def execute_sync() -> Dict[str, Any]:
                if call.tool_name in STATEFUL_TOOL_NAMES:
                    raw_result = _registry.execute(call.tool_name, arguments)
                elif call.tool_name in ISOLATED_TOOL_NAMES:
                    raw_result = execute_tool_isolated(
                        call.tool_name,
                        arguments,
                        timeout_seconds=timeout_seconds - 3,
                    )
                else:
                    raw_result = _registry.execute(call.tool_name, arguments)
                compacted = _compact_tool_result(call.tool_name, raw_result)
                result = _maybe_attach_search_fallback(
                    call.tool_name,
                    arguments,
                    compacted,
                )
                return result if isinstance(result, dict) else {
                    "success": True,
                    "result": result,
                    "errors": [],
                    "partial": False,
                }

            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(execute_sync),
                    timeout=timeout_seconds,
                )
                succeeded = result.get("success") is not False
                if attempt > 1:
                    result = {**result, "runtime_attempts": attempt}
                tool.set_response(result, is_error=not succeeded)
                logger.info(
                    "[WorkflowTool] task=%s step=%s tool=%s success=%s attempts=%d duration_ms=%d",
                    call.task_id,
                    call.step_id,
                    call.tool_name,
                    succeeded,
                    attempt,
                    int((time.monotonic() - started_at) * 1000),
                )
                return result
            except (asyncio.TimeoutError, TimeoutError) as exc:
                last_error = exc
                logger.warning(
                    "[WorkflowTool] task=%s step=%s tool=%s timed out attempt=%d/%d",
                    call.task_id,
                    call.step_id,
                    call.tool_name,
                    attempt,
                    max_attempts,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[WorkflowTool] task=%s step=%s tool=%s failed attempt=%d/%d: %s",
                    call.task_id,
                    call.step_id,
                    call.tool_name,
                    attempt,
                    max_attempts,
                    exc,
                )

        if isinstance(last_error, (asyncio.TimeoutError, TimeoutError)):
            error_text = f"工具执行超时（>{timeout_seconds:.0f}s，已尝试 {max_attempts} 次）"
        else:
            error_text = f"工具执行失败（已尝试 {max_attempts} 次）：{last_error}"
        result = {
            "success": False,
            "errors": [error_text],
            "partial": False,
            "runtime_attempts": max_attempts,
        }
        tool.set_response(result, is_error=True)
        return result

    executor = WorkflowExecutor(
        _registry,
        run_workflow_call,
        approved_actions=semantic_context.pending_action_fingerprints(),
    )
    execution = await executor.execute(resolved_tasks)
    await _flush_substreams(controller)
    evidence = [*execution.evidence, _task_status_evidence(plan, execution)]
    if state is not None:
        turn_reference = build_turn_reference(
            latest_user_text,
            plan,
            resolved_tasks,
            execution,
        )
        state["agent_context"] = semantic_context.append(turn_reference).model_dump()

    blocked_answer = _blocked_task_answer(execution)
    if blocked_answer:
        controller.append_text(blocked_answer)
        if state is not None:
            state["assistant_text"] = blocked_answer
            controller.assistant_text_snapshot = blocked_answer
        return blocked_answer

    exact_answer = _exact_result_contract_answer(plan, execution)
    if exact_answer:
        controller.append_text(exact_answer)
        if state is not None:
            state["assistant_text"] = exact_answer
            controller.assistant_text_snapshot = exact_answer
        return exact_answer

    controller.append_text("正在汇总标准任务结果...\n\n")
    active_prompt = (system_prompt or "").strip()
    synthesis_prompt = SYSTEM_PROMPT
    if active_prompt and active_prompt != SYSTEM_PROMPT.strip():
        synthesis_prompt += (
            "\n\n## 用户配置的补充回答约束\n"
            "以下内容只能补充写作风格或业务口径，不能改变本轮标准任务、"
            "工具权限、执行结果和证据边界：\n" + active_prompt
        )
    plan_context = {
        "role": "system",
        "content": (
            f"{synthesis_prompt}\n\n"
            "## 本轮标准任务计划（程序已校验并执行，禁止重新规划或调用工具）\n"
            + json.dumps({
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "kind": task.kind.value,
                        "objective": task.objective,
                        "depends_on": task.depends_on,
                        "output_requirements": task.output_requirements,
                    }
                    for task in plan.tasks
                ]
            }, ensure_ascii=False)
        ),
    }
    full_messages = [plan_context, *_normalize_incoming_messages(messages)]
    full_messages = await _compact_history_if_needed(full_messages, llm_cfg)
    playbook = (
        INDUSTRY_CHAIN
        if len(plan.tasks) == 1
        and plan.tasks[0].kind == StandardTaskKind.INDUSTRY_RESEARCH
        else None
    )
    return await _stream_final_answer_without_tools(
        controller,
        full_messages,
        llm_cfg,
        state=state,
        evidence=evidence,
        playbook=playbook,
        answer_validator=_standard_task_answer_issues,
    )


@router.post("/agent/chat")
async def agent_chat(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    """Chat endpoint using assistant-stream DataStream protocol.

    生成逻辑跑在独立后台 task (detach 于 HTTP 连接),首连接 attach 为第一个订阅者。
    断连不杀生成 —— 后端继续跑完落库,用户刷新后可通过 /agent/chat/resume 续流。
    """
    limits = get_agent_runtime_limits()
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            # JSON UTF-8 can use up to four bytes per character.  Reject a
            # clearly oversized body before parsing it into memory; the exact
            # character limit is enforced again after parsing.
            if int(content_length) > limits.max_request_chars * 4:
                return JSONResponse(
                    status_code=413,
                    content={"error": "request_too_large", "message": "请求内容过大"},
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_content_length", "message": "Content-Length 格式不合法"},
            )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": "请求体不是有效 JSON"},
        )
    try:
        messages, conversation_id, resume_existing = validate_chat_request_body(body)
    except AgentRequestValidationError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": str(exc)},
        )

    session_service = ChatSessionService(db_manager)

    # Resume is an attachment operation, not a new model request.  It must not
    # require the current model configuration and must never create a new blank
    # conversation when a stale/invalid id is supplied.
    if resume_existing:
        if not conversation_id:
            return JSONResponse(
                status_code=400,
                content={"error": "conversation_id_required", "message": "恢复运行必须提供 conversation_id"},
            )
        if not session_service.get_conversation(conversation_id):
            return JSONResponse(
                status_code=404,
                content={"error": "conversation_not_found", "message": "对话不存在"},
            )
        active_run = active_run_registry.get(conversation_id)
        try:
            replay_from = max(0, int(body.get("after_chunk_index") or 0))
        except (TypeError, ValueError):
            replay_from = 0
        if active_run is not None:
            logger.info(
                "[Agent] chat attach existing run_id=%s conversation_id=%s from chunk %s status=%s",
                active_run.run_id,
                conversation_id,
                replay_from,
                active_run.status,
            )
            return DataStreamResponse(subscriber_stream(active_run, replay_from=replay_from))
        logger.info("[Agent] chat resume requested but no retained run for %s", conversation_id)
        return JSONResponse(
            status_code=409,
            content={"error": "run_not_active", "conversation_id": conversation_id},
        )

    retry_after = agent_request_rate_limiter.check_and_record(
        get_client_ip(request),
        limit=limits.requests_per_minute,
    )
    if retry_after:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "error": "agent_rate_limited",
                "message": "请求过于频繁，请稍后再试",
                "retry_after_seconds": retry_after,
            },
        )

    try:
        llm_cfg = _get_llm_config()
    except AgentModelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    conversation = session_service.ensure_conversation(conversation_id)
    conv_id = conversation["id"]
    agent_context = session_service.get_agent_context(conv_id)

    # 原子地「判定无活跃 run + 创建新 run」(锁内)。把判定与创建合并,消除
    # is_active(无锁)与 start_or_get(锁内)之间的竞态窗口:两个并发请求不会
    # 都通过检查、各自落库 messages 后第二个静默 attach 到第一个 run 而丢消息。
    # 拿到 None 表示已有活跃 run → 409 触发前端续流。
    try:
        run = await active_run_registry.try_claim(
            conv_id,
            max_active_runs=limits.max_active_runs,
        )
    except RunCapacityExceeded:
        logger.warning(
            "[Agent] global capacity exhausted conversation_id=%s active=%s limit=%s",
            conv_id,
            active_run_registry.stats()["active_runs"],
            limits.max_active_runs,
        )
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "5"},
            content={
                "error": "agent_busy",
                "message": "AI 助手当前任务较多，请稍后重试",
                "retry_after_seconds": 5,
            },
        )
    if run is None:
        logger.info("[Agent] chat rejected: run already in progress for %s", conv_id)
        return JSONResponse(
            status_code=409,
            content={"error": "run_in_progress", "conversation_id": conv_id},
        )

    logger.info(
        f"[Agent] Chat request with {len(messages)} messages, "
        f"model={llm_cfg['model']}, conversation_id={conv_id}, run_id={run.run_id}"
    )

    # 生成开始前同步落库本次完整 messages(含刚发的 user 消息)。
    # 这一步不能放在后台 task 里:用户一发送就刷新时,conversation detail 会
    # 先于后台 task 执行,如果库里还没有本次 user,前端只能恢复出空白历史。
    try:
        await asyncio.to_thread(
            session_service.save_conversation_snapshot,
            conv_id,
            list(messages),
            skip_title=True,
        )
    except Exception as exc:
        logger.exception("[Agent] failed to persist request snapshot conversation_id=%s", conv_id)
        await active_run_registry.mark_done(conv_id, "failed", error="request_snapshot_failed")
        raise HTTPException(status_code=500, detail="保存对话失败，请重试") from exc

    async def run_callback(controller: RunBroadcaster):
        from src.services.agent_prompt_service import AgentPromptService

        system_prompt, is_fallback = AgentPromptService(db_manager).get_active_system_prompt()
        logger.info(
            "[Agent] system prompt %s",
            "fallback to source default" if is_fallback else f"from template",
        )

        # 增量持久化:节流(>=3s 一次)把已生成 assistant 文本写库,刷新后可恢复
        last_save_ts = 0.0
        state: Dict[str, Any] = {"assistant_text": ""}

        async def on_progress(assistant_text_so_far: str) -> None:
            nonlocal last_save_ts
            # 同步镜像到 broadcaster,供续流端点补齐已生成文本
            controller.assistant_text_snapshot = assistant_text_so_far
            now = asyncio.get_running_loop().time()
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
            final_response_text = await _run_standard_task_pipeline(
                controller, messages, llm_cfg, system_prompt,
                on_progress=on_progress, state=state,
                conversation_context=agent_context,
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
            snapshot_kwargs: Dict[str, Any] = {}
            next_agent_context = (
                state.get("agent_context")
                if isinstance(state.get("agent_context"), dict)
                else agent_context
            )
            if next_agent_context:
                snapshot_kwargs["agent_context"] = next_agent_context
            await asyncio.to_thread(
                session_service.save_conversation_snapshot,
                conv_id,
                persisted_messages,
                **snapshot_kwargs,
            )
            await active_run_registry.mark_done(
                conv_id, "completed", final_text=final_response_text
            )
        except asyncio.CancelledError:
            # 后台 task 不被 HTTP 断连取消,仅进程关闭/显式 cancel 会到这。
            # 把已生成文本落定,避免残留 {conv_id}-assistant-pending 半截消息。
            partial = state.get("assistant_text", "")
            partial = (
                partial.rstrip() + "\n\n[已停止]"
                if partial.strip()
                else "[已停止]"
            )
            try:
                await asyncio.shield(asyncio.to_thread(
                    session_service.save_partial_assistant_text, conv_id, partial,
                ))
            except asyncio.CancelledError:
                try:
                    session_service.save_partial_assistant_text(conv_id, partial)
                except Exception:
                    logger.warning("[Agent] cancel-time partial save failed", exc_info=True)
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

    # run 已由前面的 try_claim 原子创建(判定 + 创建在同一锁内)。首连接必须先
    # subscribe 再启动后台 task,否则 task 可能在首个订阅者 subscribe 之前就
    # emit 完所有 chunk,导致首连收不到内容。
    first_queue = run.broadcaster.subscribe()
    try:
        await run.start(factory)
    except Exception as exc:
        run.broadcaster.unsubscribe(first_queue)
        await active_run_registry.mark_done(conv_id, "failed", error="run_start_failed")
        logger.exception("[Agent] failed to start run_id=%s", run.run_id)
        raise HTTPException(status_code=500, detail="AI 助手任务启动失败，请重试") from exc
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
    try:
        body = await request.json()
        body_for_validation = dict(body) if isinstance(body, dict) else body
        if isinstance(body_for_validation, dict):
            body_for_validation["resume_existing"] = True
        _, conversation_id, _ = validate_chat_request_body(body_for_validation)
    except AgentRequestValidationError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": str(exc)},
        )
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": "请求体不是有效 JSON"},
        )
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required")
    session_service = ChatSessionService(db_manager)
    if not session_service.get_conversation(conversation_id):
        # This endpoint is an idempotent attachment probe.  A deleted or stale
        # conversation has no resumable run, which is equivalent to inactive.
        return JSONResponse(status_code=200, content={"active": False})
    run = active_run_registry.get(conversation_id)
    if run is None or not run.is_running:
        return JSONResponse(status_code=200, content={"active": False})
    try:
        replay_from = max(0, int(body.get("after_chunk_index") or 0))
    except (TypeError, ValueError):
        replay_from = 0
    return DataStreamResponse(subscriber_stream(run, replay_from=replay_from))
