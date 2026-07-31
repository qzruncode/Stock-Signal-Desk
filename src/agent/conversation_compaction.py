"""Conversation-history budget management for the Agent runtime.

This module contains the policy for deciding when to summarize old turns. It
does not know about FastAPI or the chat endpoint; model/tokenizer operations
are injected so callers and tests can control the provider boundary.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

COMPACT_RATIO = 0.8
KEEP_RECENT_MESSAGES = 6
SUMMARY_PROMPT = """\
你是对话压缩助手。下面是用户与 A 股分析助手的早期对话（含工具调用与结果）。
请把它压缩成一段紧凑的中文摘要，供后续对话引用。要求：
1. 保留所有出现过的股票代码、公司名称、关键数值（价格、涨跌幅、财务指标、日期）。
2. 保留已得出的分析结论与判断（如"近60天震荡上行""估值偏高""不可买入"等）。
3. 丢弃查询过程、工具调用细节、重复的中间数据明细。
4. 只输出摘要正文，不要加标题或额外说明。\
"""

TokenCounter = Callable[..., Any]
Completion = Callable[..., Any]
BuildKwargs = Callable[..., Dict[str, Any]]


def estimate_messages_tokens(
    messages: List[Dict[str, Any]],
    model: str,
    *,
    token_counter: TokenCounter,
) -> int:
    """Estimate message tokens and fall back to a conservative character count."""
    try:
        return int(token_counter(model=model, messages=messages))
    except Exception:
        total_chars = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                total_chars += sum(len(str(part)) for part in content)
            tool_calls = message.get("tool_calls")
            if tool_calls:
                total_chars += sum(len(json.dumps(call, ensure_ascii=False, default=str)) for call in tool_calls)
        return int(total_chars / 2.5) + len(messages) * 4


async def summarize_messages(
    llm_cfg: Dict[str, Any],
    messages: List[Dict[str, Any]],
    *,
    completion: Completion,
    build_kwargs: BuildKwargs,
) -> Optional[str]:
    """Ask the model for a compact summary, returning ``None`` on failure."""
    compact_messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                [{"role": message.get("role"), "content": message.get("content")} for message in messages],
                ensure_ascii=False,
                default=str,
            ),
        },
    ]
    kwargs = build_kwargs(llm_cfg, stream=False, messages=compact_messages)
    try:
        response = await completion(**kwargs)
        text = ""
        for choice in getattr(response, "choices", []) or []:
            message = getattr(choice, "message", None)
            if message and getattr(message, "content", None):
                text += str(message.content)
        return text.strip() or None
    except Exception:
        logger.warning("[Agent] compaction summary LLM call failed", exc_info=True)
        return None


async def compact_history_if_needed(
    full_messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    *,
    token_counter: TokenCounter,
    completion: Completion,
    build_kwargs: BuildKwargs,
) -> List[Dict[str, Any]]:
    """Summarize old turns when the conversation exceeds its model budget."""
    context_window = llm_cfg.get("context_window") or 200_000
    threshold = int(context_window * COMPACT_RATIO)
    tokens_before = estimate_messages_tokens(full_messages, llm_cfg["model"], token_counter=token_counter)
    if tokens_before < threshold:
        return full_messages

    if len(full_messages) <= KEEP_RECENT_MESSAGES + 1:
        logger.info(
            "[Agent] context over threshold (%d/%d) but too few messages to compact",
            tokens_before,
            threshold,
        )
        return full_messages

    to_summarize = full_messages[1:-KEEP_RECENT_MESSAGES]
    recent = full_messages[-KEEP_RECENT_MESSAGES:]
    if len(to_summarize) <= 1:
        return full_messages

    summary = await summarize_messages(
        llm_cfg,
        to_summarize,
        completion=completion,
        build_kwargs=build_kwargs,
    )
    if not summary:
        logger.warning(
            "[Agent] compaction skipped (summary empty), tokens=%d threshold=%d",
            tokens_before,
            threshold,
        )
        return full_messages

    compacted = [
        full_messages[0],
        {"role": "user", "content": f"[早期对话摘要]\n{summary}"},
        *recent,
    ]
    tokens_after = estimate_messages_tokens(compacted, llm_cfg["model"], token_counter=token_counter)
    logger.info(
        "[Agent] context compacted: %d msgs → summary, tokens %d → %d (threshold %d)",
        len(to_summarize),
        tokens_before,
        tokens_after,
        threshold,
    )
    return compacted


__all__ = [
    "COMPACT_RATIO",
    "KEEP_RECENT_MESSAGES",
    "SUMMARY_PROMPT",
    "compact_history_if_needed",
    "estimate_messages_tokens",
    "summarize_messages",
]
