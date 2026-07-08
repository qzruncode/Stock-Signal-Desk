# -*- coding: utf-8 -*-
"""LLM 生成参数辅助：thinking / temperature（正交于 Anthropic 网关接入）。

多供应商路由（channel protocol / model_list / agent model / provider 推断）已随
网关单源改造退役。本模块仅保留与具体供应商无关的生成参数解析，供 config_dataclass
与 generation_params 复用。
"""

import os
from typing import Any, Dict, List, Optional

from src.llm import generation_params as llm_generation_params


def resolve_litellm_thinking_enabled(
    model: str,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> Optional[bool]:
    """Resolve whether the outgoing LiteLLM request explicitly enables thinking."""
    return llm_generation_params.resolve_litellm_thinking_enabled(
        model,
        model_list=model_list,
        request_overrides=request_overrides,
    )


def get_fixed_litellm_temperature(
    model: str,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """Return a provider-mandated temperature for known strict models."""
    return llm_generation_params.get_fixed_litellm_temperature(
        model,
        model_list=model_list,
        request_overrides=request_overrides,
    )


def normalize_litellm_temperature(
    model: str,
    temperature: Optional[float],
    *,
    default: float = 0.7,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> float:
    """Normalize temperature before sending a LiteLLM request."""
    return llm_generation_params.normalize_litellm_temperature(
        model,
        temperature,
        default=default,
        model_list=model_list,
        request_overrides=request_overrides,
    )


def resolve_unified_llm_temperature(model: str) -> float:
    """Resolve the unified LLM temperature from ``LLM_TEMPERATURE``.

    网关单源后不再按供应商读取 ``GEMINI_TEMPERATURE`` / ``ANTHROPIC_TEMPERATURE`` 等
    已退役字段；统一只读 ``LLM_TEMPERATURE``，缺省 0.7。``model`` 参数保留以兼容
    既有调用签名，但不再影响结果。
    """
    llm_temperature_raw = os.getenv("LLM_TEMPERATURE")
    if llm_temperature_raw and llm_temperature_raw.strip():
        try:
            return float(llm_temperature_raw)
        except (ValueError, TypeError):
            pass
    return 0.7
