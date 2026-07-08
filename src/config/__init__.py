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
    FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT,
)
from src.config.env_helpers import parse_env_bool, parse_env_int, parse_env_float
from src.config.llm_config import (
    get_fixed_litellm_temperature,
    normalize_litellm_temperature,
    resolve_litellm_thinking_enabled,
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
    "setup_env",
    "parse_env_bool",
    "parse_env_int",
    "parse_env_float",
    "NEWS_STRATEGY_WINDOWS",
    "get_fixed_litellm_temperature",
    "normalize_litellm_temperature",
    "normalize_news_strategy_profile",
    "resolve_litellm_thinking_enabled",
    "resolve_news_window_days",
    "resolve_unified_llm_temperature",
    "resolve_proxy_and_configure_no_proxy",
    "FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT",
]
