# -*- coding: utf-8 -*-
"""Config dataclass — the central singleton configuration object."""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse
from dotenv import dotenv_values

from src.report_language import (
    is_supported_report_language_value,
    normalize_report_language,
)
from src.config.env_helpers import parse_env_bool, parse_env_int, parse_env_float
from src.config.llm_config import (
    _get_litellm_provider,
    _uses_direct_env_provider,
    canonicalize_llm_channel_protocol,
    resolve_llm_channel_protocol,
    channel_allows_empty_api_key,
    normalize_llm_channel_model,
    get_configured_llm_models,
    resolve_unified_llm_temperature,
    SUPPORTED_LLM_CHANNEL_PROTOCOLS,
    ANSPIRE_LLM_BASE_URL_DEFAULT,
    ANSPIRE_LLM_MODEL_DEFAULT,
)
from src.config.news_config import (
    normalize_news_strategy_profile,
    resolve_news_window_days,
)
from src.config.proxy_config import resolve_proxy_and_configure_no_proxy
from src.config.setup import setup_env

logger = logging.getLogger(__name__)

FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT = 8.0
SUPPORTED_LLM_CHANNEL_PROTOCOLS_TUPLE = SUPPORTED_LLM_CHANNEL_PROTOCOLS


@dataclass
class ConfigIssue:
    """Structured configuration validation issue with a severity level."""

    severity: Literal["error", "warning", "info"]
    message: str
    field: str = ""

    def __str__(self) -> str:
        return self.message


