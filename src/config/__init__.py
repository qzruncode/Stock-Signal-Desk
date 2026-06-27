# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 配置管理模块
===================================

职责：
1. 使用单例模式管理全局配置
2. 从 .env 文件加载敏感配置
3. 提供类型安全的配置访问接口
"""

# Re-export all public symbols for backward compatibility.
# All `from src.config import X` paths continue to work.

from src.config.config_dataclass import (
    Config,
    ConfigIssue,
    get_config,
    get_api_keys_for_model,
    extra_litellm_params,
    FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT,
)
from src.config.env_helpers import parse_env_bool, parse_env_int, parse_env_float
from src.config.llm_config import (
    _get_litellm_provider,
    _uses_direct_env_provider,
    ANSPIRE_LLM_BASE_URL_DEFAULT,
    ANSPIRE_LLM_MODEL_DEFAULT,
    SUPPORTED_LLM_CHANNEL_PROTOCOLS,
    canonicalize_llm_channel_protocol,
    channel_allows_empty_api_key,
    get_configured_llm_models,
    get_fixed_litellm_temperature,
    normalize_llm_channel_model,
    normalize_litellm_temperature,
    resolve_llm_channel_protocol,
    resolve_litellm_thinking_enabled,
    resolve_litellm_wire_model,
    resolve_unified_llm_temperature,
)
from src.config.news_config import (
    NEWS_STRATEGY_WINDOWS,
    normalize_news_strategy_profile,
    resolve_news_window_days,
)
from src.config.setup import setup_env
from src.config.proxy_config import resolve_proxy_and_configure_no_proxy

__all__ = [
    "Config",
    "ConfigIssue",
    "get_config",
    "get_api_keys_for_model",
    "extra_litellm_params",
    "setup_env",
    "parse_env_bool",
    "parse_env_int",
    "parse_env_float",
    "ANSPIRE_LLM_BASE_URL_DEFAULT",
    "ANSPIRE_LLM_MODEL_DEFAULT",
    "SUPPORTED_LLM_CHANNEL_PROTOCOLS",
    "NEWS_STRATEGY_WINDOWS",
    "canonicalize_llm_channel_protocol",
    "channel_allows_empty_api_key",
    "get_configured_llm_models",
    "get_fixed_litellm_temperature",
    "normalize_llm_channel_model",
    "normalize_litellm_temperature",
    "normalize_news_strategy_profile",
    "resolve_llm_channel_protocol",
    "resolve_litellm_thinking_enabled",
    "resolve_litellm_wire_model",
    "resolve_news_window_days",
    "resolve_unified_llm_temperature",
    "resolve_proxy_and_configure_no_proxy",
    "_get_litellm_provider",
    "_uses_direct_env_provider",
    "FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT",
]