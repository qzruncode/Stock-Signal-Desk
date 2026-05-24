# -*- coding: utf-8 -*-
"""
===================================
简化 AI 调用模块
===================================

职责：
1. 封装单次 AI 调用 — 每只股票一次调用
2. 模型自行通过联网搜索获取数据，无需工具
3. 支持自定义 system prompt（即分析模板）

"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Tuple, Optional

from src.storage import persist_llm_usage

logger = logging.getLogger(__name__)


def current_shanghai_timestamp() -> str:
    """Return the timestamp used in realtime-data instructions."""
    try:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S %Z").strip()


def build_realtime_data_policy(stock_code: str, stock_name: str) -> str:
    """Build the non-negotiable freshness policy injected into every AI call."""
    timestamp = current_shanghai_timestamp()
    return (
        "【实时数据硬性要求】\n"
        f"- 本次分析生成时间：{timestamp}。\n"
        f"- 分析对象：{stock_name}({stock_code})。\n"
        "- 必须主动联网检索并优先使用当前最新可获得数据。\n"
        "- 输出中必须写明关键数据来源名称和数据时间。\n"
    )


def with_realtime_data_policy(system_prompt: str, stock_code: str, stock_name: str) -> str:
    """Append freshness policy unless the caller already injected it."""
    base = (system_prompt or "").strip()
    if "【实时数据硬性要求】" in base:
        return base
    policy = build_realtime_data_policy(stock_code, stock_name)
    return f"{base}\n\n{policy}".strip()


def call_ai_for_stock(
    analyzer,  # GeminiAnalyzer
    system_prompt: str,
    stock_code: str,
    stock_name: str,
    *,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    stream_progress_callback=None,
) -> Tuple[str, str, Dict[str, Any]]:
    """对单只股票调用一次 AI 分析。

    system_prompt 来自用户选择的提示词模板，定义分析框架。
    模型自行联网搜索行情、新闻、财报等数据。

    Returns:
        (response_text, model_used, usage_dict)
    """
    effective_system_prompt = with_realtime_data_policy(system_prompt, stock_code, stock_name)
    user_prompt = (
        f"请分析股票 {stock_name}({stock_code})。"
        "必须联网搜索实时最新数据（行情、新闻、公告、财报、资金流向等），"
        "严格遵守系统提示词中的实时数据硬性要求。"
    )

    generation_config = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }

    try:
        response_text, model_used, usage = analyzer._call_litellm(
            user_prompt,
            generation_config,
            system_prompt=effective_system_prompt,
            stream=True,
            stream_progress_callback=stream_progress_callback,
        )
        persist_llm_usage(usage, model_used, call_type="batch_analysis")
        logger.info(
            "AI analysis complete for %s(%s): %d chars, model=%s",
            stock_name, stock_code, len(response_text), model_used,
        )
        return response_text, model_used, usage
    except Exception:
        logger.exception("AI call failed for %s(%s)", stock_name, stock_code)
        raise
