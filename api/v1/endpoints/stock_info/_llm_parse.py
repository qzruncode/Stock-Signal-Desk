# -*- coding: utf-8 -*-
"""LLM response parsers and generation orchestration for stock business analysis."""

from __future__ import annotations

import logging
from datetime import datetime

from api.v1.endpoints.stock_info._data import (
    _fetch_macro_data,
    _fetch_peer_data,
    _get_stock_industry,
)
from api.v1.endpoints.stock_info._llm_prompts import (
    format_llm_input,
    _build_environment_prompt,
    _build_track_quality_prompt,
    _build_catalyst_prompt,
    _build_business_prompt,
)

logger = logging.getLogger(__name__)


def _parse_environment_analysis(response_text: str, model_used: str, llm_input: str) -> dict:
    return {
        "analysis_type": "environment",
        "model_used": model_used,
        "llm_input": llm_input,
        "response": response_text,
        "generated_at": datetime.now().isoformat(),
    }


def _parse_track_quality_analysis(response_text: str, model_used: str, llm_input: str, peer_data: list[dict]) -> dict:
    return {
        "analysis_type": "track_quality",
        "model_used": model_used,
        "llm_input": llm_input,
        "response": response_text,
        "peers": peer_data[:5] if peer_data else [],
        "generated_at": datetime.now().isoformat(),
    }


def _parse_catalyst_analysis(response_text: str, model_used: str, llm_input: str) -> dict:
    return {
        "analysis_type": "catalyst",
        "model_used": model_used,
        "llm_input": llm_input,
        "response": response_text,
        "generated_at": datetime.now().isoformat(),
    }


def _generate_llm_business_analysis(
    symbol: str,
    intro: dict,
    composition: list[dict],
    profit_forecast: list[dict],
    financial_summary: dict,
    events: dict,
):
    """Generate LLM business analysis synchronously.

    模型/鉴权统一由 Anthropic 网关配置（ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL）决定，
    不再接受调用方传入的 model/api_key。
    """
    from src.llm.anthropic_gateway import build_litellm_kwargs, resolve_anthropic_gateway_config

    import litellm

    llm_cfg = resolve_anthropic_gateway_config()
    resolved_model = llm_cfg["model"]

    industry = _get_stock_industry(symbol)
    main_business = (intro.get("main_business") or intro.get("主营业务", ""))[:200] if isinstance(intro, dict) else ""
    macro_data = _fetch_macro_data()
    peer_data = _fetch_peer_data(industry, symbol)

    # Environment analysis
    env_system, env_user = _build_environment_prompt(symbol, industry, main_business, peer_data, macro_data)
    kwargs = build_litellm_kwargs(
        llm_cfg,
        stream=False,
        messages=[
            {"role": "system", "content": env_system},
            {"role": "user", "content": env_user},
        ],
    )

    try:
        env_response = litellm.completion(**kwargs)
        env_text = env_response.choices[0].message.content or ""
    except Exception as e:
        logger.warning("[StockBusiness] Environment analysis LLM call failed: %s", e)
        env_text = "环境分析暂不可用"
    env_input = format_llm_input(env_system, env_user)
    env_result = _parse_environment_analysis(env_text, resolved_model, env_input)

    # Track quality analysis
    tr_system, tr_user = _build_track_quality_prompt(
        symbol, industry, main_business, composition, financial_summary, peer_data
    )
    kwargs["messages"] = [
        {"role": "system", "content": tr_system},
        {"role": "user", "content": tr_user},
    ]
    try:
        tr_response = litellm.completion(**kwargs)
        tr_text = tr_response.choices[0].message.content or ""
    except Exception as e:
        logger.warning("[StockBusiness] Track quality LLM call failed: %s", e)
        tr_text = "经营质量分析暂不可用"
    tr_input = format_llm_input(tr_system, tr_user)
    tr_result = _parse_track_quality_analysis(tr_text, resolved_model, tr_input, peer_data)

    # Catalyst analysis
    cat_system, cat_user = _build_catalyst_prompt(symbol, industry, events, financial_summary)
    kwargs["messages"] = [
        {"role": "system", "content": cat_system},
        {"role": "user", "content": cat_user},
    ]
    try:
        cat_response = litellm.completion(**kwargs)
        cat_text = cat_response.choices[0].message.content or ""
    except Exception as e:
        logger.warning("[StockBusiness] Catalyst LLM call failed: %s", e)
        cat_text = "催化剂分析暂不可用"
    cat_input = format_llm_input(cat_system, cat_user)
    cat_result = _parse_catalyst_analysis(cat_text, resolved_model, cat_input)

    # Full business analysis
    biz_system, biz_user, llm_input = _build_business_prompt(
        symbol, intro, composition, profit_forecast, financial_summary, events
    )
    kwargs["messages"] = [
        {"role": "system", "content": biz_system},
        {"role": "user", "content": biz_user},
    ]
    try:
        biz_response = litellm.completion(**kwargs)
        biz_full_text = biz_response.choices[0].message.content or ""
    except Exception as e:
        logger.warning("[StockBusiness] Business analysis LLM call failed: %s", e)
        biz_full_text = "业务分析暂不可用"

    return {
        "symbol": symbol,
        "intro": intro,
        "composition": composition[:20] if composition else [],
        "profit_forecast": profit_forecast[:10] if profit_forecast else [],
        "financial_summary": financial_summary[-20:] if isinstance(financial_summary, list) else financial_summary,
        "events": events,
        "environment_analysis": env_result,
        "track_quality_analysis": tr_result,
        "catalyst_analysis": cat_result,
        "business_analysis": {
            "analysis_type": "business",
            "model_used": resolved_model,
            "llm_input": llm_input,
            "response": biz_full_text if biz_full_text else "业务分析暂不可用",
            "generated_at": datetime.now().isoformat(),
        },
        "generated_at": datetime.now().isoformat(),
    }