@dataclass
class Config:
    """
    系统配置类 - 单例模式

    设计说明：
    - 使用 dataclass 简化配置属性定义
    - 所有配置项从环境变量读取，支持默认值
    - 类方法 get_instance() 实现单例访问
    """

    # === 自选股配置 ===
    stock_list: List[str] = field(default_factory=list)

    # === 飞书云文档配置 ===
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_folder_token: Optional[str] = None

    # === AI 分析配置 ===
    litellm_model: str = ""
    litellm_fallback_models: List[str] = field(default_factory=list)
    llm_temperature: float = 0.7
    llm_thinking_enabled: bool = False
    llm_reasoning_effort: str = "auto"

    litellm_config_path: Optional[str] = None
    llm_models_source: str = "legacy_env"
    llm_channels: List[Dict[str, Any]] = field(default_factory=list)
    llm_model_list: List[Dict[str, Any]] = field(default_factory=list)

    gemini_api_keys: List[str] = field(default_factory=list)
    anthropic_api_keys: List[str] = field(default_factory=list)
    openai_api_keys: List[str] = field(default_factory=list)
    deepseek_api_keys: List[str] = field(default_factory=list)

    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-3.1-pro-preview"
    gemini_model_fallback: str = "gemini-3-flash-preview"
    gemini_temperature: float = 0.7
    gemini_request_delay: float = 2.0
    gemini_max_retries: int = 5
    gemini_retry_delay: float = 5.0

    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_temperature: float = 0.7
    anthropic_max_tokens: int = 8192

    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: str = "gpt-5.5"
    openai_vision_model: Optional[str] = None
    openai_temperature: float = 0.7

    # === Vision 配置 ===
    vision_model: str = ""
    vision_provider_priority: str = "gemini,anthropic,openai"

    # === 搜索引擎配置 ===
    anspire_api_keys: List[str] = field(default_factory=list)
    bocha_api_keys: List[str] = field(default_factory=list)
    minimax_api_keys: List[str] = field(default_factory=list)
    tavily_api_keys: List[str] = field(default_factory=list)
    brave_api_keys: List[str] = field(default_factory=list)
    serpapi_keys: List[str] = field(default_factory=list)
    searxng_base_urls: List[str] = field(default_factory=list)
    searxng_public_instances_enabled: bool = True

    # === 新闻与分析筛选配置 ===
    news_max_age_days: int = 3
    news_strategy_profile: str = "short"
    bias_threshold: float = 5.0

    # === 通知配置 ===
    wechat_webhook_url: str = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=c8b160e1-d0c5-468b-96ea-4953832096b1"
    report_type: str = "simple"
    report_language: str = "zh"
    report_summary_only: bool = False
    report_show_llm_model: bool = True
    report_integrity_enabled: bool = True
    report_integrity_retry: int = 1
    report_history_compare_n: int = 0
    analysis_delay: float = 0.0
    wechat_max_bytes: int = 4000
    wechat_msg_type: str = "markdown"

    # === 数据库配置 ===
    database_path: str = "./data/stock_analysis.db"
    sqlite_wal_enabled: bool = True
    sqlite_busy_timeout_ms: int = 5000
    sqlite_write_retry_max: int = 3
    sqlite_write_retry_base_delay: float = 0.1
    save_context_snapshot: bool = True

    # === 日志配置 ===
    log_dir: str = "./logs"
    log_level: str = "INFO"

    # === 系统配置 ===
    max_workers: int = 3
    debug: bool = False
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None

    # === RSS 配置 ===
    rsshub_base_url: str = "http://127.0.0.1:1200"

    # === 定时任务配置 ===
    schedule_enabled: bool = False
    schedule_time: str = "18:00"
    schedule_run_immediately: bool = True
    run_immediately: bool = True
    trading_day_check_enabled: bool = True

    # === 实时行情增强数据配置 ===
    enable_realtime_quote: bool = True
    enable_realtime_technical_indicators: bool = True
    enable_chip_distribution: bool = True
    enable_eastmoney_patch: bool = False
    realtime_cache_ttl: int = 600

    # === 基本面配置 ===
    enable_fundamental_pipeline: bool = True
    fundamental_stage_timeout_seconds: float = FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT
    fundamental_fetch_timeout_seconds: float = 3.0
    fundamental_retry_max: int = 1
    fundamental_cache_ttl_seconds: int = 120
    fundamental_cache_max_entries: int = 256

    # === Portfolio 配置 ===
    portfolio_risk_concentration_alert_pct: float = 35.0
    portfolio_risk_drawdown_alert_pct: float = 15.0
    portfolio_risk_stop_loss_alert_pct: float = 10.0
    portfolio_risk_stop_loss_near_ratio: float = 0.8
    portfolio_risk_lookback_days: int = 180
    portfolio_fx_update_enabled: bool = True

    # === 流控配置 ===
    akshare_sleep_min: float = 2.0
    akshare_sleep_max: float = 5.0
    max_retries: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0

    # === WebUI 配置 ===
    webui_enabled: bool = False
    webui_host: str = "127.0.0.1"
    webui_port: int = 8000

    # === 机器人配置 ===
    bot_enabled: bool = True
    bot_command_prefix: str = "/"
    bot_rate_limit_requests: int = 10
    bot_rate_limit_window: int = 60
    bot_admin_users: List[str] = field(default_factory=list)

    feishu_verification_token: Optional[str] = None
    feishu_encrypt_key: Optional[str] = None
    feishu_stream_enabled: bool = False

    dingtalk_app_key: Optional[str] = None
    dingtalk_app_secret: Optional[str] = None
    dingtalk_stream_enabled: bool = False

    wecom_corpid: Optional[str] = None
    wecom_token: Optional[str] = None
    wecom_encoding_aes_key: Optional[str] = None
    wecom_agent_id: Optional[str] = None

    # === 配置校验模式 ===
    config_validate_mode: str = "warn"

    # --- Post-init validation ---
    _WEBUI_RUNTIME_ENV_FILE_PRIORITY_KEYS = frozenset(
        {
            "STOCK_LIST",
            "RUN_IMMEDIATELY",
            "SCHEDULE_ENABLED",
            "SCHEDULE_TIME",
            "SCHEDULE_RUN_IMMEDIATELY",
        }
    )
    _BOOTSTRAP_RUNTIME_ENV_OVERRIDES_CAPTURED = False
    _BOOTSTRAP_RUNTIME_ENV_OVERRIDES = frozenset()
    _BOOTSTRAP_RUNTIME_ENV_PRESENT_KEYS = frozenset()

    def __post_init__(self) -> None:
        pass

    _instance: Optional['Config'] = None

    @classmethod
    def get_instance(cls) -> 'Config':
        """获取配置单例实例"""
        if cls._instance is None:
            cls._instance = cls._load_from_env()
        return cls._instance

    @classmethod
    def _load_from_env(cls) -> 'Config':
        """从环境变量加载配置"""
        cls._capture_bootstrap_runtime_env_overrides()
        preexisting_report_language = os.environ.get("REPORT_LANGUAGE")

        setup_env()

        # Proxy setup (delegated to proxy_config for single-source-of-truth)
        http_proxy = os.getenv('HTTP_PROXY') or os.getenv('http_proxy')
        https_proxy = os.getenv('HTTPS_PROXY') or os.getenv('https_proxy')
        resolve_proxy_and_configure_no_proxy(http_proxy, https_proxy)

        # Parse stock list
        stock_list_str = cls._resolve_env_value('STOCK_LIST', default='', prefer_env_file=True)
        stock_list = [
            (c or "").strip().upper()
            for c in stock_list_str.split(',')
            if (c or "").strip()
        ]
        if not stock_list:
            stock_list = ['600519', '000001', '300750']

        # LLM multi-key parsing
        _gemini_keys_raw = os.getenv('GEMINI_API_KEYS', '')
        gemini_api_keys = [k.strip() for k in _gemini_keys_raw.split(',') if k.strip()]
        _single_gemini = os.getenv('GEMINI_API_KEY', '').strip()
        if not gemini_api_keys and _single_gemini:
            gemini_api_keys = [_single_gemini]

        _anthropic_keys_raw = os.getenv('ANTHROPIC_API_KEYS', '')
        anthropic_api_keys = [k.strip() for k in _anthropic_keys_raw.split(',') if k.strip()]
        _single_anthropic = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if not anthropic_api_keys and _single_anthropic:
            anthropic_api_keys = [_single_anthropic]

        _aihubmix = os.getenv('AIHUBMIX_KEY', '').strip()
        _openai_keys_raw = os.getenv('OPENAI_API_KEYS', '')
        openai_api_keys = [k.strip() for k in _openai_keys_raw.split(',') if k.strip()]
        if not openai_api_keys:
            _single_openai = os.getenv('OPENAI_API_KEY', '').strip()
            _fallback_key = _aihubmix or _single_openai
            if _fallback_key:
                openai_api_keys = [_fallback_key]
        openai_base_url = os.getenv('OPENAI_BASE_URL') or (
            'https://aihubmix.com/v1' if _aihubmix else None
        )

        _deepseek_keys_raw = os.getenv('DEEPSEEK_API_KEYS', '')
        deepseek_api_keys = [k.strip() for k in _deepseek_keys_raw.split(',') if k.strip()]
        if not deepseek_api_keys:
            _single_deepseek = os.getenv('DEEPSEEK_API_KEY', '').strip()
            if _single_deepseek:
                deepseek_api_keys = [_single_deepseek]

        anspire_keys_str = os.getenv('ANSPIRE_API_KEYS', '')
        anspire_api_keys = [k.strip() for k in anspire_keys_str.split(',') if k.strip()]
        anspire_llm_enabled = parse_env_bool(os.getenv('ANSPIRE_LLM_ENABLED'), default=True)
        anspire_llm_base_url = (
            os.getenv('ANSPIRE_LLM_BASE_URL') or ANSPIRE_LLM_BASE_URL_DEFAULT
        ).strip()
        _anspire_llm_model_env = os.getenv('ANSPIRE_LLM_MODEL', '').strip()
        anspire_channel_disabled = False
        for _raw_channel in os.getenv('LLM_CHANNELS', '').split(','):
            if _raw_channel.strip().lower() != "anspire":
                continue
            _channel_enabled_raw = os.getenv('LLM_ANSPIRE_ENABLED')
            if _channel_enabled_raw is not None and _channel_enabled_raw.strip():
                anspire_channel_disabled = not parse_env_bool(_channel_enabled_raw, default=True)
            else:
                anspire_channel_disabled = not anspire_llm_enabled
            break
        using_anspire_llm_legacy = bool(
            anspire_llm_enabled
            and not anspire_channel_disabled
            and anspire_api_keys
            and not openai_api_keys
        )
        if using_anspire_llm_legacy:
            openai_api_keys = list(anspire_api_keys)
            openai_base_url = anspire_llm_base_url

        litellm_model = os.getenv('LITELLM_MODEL', '').strip()
        inferred_legacy_deepseek_model = False
        _openai_model_env = os.getenv('OPENAI_MODEL', '').strip()
        if using_anspire_llm_legacy:
            _openai_model_name = _anspire_llm_model_env or _openai_model_env or ANSPIRE_LLM_MODEL_DEFAULT
        else:
            _openai_model_name = _openai_model_env or 'gpt-5.5'
        if not litellm_model:
            _gemini_model_name = os.getenv('GEMINI_MODEL', 'gemini-3.1-pro-preview').strip()
            _anthropic_model_name = os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-6').strip()
            if gemini_api_keys:
                litellm_model = f'gemini/{_gemini_model_name}'
            elif anthropic_api_keys:
                litellm_model = f'anthropic/{_anthropic_model_name}'
            elif deepseek_api_keys:
                litellm_model = 'deepseek/deepseek-chat'
                inferred_legacy_deepseek_model = True
            elif openai_api_keys:
                if '/' not in _openai_model_name:
                    litellm_model = f'openai/{_openai_model_name}'
                else:
                    litellm_model = _openai_model_name

        _fallback_str = os.getenv('LITELLM_FALLBACK_MODELS', '')
        if _fallback_str.strip():
            litellm_fallback_models = [m.strip() for m in _fallback_str.split(',') if m.strip()]
        else:
            _gemini_fallback = os.getenv('GEMINI_MODEL_FALLBACK', 'gemini-3-flash-preview').strip()
            if litellm_model.startswith('gemini/') and _gemini_fallback:
                _fb = f'gemini/{_gemini_fallback}' if '/' not in _gemini_fallback else _gemini_fallback
                litellm_fallback_models = [_fb]
            else:
                litellm_fallback_models = []

        litellm_config_path = os.getenv('LITELLM_CONFIG', '').strip() or None
        llm_models_source = "legacy_env"
        llm_channels: List[Dict[str, Any]] = []
        llm_model_list: List[Dict[str, Any]] = []

        if litellm_config_path:
            llm_model_list = cls._parse_litellm_yaml(litellm_config_path)
            if llm_model_list:
                llm_models_source = "litellm_config"

        if not llm_model_list:
            _channels_str = os.getenv('LLM_CHANNELS', '').strip()
            if _channels_str:
                llm_channels = cls._parse_llm_channels(_channels_str)
                llm_model_list = cls._channels_to_model_list(llm_channels)
                if llm_model_list:
                    llm_models_source = "llm_channels"

        if not llm_model_list:
            llm_model_list = cls._legacy_keys_to_model_list(
                gemini_api_keys, anthropic_api_keys, openai_api_keys,
                openai_base_url, deepseek_api_keys,
            )
            if llm_model_list:
                llm_models_source = "legacy_env"

        if (
            inferred_legacy_deepseek_model
            and llm_models_source == "legacy_env"
            and litellm_model == 'deepseek/deepseek-chat'
        ):
            logger.warning(
                "Deprecation warning:\n"
                "deepseek-chat will be deprecated on 2026-07-24,\n"
                "please migrate to deepseek-v4-flash."
            )

        if not litellm_model and llm_channels:
            for _ch in llm_channels:
                if _ch.get('models'):
                    litellm_model = _ch['models'][0]
                    break

        if not litellm_fallback_models and llm_channels and litellm_model:
            _all_ch_models: List[str] = []
            for _ch in llm_channels:
                _all_ch_models.extend(_ch.get('models', []))
            _seen = {litellm_model}
            litellm_fallback_models = [
                m for m in _all_ch_models
                if m not in _seen and not _seen.add(m)
            ]

        bocha_keys_str = os.getenv('BOCHA_API_KEYS', '')
        bocha_api_keys = [k.strip() for k in bocha_keys_str.split(',') if k.strip()]

        minimax_keys_str = os.getenv('MINIMAX_API_KEYS', '')
        minimax_api_keys = [k.strip() for k in minimax_keys_str.split(',') if k.strip()]

        tavily_keys_str = os.getenv('TAVILY_API_KEYS', '')
        tavily_api_keys = [k.strip() for k in tavily_keys_str.split(',') if k.strip()]

        serpapi_keys_str = os.getenv('SERPAPI_API_KEYS', '')
        serpapi_keys = [k.strip() for k in serpapi_keys_str.split(',') if k.strip()]

        brave_keys_str = os.getenv('BRAVE_API_KEYS', '')
        brave_api_keys = [k.strip() for k in brave_keys_str.split(',') if k.strip()]

        _raw_urls = [u.strip() for u in os.getenv('SEARXNG_BASE_URLS', '').split(',') if u.strip()]
        searxng_base_urls = []
        invalid_searxng_urls = []
        for u in _raw_urls:
            p = urlparse(u)
            if p.scheme in ('http', 'https') and p.netloc:
                searxng_base_urls.append(u)
            else:
                invalid_searxng_urls.append(u)
        if invalid_searxng_urls:
            logger.warning(
                "SEARXNG_BASE_URLS 中存在无效 URL，已忽略: %s",
                ", ".join(invalid_searxng_urls[:3]),
            )
        searxng_public_instances_enabled = parse_env_bool(
            os.getenv('SEARXNG_PUBLIC_INSTANCES_ENABLED'), default=True,
        )

        wechat_msg_type = os.getenv('WECHAT_MSG_TYPE', 'markdown')
        wechat_msg_type_lower = wechat_msg_type.lower()
        wechat_max_bytes_env = os.getenv('WECHAT_MAX_BYTES')
        if wechat_max_bytes_env not in (None, ''):
            wechat_max_bytes = parse_env_int(
                wechat_max_bytes_env,
                2048 if wechat_msg_type_lower == 'text' else 4000,
                field_name='WECHAT_MAX_BYTES',
                minimum=1,
            )
        else:
            wechat_max_bytes = 2048 if wechat_msg_type_lower == 'text' else 4000

        legacy_run_immediately_env = cls._resolve_env_value(
            'RUN_IMMEDIATELY',
            prefer_env_file=True,
        )
        legacy_run_immediately = (
            legacy_run_immediately_env.lower() == 'true'
            if legacy_run_immediately_env is not None
            else True
        )

        schedule_run_immediately_env = cls._resolve_env_value(
            'SCHEDULE_RUN_IMMEDIATELY',
            prefer_env_file=True,
        )
        if (
            not cls._had_bootstrap_runtime_env_key('SCHEDULE_RUN_IMMEDIATELY')
            and cls._has_bootstrap_runtime_env_override('RUN_IMMEDIATELY')
        ):
            schedule_run_immediately = legacy_run_immediately
        else:
            schedule_run_immediately = (
                schedule_run_immediately_env.lower() == 'true'
                if schedule_run_immediately_env is not None
                else legacy_run_immediately
            )
        schedule_time_value = cls._resolve_env_value(
            'SCHEDULE_TIME',
            default='18:00',
            prefer_env_file=True,
        )

        report_language_raw = cls._resolve_report_language_env_value(
            preexisting_report_language
        )
        report_show_llm_model_raw = os.getenv('REPORT_SHOW_LLM_MODEL')
        report_show_llm_model = parse_env_bool(report_show_llm_model_raw, default=True)
        if report_show_llm_model_raw is not None and not report_show_llm_model_raw.strip():
            report_show_llm_model = False

        return cls(
            stock_list=stock_list,
            feishu_app_id=os.getenv('FEISHU_APP_ID'),
            feishu_app_secret=os.getenv('FEISHU_APP_SECRET'),
            feishu_folder_token=os.getenv('FEISHU_FOLDER_TOKEN'),
            litellm_model=litellm_model,
            litellm_fallback_models=litellm_fallback_models,
            llm_temperature=resolve_unified_llm_temperature(litellm_model),
            llm_thinking_enabled=os.getenv('LLM_THINKING_ENABLED', 'false').lower() == 'true',
            llm_reasoning_effort=(os.getenv('LLM_REASONING_EFFORT') or 'auto').strip().lower(),
            litellm_config_path=litellm_config_path,
            llm_models_source=llm_models_source,
            llm_channels=llm_channels,
            llm_model_list=llm_model_list,
            gemini_api_keys=gemini_api_keys,
            anthropic_api_keys=anthropic_api_keys,
            openai_api_keys=openai_api_keys,
            deepseek_api_keys=deepseek_api_keys,
            gemini_api_key=os.getenv('GEMINI_API_KEY'),
            gemini_model=os.getenv('GEMINI_MODEL', 'gemini-3.1-pro-preview'),
            gemini_model_fallback=os.getenv('GEMINI_MODEL_FALLBACK', 'gemini-3-flash-preview'),
            gemini_temperature=parse_env_float(os.getenv('GEMINI_TEMPERATURE'), 0.7, field_name='GEMINI_TEMPERATURE'),
            gemini_request_delay=parse_env_float(os.getenv('GEMINI_REQUEST_DELAY'), 2.0, field_name='GEMINI_REQUEST_DELAY', minimum=0.0),
            gemini_max_retries=parse_env_int(os.getenv('GEMINI_MAX_RETRIES'), 5, field_name='GEMINI_MAX_RETRIES', minimum=0),
            gemini_retry_delay=parse_env_float(os.getenv('GEMINI_RETRY_DELAY'), 5.0, field_name='GEMINI_RETRY_DELAY', minimum=0.0),
            anthropic_api_key=os.getenv('ANTHROPIC_API_KEY'),
            anthropic_model=os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-6'),
            anthropic_temperature=parse_env_float(os.getenv('ANTHROPIC_TEMPERATURE'), 0.7, field_name='ANTHROPIC_TEMPERATURE'),
            anthropic_max_tokens=parse_env_int(os.getenv('ANTHROPIC_MAX_TOKENS'), 8192, field_name='ANTHROPIC_MAX_TOKENS', minimum=1),
            openai_api_key=openai_api_keys[0] if openai_api_keys else None,
            openai_base_url=openai_base_url,
            openai_model=_openai_model_name,
            openai_vision_model=os.getenv('OPENAI_VISION_MODEL') or None,
            openai_temperature=parse_env_float(os.getenv('OPENAI_TEMPERATURE'), 0.7, field_name='OPENAI_TEMPERATURE'),
            vision_model=(
                os.getenv('VISION_MODEL')
                or os.getenv('OPENAI_VISION_MODEL')
                or ""
            ),
            vision_provider_priority=os.getenv('VISION_PROVIDER_PRIORITY', 'gemini,anthropic,openai'),
            anspire_api_keys=anspire_api_keys,
            bocha_api_keys=bocha_api_keys,
            minimax_api_keys=minimax_api_keys,
            tavily_api_keys=tavily_api_keys,
            brave_api_keys=brave_api_keys,
            serpapi_keys=serpapi_keys,
            searxng_base_urls=searxng_base_urls,
            searxng_public_instances_enabled=searxng_public_instances_enabled,
            news_max_age_days=parse_env_int(os.getenv('NEWS_MAX_AGE_DAYS'), 3, field_name='NEWS_MAX_AGE_DAYS', minimum=1),
            news_strategy_profile=cls._parse_news_strategy_profile(os.getenv('NEWS_STRATEGY_PROFILE', 'short')),
            bias_threshold=parse_env_float(os.getenv('BIAS_THRESHOLD'), 5.0, field_name='BIAS_THRESHOLD', minimum=1.0),
            report_type=cls._parse_report_type(os.getenv('REPORT_TYPE', 'simple')),
            report_language=cls._parse_report_language(report_language_raw),
            report_summary_only=os.getenv('REPORT_SUMMARY_ONLY', 'false').lower() == 'true',
            report_show_llm_model=report_show_llm_model,
            report_integrity_enabled=os.getenv('REPORT_INTEGRITY_ENABLED', 'true').lower() == 'true',
            report_integrity_retry=parse_env_int(os.getenv('REPORT_INTEGRITY_RETRY'), 1, field_name='REPORT_INTEGRITY_RETRY', minimum=0),
            report_history_compare_n=parse_env_int(os.getenv('REPORT_HISTORY_COMPARE_N'), 0, field_name='REPORT_HISTORY_COMPARE_N', minimum=0),
            analysis_delay=parse_env_float(os.getenv('ANALYSIS_DELAY'), 0.0, field_name='ANALYSIS_DELAY', minimum=0.0),
            wechat_max_bytes=wechat_max_bytes,
            wechat_msg_type=wechat_msg_type_lower,
            database_path=os.getenv('DATABASE_PATH', './data/stock_analysis.db'),
            sqlite_wal_enabled=os.getenv('SQLITE_WAL_ENABLED', 'true').lower() == 'true',
            sqlite_busy_timeout_ms=parse_env_int(os.getenv('SQLITE_BUSY_TIMEOUT_MS'), 5000, field_name='SQLITE_BUSY_TIMEOUT_MS', minimum=0),
            sqlite_write_retry_max=parse_env_int(os.getenv('SQLITE_WRITE_RETRY_MAX'), 3, field_name='SQLITE_WRITE_RETRY_MAX', minimum=0),
            sqlite_write_retry_base_delay=parse_env_float(os.getenv('SQLITE_WRITE_RETRY_BASE_DELAY'), 0.1, field_name='SQLITE_WRITE_RETRY_BASE_DELAY', minimum=0.0),
            save_context_snapshot=os.getenv('SAVE_CONTEXT_SNAPSHOT', 'true').lower() == 'true',
            log_dir=os.getenv('LOG_DIR', './logs'),
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
            max_workers=parse_env_int(os.getenv('MAX_WORKERS'), 3, field_name='MAX_WORKERS', minimum=1),
            debug=os.getenv('DEBUG', 'false').lower() == 'true',
            config_validate_mode=os.getenv('CONFIG_VALIDATE_MODE', 'warn').lower(),
            http_proxy=os.getenv('HTTP_PROXY'),
            https_proxy=os.getenv('HTTPS_PROXY'),
            rsshub_base_url=(os.getenv('RSSHUB_BASE_URL') or 'http://127.0.0.1:1200').rstrip('/'),
            schedule_enabled=cls._resolve_env_value('SCHEDULE_ENABLED', default='false', prefer_env_file=True).lower() == 'true',
            schedule_time=(schedule_time_value or '18:00').strip() or '18:00',
            schedule_run_immediately=schedule_run_immediately,
            run_immediately=legacy_run_immediately,
            trading_day_check_enabled=os.getenv('TRADING_DAY_CHECK_ENABLED', 'true').lower() != 'false',
            webui_enabled=os.getenv('WEBUI_ENABLED', 'false').lower() == 'true',
            webui_host=os.getenv('WEBUI_HOST', '127.0.0.1'),
            webui_port=parse_env_int(os.getenv('WEBUI_PORT'), 8000, field_name='WEBUI_PORT', minimum=1, maximum=65535),
            bot_enabled=os.getenv('BOT_ENABLED', 'true').lower() == 'true',
            bot_command_prefix=os.getenv('BOT_COMMAND_PREFIX', '/'),
            bot_rate_limit_requests=parse_env_int(os.getenv('BOT_RATE_LIMIT_REQUESTS'), 10, field_name='BOT_RATE_LIMIT_REQUESTS', minimum=1),
            bot_rate_limit_window=parse_env_int(os.getenv('BOT_RATE_LIMIT_WINDOW'), 60, field_name='BOT_RATE_LIMIT_WINDOW', minimum=1),
            bot_admin_users=[u.strip() for u in os.getenv('BOT_ADMIN_USERS', '').split(',') if u.strip()],
            feishu_verification_token=os.getenv('FEISHU_VERIFICATION_TOKEN'),
            feishu_encrypt_key=os.getenv('FEISHU_ENCRYPT_KEY'),
            feishu_stream_enabled=os.getenv('FEISHU_STREAM_ENABLED', 'false').lower() == 'true',
            dingtalk_app_key=os.getenv('DINGTALK_APP_KEY'),
            dingtalk_app_secret=os.getenv('DINGTALK_APP_SECRET'),
            dingtalk_stream_enabled=os.getenv('DINGTALK_STREAM_ENABLED', 'false').lower() == 'true',
            wecom_corpid=os.getenv('WECOM_CORPID'),
            wecom_token=os.getenv('WECOM_TOKEN'),
            wecom_encoding_aes_key=os.getenv('WECOM_ENCODING_AES_KEY'),
            wecom_agent_id=os.getenv('WECOM_AGENT_ID'),
            enable_realtime_quote=os.getenv('ENABLE_REALTIME_QUOTE', 'true').lower() == 'true',
            enable_realtime_technical_indicators=os.getenv('ENABLE_REALTIME_TECHNICAL_INDICATORS', 'true').lower() == 'true',
            enable_chip_distribution=os.getenv('ENABLE_CHIP_DISTRIBUTION', 'true').lower() == 'true',
            enable_eastmoney_patch=os.getenv('ENABLE_EASTMONEY_PATCH', 'false').lower() == 'true',
            realtime_cache_ttl=parse_env_int(os.getenv('REALTIME_CACHE_TTL'), 600, field_name='REALTIME_CACHE_TTL', minimum=0),
            enable_fundamental_pipeline=os.getenv('ENABLE_FUNDAMENTAL_PIPELINE', 'true').lower() == 'true',
            fundamental_stage_timeout_seconds=parse_env_float(
                os.getenv('FUNDAMENTAL_STAGE_TIMEOUT_SECONDS'),
                FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT,
                field_name='FUNDAMENTAL_STAGE_TIMEOUT_SECONDS',
                minimum=0.0,
            ),
            fundamental_fetch_timeout_seconds=parse_env_float(
                os.getenv('FUNDAMENTAL_FETCH_TIMEOUT_SECONDS'),
                3.0,
                field_name='FUNDAMENTAL_FETCH_TIMEOUT_SECONDS',
                minimum=0.0,
            ),
            fundamental_retry_max=parse_env_int(os.getenv('FUNDAMENTAL_RETRY_MAX'), 1, field_name='FUNDAMENTAL_RETRY_MAX', minimum=0),
            fundamental_cache_ttl_seconds=parse_env_int(
                os.getenv('FUNDAMENTAL_CACHE_TTL_SECONDS'), 120, field_name='FUNDAMENTAL_CACHE_TTL_SECONDS', minimum=0,
            ),
            fundamental_cache_max_entries=parse_env_int(
                os.getenv('FUNDAMENTAL_CACHE_MAX_ENTRIES'), 256, field_name='FUNDAMENTAL_CACHE_MAX_ENTRIES', minimum=1,
            ),
            portfolio_risk_concentration_alert_pct=parse_env_float(
                os.getenv('PORTFOLIO_RISK_CONCENTRATION_ALERT_PCT'), 35.0, field_name='PORTFOLIO_RISK_CONCENTRATION_ALERT_PCT', minimum=0.0,
            ),
            portfolio_risk_drawdown_alert_pct=parse_env_float(
                os.getenv('PORTFOLIO_RISK_DRAWDOWN_ALERT_PCT'), 15.0, field_name='PORTFOLIO_RISK_DRAWDOWN_ALERT_PCT', minimum=0.0,
            ),
            portfolio_risk_stop_loss_alert_pct=parse_env_float(
                os.getenv('PORTFOLIO_RISK_STOP_LOSS_ALERT_PCT'), 10.0, field_name='PORTFOLIO_RISK_STOP_LOSS_ALERT_PCT', minimum=0.0,
            ),
            portfolio_risk_stop_loss_near_ratio=parse_env_float(
                os.getenv('PORTFOLIO_RISK_STOP_LOSS_NEAR_RATIO'), 0.8, field_name='PORTFOLIO_RISK_STOP_LOSS_NEAR_RATIO', minimum=0.0,
            ),
            portfolio_risk_lookback_days=parse_env_int(
                os.getenv('PORTFOLIO_RISK_LOOKBACK_DAYS'), 180, field_name='PORTFOLIO_RISK_LOOKBACK_DAYS', minimum=1,
            ),
            portfolio_fx_update_enabled=os.getenv('PORTFOLIO_FX_UPDATE_ENABLED', 'true').lower() == 'true',
        )

    @classmethod
    def _parse_litellm_yaml(cls, config_path: str) -> List[Dict[str, Any]]:
        import logging as _logging
        _logger = _logging.getLogger(__name__)
        try:
            import yaml
        except ImportError:
            _logger.warning("PyYAML not installed; LITELLM_CONFIG ignored. Install with: pip install pyyaml")
            return []

        path = Path(config_path)
        if not path.is_absolute():
            path = Path(__file__).parent.parent / path
        if not path.exists():
            _logger.warning(f"LITELLM_CONFIG file not found: {path}")
            return []

        try:
            with open(path, encoding='utf-8') as f:
                yaml_config = yaml.safe_load(f) or {}
        except Exception as e:
            _logger.warning(f"Failed to parse LITELLM_CONFIG: {e}")
            return []

        model_list = yaml_config.get('model_list', [])
        if not isinstance(model_list, list):
            _logger.warning("LITELLM_CONFIG: model_list must be a list")
            return []

        for entry in model_list:
            params = entry.get('litellm_params', {})
            for key in list(params.keys()):
                val = params.get(key)
                if isinstance(val, str) and val.startswith('os.environ/'):
                    env_name = val.split('/', 1)[1]
                    params[key] = os.getenv(env_name, '')

        _logger.info(f"LITELLM_CONFIG: loaded {len(model_list)} model deployment(s) from {path}")
        return model_list

    @classmethod
    def _parse_llm_channels(cls, channels_str: str) -> List[Dict[str, Any]]:
        import logging as _logging
        _logger = _logging.getLogger(__name__)

        channels: List[Dict[str, Any]] = []
        for raw_name in channels_str.split(','):
            ch_name = raw_name.strip()
            if not ch_name:
                continue
            ch_lower = ch_name.lower()
            ch_upper = ch_name.upper()

            base_url = os.getenv(f'LLM_{ch_upper}_BASE_URL', '').strip() or None
            if ch_lower == "anspire" and not base_url:
                base_url = (
                    os.getenv('ANSPIRE_LLM_BASE_URL') or ANSPIRE_LLM_BASE_URL_DEFAULT
                ).strip() or None
            protocol_raw = os.getenv(f'LLM_{ch_upper}_PROTOCOL', '').strip()
            if ch_lower == "anspire" and not protocol_raw:
                protocol_raw = "openai"
            enabled_raw = os.getenv(f'LLM_{ch_upper}_ENABLED')
            if ch_lower == "anspire" and (enabled_raw is None or not enabled_raw.strip()):
                enabled_raw = os.getenv('ANSPIRE_LLM_ENABLED')
            enabled = parse_env_bool(enabled_raw, default=True)

            api_keys_raw = os.getenv(f'LLM_{ch_upper}_API_KEYS', '')
            api_keys = [k.strip() for k in api_keys_raw.split(',') if k.strip()]
            if not api_keys:
                single_key = os.getenv(f'LLM_{ch_upper}_API_KEY', '').strip()
                if single_key:
                    api_keys = [single_key]
            if not api_keys and ch_lower == "anspire":
                anspire_keys_raw = os.getenv('ANSPIRE_API_KEYS', '')
                api_keys = [k.strip() for k in anspire_keys_raw.split(',') if k.strip()]

            models_raw = os.getenv(f'LLM_{ch_upper}_MODELS', '')
            raw_models = [m.strip() for m in models_raw.split(',') if m.strip()]
            if not raw_models and ch_lower == "anspire":
                anspire_model = (os.getenv('ANSPIRE_LLM_MODEL') or ANSPIRE_LLM_MODEL_DEFAULT).strip()
                if anspire_model:
                    raw_models = [anspire_model]
            protocol = resolve_llm_channel_protocol(protocol_raw, base_url=base_url, models=raw_models, channel_name=ch_name)
            models = [normalize_llm_channel_model(m, protocol, base_url) for m in raw_models]

            extra_headers_raw = os.getenv(f'LLM_{ch_upper}_EXTRA_HEADERS', '').strip()
            extra_headers = None
            if extra_headers_raw:
                try:
                    extra_headers = json.loads(extra_headers_raw)
                except json.JSONDecodeError:
                    _logger.warning(f"LLM_{ch_upper}_EXTRA_HEADERS: invalid JSON, ignored")

            if not enabled:
                _logger.info(f"LLM channel '{ch_name}': disabled, skipped")
                continue

            if protocol_raw and canonicalize_llm_channel_protocol(protocol_raw) not in SUPPORTED_LLM_CHANNEL_PROTOCOLS:
                _logger.warning(
                    "LLM_%s_PROTOCOL=%s is unsupported; auto-detected protocol=%s",
                    ch_upper, protocol_raw, protocol or "unknown",
                )

            if not api_keys and channel_allows_empty_api_key(protocol, base_url):
                api_keys = [""]

            if not api_keys:
                _logger.warning(f"LLM channel '{ch_name}': no API key configured, skipped")
                continue
            if not models:
                _logger.warning(f"LLM channel '{ch_name}': no models configured, skipped")
                continue

            channels.append({
                'name': ch_name.lower(),
                'protocol': protocol,
                'enabled': enabled,
                'base_url': base_url,
                'api_keys': api_keys,
                'models': models,
                'extra_headers': extra_headers,
            })
            _logger.info(f"LLM channel '{ch_name}': {len(models)} model(s), {len(api_keys)} key(s)")

        return channels

    @classmethod
    def _channels_to_model_list(cls, channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        model_list: List[Dict[str, Any]] = []
        for ch in channels:
            for model_name in ch['models']:
                for api_key in ch['api_keys']:
                    litellm_params: Dict[str, Any] = {'model': model_name}
                    if api_key:
                        litellm_params['api_key'] = api_key
                    if ch['base_url']:
                        litellm_params['api_base'] = ch['base_url']
                    headers = dict(ch.get('extra_headers') or {})
                    if ch['base_url'] and 'aihubmix.com' in ch['base_url']:
                        headers.setdefault('APP-Code', 'GPIJ3886')
                    if headers:
                        litellm_params['extra_headers'] = headers
                    model_list.append({
                        'model_name': model_name,
                        'litellm_params': litellm_params,
                    })
        return model_list

    @classmethod
    def _legacy_keys_to_model_list(
        cls,
        gemini_keys: List[str],
        anthropic_keys: List[str],
        openai_keys: List[str],
        openai_base_url: Optional[str],
        deepseek_keys: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        model_list: List[Dict[str, Any]] = []
        for k in gemini_keys:
            if k and len(k) >= 8:
                model_list.append({
                    'model_name': '__legacy_gemini__',
                    'litellm_params': {'model': '__legacy_gemini__', 'api_key': k},
                })
        for k in anthropic_keys:
            if k and len(k) >= 8:
                model_list.append({
                    'model_name': '__legacy_anthropic__',
                    'litellm_params': {'model': '__legacy_anthropic__', 'api_key': k},
                })
        for k in openai_keys:
            if k and len(k) >= 8:
                params: Dict[str, Any] = {'model': '__legacy_openai__', 'api_key': k}
                if openai_base_url:
                    params['api_base'] = openai_base_url
                if openai_base_url and 'aihubmix.com' in openai_base_url:
                    params['extra_headers'] = {'APP-Code': 'GPIJ3886'}
                model_list.append({
                    'model_name': '__legacy_openai__',
                    'litellm_params': params,
                })
        for k in (deepseek_keys or []):
            if k and len(k) >= 8:
                model_list.append({
                    'model_name': '__legacy_deepseek__',
                    'litellm_params': {'model': '__legacy_deepseek__', 'api_key': k},
                })
        return model_list

    @classmethod
    def _parse_report_type(cls, value: str) -> str:
        v = (value or 'simple').strip().lower()
        if v in ('simple', 'full', 'brief'):
            return v
        import logging as _logging
        _logging.getLogger(__name__).warning(
            f"REPORT_TYPE '{value}' invalid, fallback to 'simple' (valid: simple/full/brief)"
        )
        return 'simple'

    @classmethod
    def _get_env_file_value(cls, key: str) -> Optional[str]:
        env_file = os.getenv("ENV_FILE")
        env_path = Path(env_file) if env_file else (Path(__file__).parent.parent / ".env")
        if not env_path.exists():
            return None
        try:
            env_values = dotenv_values(env_path)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to read %s while resolving %s: %s", env_path, key, exc,
            )
            return None
        value = env_values.get(key)
        if value is None:
            return None
        return str(value)

    @classmethod
    def _resolve_env_value(
        cls,
        key: str,
        *,
        default: Optional[str] = None,
        prefer_env_file: bool = False,
    ) -> Optional[str]:
        env_value = os.getenv(key)
        file_value = cls._get_env_file_value(key)
        should_prefer_file = prefer_env_file or key in cls._WEBUI_RUNTIME_ENV_FILE_PRIORITY_KEYS
        if should_prefer_file and file_value is not None:
            if env_value is not None and cls._has_bootstrap_runtime_env_override(key):
                return env_value
            return file_value
        if env_value is not None:
            return env_value
        if file_value is not None:
            return file_value
        return default

    @classmethod
    def _capture_bootstrap_runtime_env_overrides(cls) -> None:
        if cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES_CAPTURED:
            return
        explicit_overrides = set()
        present_keys = set()
        for key in cls._WEBUI_RUNTIME_ENV_FILE_PRIORITY_KEYS:
            env_value = os.environ.get(key)
            if env_value is None:
                continue
            present_keys.add(key)
            file_value = cls._get_env_file_value(key)
            if file_value is None or env_value != file_value:
                explicit_overrides.add(key)
        cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES = frozenset(explicit_overrides)
        cls._BOOTSTRAP_RUNTIME_ENV_PRESENT_KEYS = frozenset(present_keys)
        cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES_CAPTURED = True

    @classmethod
    def _has_bootstrap_runtime_env_override(cls, key: str) -> bool:
        cls._capture_bootstrap_runtime_env_overrides()
        return key in cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES

    @classmethod
    def _had_bootstrap_runtime_env_key(cls, key: str) -> bool:
        cls._capture_bootstrap_runtime_env_overrides()
        return key in cls._BOOTSTRAP_RUNTIME_ENV_PRESENT_KEYS

    @classmethod
    def _resolve_report_language_env_value(
        cls,
        preexisting_env_value: Optional[str],
    ) -> str:
        file_value = cls._get_env_file_value("REPORT_LANGUAGE")
        env_value = os.getenv("REPORT_LANGUAGE")
        if preexisting_env_value is not None:
            env_text = preexisting_env_value.strip()
            file_text = (file_value or "").strip()
            if file_text and env_text and env_text.lower() != file_text.lower():
                env_file = os.getenv("ENV_FILE") or str(Path(__file__).parent.parent / ".env")
                logging.getLogger(__name__).warning(
                    "REPORT_LANGUAGE environment value '%s' overrides %s ('%s')",
                    preexisting_env_value, env_file, file_value,
                )
            return preexisting_env_value
        if file_value is not None:
            return file_value
        return env_value or "zh"

    @classmethod
    def _parse_report_language(cls, value: Optional[str]) -> str:
        normalized = normalize_report_language(value, default="zh")
        raw = (value or "").strip()
        if raw and not is_supported_report_language_value(raw):
            logging.getLogger(__name__).warning(
                "REPORT_LANGUAGE '%s' invalid, fallback to 'zh' (valid: zh/en)", value,
            )
        return normalized

    @classmethod
    def _parse_news_strategy_profile(cls, value: Optional[str]) -> str:
        normalized = normalize_news_strategy_profile(value)
        raw = (value or "short").strip().lower()
        if raw != normalized:
            logging.getLogger(__name__).warning(
                "NEWS_STRATEGY_PROFILE '%s' invalid, fallback to 'short' "
                "(valid: ultra_short/short/medium/long)", value,
            )
        return normalized

    def get_effective_news_window_days(self) -> int:
        return resolve_news_window_days(
            news_max_age_days=self.news_max_age_days,
            news_strategy_profile=self.news_strategy_profile,
        )

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (primarily for testing)."""
        cls._instance = None
        cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES_CAPTURED = False
        cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES = frozenset()
        cls._BOOTSTRAP_RUNTIME_ENV_PRESENT_KEYS = frozenset()

    def has_searxng_enabled(self) -> bool:
        return bool(self.searxng_base_urls) or bool(self.searxng_public_instances_enabled)

    def has_search_capability_enabled(self) -> bool:
        return bool(
            self.anspire_api_keys
            or self.bocha_api_keys
            or self.minimax_api_keys
            or self.tavily_api_keys
            or self.brave_api_keys
            or self.serpapi_keys
            or self.has_searxng_enabled()
        )

    def refresh_stock_list(self) -> None:
        env_file = os.getenv("ENV_FILE")
        env_path = Path(env_file) if env_file else (Path(__file__).parent.parent / '.env')
        stock_list_str = ''
        if env_path.exists():
            env_values = dotenv_values(env_path)
            stock_list_str = (env_values.get('STOCK_LIST') or '').strip()
        if not stock_list_str:
            stock_list_str = os.getenv('STOCK_LIST', '')
        stock_list = [
            (c or "").strip().upper()
            for c in stock_list_str.split(',')
            if (c or "").strip()
        ]
        if not stock_list:
            stock_list = ['000001']
        self.stock_list = stock_list

    def validate_structured(self) -> List[ConfigIssue]:
        issues: List[ConfigIssue] = []

        if not self.stock_list:
            issues.append(ConfigIssue(severity="error", message="未配置自选股列表 (STOCK_LIST)", field="STOCK_LIST"))

        has_direct_env_model = bool(self.litellm_model) and _uses_direct_env_provider(self.litellm_model)
        if not self.llm_model_list and not has_direct_env_model:
            issues.append(ConfigIssue(
                severity="error",
                message="未配置任何可用的 AI 模型接入（高级模型路由配置 / 渠道 / API Key），AI 分析功能将不可用",
                field="LITELLM_CONFIG",
            ))
        elif not self.litellm_model:
            issues.append(ConfigIssue(
                severity="info",
                message="尚未明确指定主模型，系统将自动从可用 API Key 推断。建议尽早配置主模型（格式如 gemini/gemini-3.1-pro-preview）",
                field="LITELLM_MODEL",
            ))

        available_router_models = get_configured_llm_models(self.llm_model_list)
        available_router_model_set = set(available_router_models)

        def _has_runtime_source_for_model(model: str) -> bool:
            if not model or _uses_direct_env_provider(model):
                return True
            provider = _get_litellm_provider(model)
            if provider in {"gemini", "vertex_ai"}:
                return any(k and len(k) >= 8 for k in (self.gemini_api_keys or []))
            if provider == "anthropic":
                return any(k and len(k) >= 8 for k in (self.anthropic_api_keys or []))
            if provider == "deepseek":
                return any(k and len(k) >= 8 for k in (self.deepseek_api_keys or []))
            if provider == "openai":
                return any(k and len(k) >= 8 for k in (self.openai_api_keys or []))
            return False

        if available_router_model_set:
            if self.litellm_model and not _uses_direct_env_provider(self.litellm_model) and self.litellm_model not in available_router_model_set:
                issues.append(ConfigIssue(
                    severity="error",
                    message=f"已配置的主模型未出现在当前渠道或高级模型路由配置中。当前可用模型：{', '.join(available_router_models[:6])}",
                    field="LITELLM_MODEL",
                ))
            invalid_fallbacks = [
                model for model in (self.litellm_fallback_models or [])
                if model and model not in available_router_model_set and not _uses_direct_env_provider(model)
            ]
            if invalid_fallbacks:
                issues.append(ConfigIssue(
                    severity="warning",
                    message=f"备选模型中包含未在当前渠道或高级模型路由配置中声明的模型：{', '.join(invalid_fallbacks[:3])}",
                    field="LITELLM_FALLBACK_MODELS",
                ))
            if self.vision_model and not _uses_direct_env_provider(self.vision_model) and self.vision_model not in available_router_model_set:
                issues.append(ConfigIssue(
                    severity="warning",
                    message=f"VISION_MODEL 未出现在当前渠道声明中。当前可用模型：{', '.join(available_router_models[:6])}",
                    field="VISION_MODEL",
                ))

        if not self.has_search_capability_enabled():
            issues.append(ConfigIssue(
                severity="info",
                message="未配置搜索引擎能力 (Bocha/MiniMax/Tavily/Brave/SerpAPI/SearXNG)，新闻搜索功能将不可用",
                field="BOCHA_API_KEYS",
            ))

        has_notification = bool(self.wechat_webhook_url)
        if not has_notification:
            issues.append(ConfigIssue(
                severity="warning",
                message="未配置企业微信 Webhook，将不发送推送通知",
                field="WECHAT_WEBHOOK_URL",
            ))

        has_feishu_app_id = bool((self.feishu_app_id or "").strip())
        has_feishu_app_secret = bool((self.feishu_app_secret or "").strip())
        has_feishu_app_credentials = has_feishu_app_id or has_feishu_app_secret
        has_feishu_doc_token = bool((self.feishu_folder_token or "").strip())
        has_feishu_full_cloud_doc_credentials = has_feishu_app_id and has_feishu_app_secret and has_feishu_doc_token
        if has_feishu_app_credentials and not has_feishu_full_cloud_doc_credentials and not (self.feishu_stream_enabled and has_feishu_app_id and has_feishu_app_secret):
            issues.append(ConfigIssue(
                severity="warning",
                message="仅配置 FEISHU_APP_ID / FEISHU_APP_SECRET 不会开启飞书群推送；若要使用应用机器人，请同时开启 FEISHU_STREAM_ENABLED 并完成应用发布与权限配置。",
                field="FEISHU_APP_ID",
            ))

        if os.getenv("OPENAI_VISION_MODEL"):
            issues.append(ConfigIssue(
                severity="info",
                message="OPENAI_VISION_MODEL 已废弃，请改用 VISION_MODEL。当前值已自动迁移，建议更新配置文件以消除此提示。",
                field="OPENAI_VISION_MODEL",
            ))

        if self.vision_model:
            _VISION_KEY_MAP = {
                "gemini": self.gemini_api_keys,
                "vertex_ai": self.gemini_api_keys,
                "anthropic": self.anthropic_api_keys,
                "openai": self.openai_api_keys,
                "deepseek": self.deepseek_api_keys,
            }
            _primary_prefix = self.vision_model.split("/")[0] if "/" in self.vision_model else "openai"
            _priority_providers = [p.strip().lower() for p in self.vision_provider_priority.split(",") if p.strip()]
            _all_providers = {_primary_prefix} | set(_priority_providers)
            _has_any_key = any(
                any(k and len(k) >= 8 for k in (_VISION_KEY_MAP.get(p) or []))
                for p in _all_providers if p in _VISION_KEY_MAP
            )
            if not _has_any_key:
                _checked = sorted(_all_providers & _VISION_KEY_MAP.keys())
                issues.append(ConfigIssue(
                    severity="warning",
                    message=f"VISION_MODEL 已配置，但未找到可用的 Vision API Key（已检查：{', '.join(_checked)}）。图片股票代码提取功能将不可用，请配置对应的 API Key。",
                    field="VISION_MODEL",
                ))

        return issues

    def validate(self) -> List[str]:
        return [issue.message for issue in self.validate_structured()]

    def get_db_url(self) -> str:
        db_path = Path(self.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path.absolute()}"


# === Convenience accessor ===
def get_config() -> Config:
    """获取全局配置实例的快捷方式"""
    return Config.get_instance()


# === Shared LLM helpers (used by analyzer and agent/llm_adapter) ===
def get_api_keys_for_model(model: str, config: Config) -> List[str]:
    """Return explicitly managed API keys for a litellm model (legacy path only)."""
    provider = _get_litellm_provider(model)
    if provider in {"gemini", "vertex_ai"}:
        return [k for k in config.gemini_api_keys if k and len(k) >= 8]
    if provider == "anthropic":
        return [k for k in config.anthropic_api_keys if k and len(k) >= 8]
    if provider == "deepseek":
        return [k for k in config.deepseek_api_keys if k and len(k) >= 8]
    if provider == "openai":
        return [k for k in config.openai_api_keys if k and len(k) >= 8]
    return []


def extra_litellm_params(model: str, config: Config) -> Dict[str, Any]:
    """Build extra litellm params for a model (legacy path only)."""
    params: Dict[str, Any] = {}
    if model.startswith("deepseek/"):
        return params
    if model.startswith("openai/") or "/" not in model:
        if config.openai_base_url:
            params["api_base"] = config.openai_base_url
        if config.openai_base_url and "aihubmix.com" in config.openai_base_url:
            params["extra_headers"] = {"APP-Code": "GPIJ3886"}
    return params