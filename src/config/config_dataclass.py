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
from src.config.env_helpers import parse_env_int, parse_env_float
from src.config.llm_config import (
    resolve_unified_llm_temperature,
)
from src.config.news_config import (
    normalize_news_strategy_profile,
    resolve_news_window_days,
)
from src.config.proxy_config import resolve_proxy_and_configure_no_proxy
from src.config.setup import setup_env
from src.config.config_loader import load_config_from_env

FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT = 8.0


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

    # === AI 分析配置 ===
    # 模型/鉴权统一由 Anthropic 网关（ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL）决定，
    # 由 src.llm.anthropic_gateway 解析，不在 Config 上落地字段。
    # 以下仅保留正交的生成参数与请求节流配置。
    llm_temperature: float = 0.7
    llm_thinking_enabled: bool = False
    llm_reasoning_effort: str = "auto"

    # === 新闻与分析筛选配置 ===
    news_max_age_days: int = 3
    news_strategy_profile: str = "short"
    bias_threshold: float = 5.0

    # === 通知配置 ===
    wechat_webhook_url: str = ""
    report_language: str = "zh"
    wechat_max_bytes: int = 4000
    wechat_msg_type: str = "markdown"

    # === 数据库配置 ===
    database_url: Optional[str] = None
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
    debug: bool = False
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None

    # === RSS 配置 ===
    rsshub_base_url: str = "http://127.0.0.1:1200"

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

    # === 配置校验模式 ===
    config_validate_mode: str = "warn"

    # --- Post-init validation ---
    _WEBUI_RUNTIME_ENV_FILE_PRIORITY_KEYS = frozenset(
        {
            "ADMIN_AUTH_ENABLED",
            "STOCK_LIST",
        }
    )
    _BOOTSTRAP_RUNTIME_ENV_OVERRIDES_CAPTURED = False
    _BOOTSTRAP_RUNTIME_ENV_OVERRIDES = frozenset()
    _BOOTSTRAP_RUNTIME_ENV_PRESENT_KEYS = frozenset()

    _instance: Optional["Config"] = None

    @classmethod
    def get_instance(cls) -> "Config":
        """获取配置单例实例"""
        if cls._instance is None:
            cls._instance = cls._load_from_env()
        return cls._instance

    @classmethod
    def _load_from_env(cls) -> "Config":
        """从环境变量加载配置。"""
        return load_config_from_env(
            cls,
            setup_env=setup_env,
            resolve_proxy_and_configure_no_proxy=resolve_proxy_and_configure_no_proxy,
            parse_env_int=parse_env_int,
            parse_env_float=parse_env_float,
            resolve_unified_llm_temperature=resolve_unified_llm_temperature,
            fundamental_stage_timeout_seconds_default=FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT,
        )
    @classmethod
    def _get_env_file_value(cls, key: str) -> Optional[str]:
        env_path = cls._resolve_env_path()
        if not env_path.exists():
            return None
        try:
            env_values = dotenv_values(env_path)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to read %s while resolving %s: %s",
                env_path,
                key,
                exc,
            )
            return None
        value = env_values.get(key)
        if value is None:
            return None
        return str(value)

    @classmethod
    def _resolve_env_path(cls) -> Path:
        env_file = os.getenv("ENV_FILE")
        if env_file:
            return Path(env_file).expanduser().resolve()

        project_env = (Path(__file__).resolve().parent.parent.parent / ".env").resolve()
        if project_env.exists():
            return project_env

        return (Path(__file__).resolve().parent.parent / ".env").resolve()

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
                env_file = os.getenv("ENV_FILE") or str(cls._resolve_env_path())
                logging.getLogger(__name__).warning(
                    "REPORT_LANGUAGE environment value '%s' overrides %s ('%s')",
                    preexisting_env_value,
                    env_file,
                    file_value,
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
                "REPORT_LANGUAGE '%s' invalid, fallback to 'zh' (valid: zh/en)",
                value,
            )
        return normalized

    @classmethod
    def _parse_news_strategy_profile(cls, value: Optional[str]) -> str:
        normalized = normalize_news_strategy_profile(value)
        raw = (value or "short").strip().lower()
        if raw != normalized:
            logging.getLogger(__name__).warning(
                "NEWS_STRATEGY_PROFILE '%s' invalid, fallback to 'short' " "(valid: ultra_short/short/medium/long)",
                value,
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

    def refresh_stock_list(self) -> None:
        env_path = self._resolve_env_path()
        stock_list_str = ""
        if env_path.exists():
            env_values = dotenv_values(env_path)
            stock_list_str = (env_values.get("STOCK_LIST") or "").strip()
        if not stock_list_str:
            stock_list_str = os.getenv("STOCK_LIST", "")
        stock_list = [(c or "").strip().upper() for c in stock_list_str.split(",") if (c or "").strip()]
        if not stock_list:
            stock_list = ["000001"]
        self.stock_list = stock_list

    def validate_structured(self) -> List[ConfigIssue]:
        issues: List[ConfigIssue] = []

        if not self.stock_list:
            issues.append(ConfigIssue(severity="error", message="未配置自选股列表 (STOCK_LIST)", field="STOCK_LIST"))

        # AI 模型接入：统一由 Anthropic 网关（ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL）决定
        _gw_missing = [
            name
            for name, val in (
                ("ANTHROPIC_BASE_URL", os.getenv("ANTHROPIC_BASE_URL")),
                ("ANTHROPIC_AUTH_TOKEN", os.getenv("ANTHROPIC_AUTH_TOKEN")),
                ("ANTHROPIC_MODEL", os.getenv("ANTHROPIC_MODEL")),
            )
            if not (val or "").strip()
        ]
        if _gw_missing:
            issues.append(
                ConfigIssue(
                    severity="error",
                    message=f"Anthropic 网关未配置完整，AI 分析功能将不可用。请补全：{', '.join(_gw_missing)}",
                    field="ANTHROPIC_BASE_URL",
                )
            )

        has_notification = bool(self.wechat_webhook_url)
        if not has_notification:
            issues.append(
                ConfigIssue(
                    severity="warning",
                    message="未配置企业微信 Webhook，将不发送推送通知",
                    field="WECHAT_WEBHOOK_URL",
                )
            )

        return issues

    def validate(self) -> List[str]:
        return [issue.message for issue in self.validate_structured()]

    def get_db_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_path = Path(self.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path.absolute()}"


# === Convenience accessor ===
def get_config() -> Config:
    """获取全局配置实例的快捷方式"""
    return Config.get_instance()
