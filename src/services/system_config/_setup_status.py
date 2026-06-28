# -*- coding: utf-8 -*-
"""First-run setup status helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from src.config import (
    ANSPIRE_LLM_BASE_URL_DEFAULT,
    ANSPIRE_LLM_MODEL_DEFAULT,
    _get_litellm_provider,
    _uses_direct_env_provider,
    canonicalize_llm_channel_protocol,
    channel_allows_empty_api_key,
    normalize_llm_channel_model,
    parse_env_bool,
    resolve_llm_channel_protocol,
)
from src.core.config_registry import get_registered_field_keys

from ._types import _normalize_agent_model


class SetupStatusMixin:
    """Compute read-only first-run setup status without mutating runtime state."""

    def get_setup_status(self) -> Dict[str, Any]:
        """Return read-only first-run setup status without mutating runtime state."""
        effective_map = self._build_setup_effective_config_map()
        llm_check = self._build_setup_primary_llm_check(effective_map)
        agent_check = self._build_setup_agent_llm_check(effective_map, llm_check)
        checks = [
            llm_check,
            agent_check,
            self._build_setup_stock_list_check(effective_map),
            self._build_setup_notification_check(effective_map),
            self._build_setup_storage_check(effective_map),
        ]

        required_missing = [
            check["key"]
            for check in checks
            if check["required"] and check["status"] == "needs_action"
        ]
        return {
            "is_complete": not required_missing,
            "ready_for_smoke": not required_missing,
            "required_missing_keys": required_missing,
            "next_step_key": required_missing[0] if required_missing else None,
            "checks": checks,
        }

    @staticmethod
    def _setup_check(
        key: str,
        title: str,
        category: str,
        required: bool,
        status: str,
        message: str,
        next_step: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "key": key,
            "title": title,
            "category": category,
            "required": required,
            "status": status,
            "message": message,
            "next_step": next_step,
        }

    @staticmethod
    def _is_setup_relevant_env_key(key: str) -> bool:
        if key in {
            "STOCK_LIST",
            "DATABASE_PATH",
            "LITELLM_CONFIG",
            "LITELLM_MODEL",
            "LITELLM_FALLBACK_MODELS",
            "AGENT_LITELLM_MODEL",
            "VISION_MODEL",
            "OPENAI_BASE_URL",
            "OLLAMA_API_BASE",
            "FEISHU_STREAM_ENABLED",
        }:
            return True
        prefixes = (
            "LLM_",
            "GEMINI_",
            "OPENAI_",
            "ANTHROPIC_",
            "DEEPSEEK_",
            "OLLAMA_",
            "FEISHU_",
            "TELEGRAM_",
            "EMAIL_",
            "DISCORD_",
            "SLACK_",
            "DINGTALK_",
            "WECHAT_",
            "PUSHOVER_",
            "NTFY_",
            "GOTIFY_",
            "PUSHPLUS_",
            "SERVERCHAN",
            "CUSTOM_WEBHOOK",
            "WECOM_",
            "ASTRBOT_",
        )
        return key.startswith(prefixes) or key.endswith("_API_KEY") or key.endswith("_API_KEYS")

    def _build_setup_effective_config_map(self) -> Dict[str, str]:
        """Combine saved `.env` values with injected runtime env values for status checks."""
        saved_map = self._build_display_config_map(self._manager.read_config_map())
        effective_map = dict(saved_map)
        registered_keys = {key.upper() for key in get_registered_field_keys()}

        for raw_key, raw_value in os.environ.items():
            key = str(raw_key).upper()
            value = "" if raw_value is None else str(raw_value)
            if key in registered_keys or self._is_setup_relevant_env_key(key):
                effective_map[key] = value

        return self._build_display_config_map(effective_map)

    @staticmethod
    def _has_any_config_value(effective_map: Dict[str, str], keys: Sequence[str]) -> bool:
        return any((effective_map.get(key) or "").strip() for key in keys)

    @classmethod
    def _anspire_legacy_llm_enabled(cls, effective_map: Dict[str, str]) -> bool:
        if not parse_env_bool(effective_map.get("ANSPIRE_LLM_ENABLED"), default=True):
            return False
        for name in cls._split_csv(effective_map.get("LLM_CHANNELS") or ""):
            if name.strip().lower() != "anspire":
                continue
            enabled_raw = effective_map.get("LLM_ANSPIRE_ENABLED")
            if not (enabled_raw or "").strip():
                enabled_raw = effective_map.get("ANSPIRE_LLM_ENABLED")
            return parse_env_bool(enabled_raw, default=True)
        return True

    @classmethod
    def _provider_has_setup_credentials(cls, provider: str, effective_map: Dict[str, str]) -> bool:
        normalized = canonicalize_llm_channel_protocol(provider)
        if normalized == "ollama":
            return True
        if normalized == "gemini" or normalized == "vertex_ai":
            return cls._has_any_config_value(effective_map, ("GEMINI_API_KEYS", "GEMINI_API_KEY"))
        if normalized == "anthropic":
            return cls._has_any_config_value(effective_map, ("ANTHROPIC_API_KEYS", "ANTHROPIC_API_KEY"))
        if normalized == "deepseek":
            return cls._has_any_config_value(effective_map, ("DEEPSEEK_API_KEYS", "DEEPSEEK_API_KEY"))
        if normalized == "openai":
            if cls._has_any_config_value(effective_map, ("OPENAI_API_KEYS", "OPENAI_API_KEY", "AIHUBMIX_KEY")):
                return True
            if (
                cls._anspire_legacy_llm_enabled(effective_map)
                and cls._has_any_config_value(effective_map, ("ANSPIRE_API_KEYS",))
            ):
                return True
            base_url = (effective_map.get("OPENAI_BASE_URL") or "").strip()
            return channel_allows_empty_api_key("openai", base_url)

        env_prefix = normalized.upper().replace("-", "_")
        return cls._has_any_config_value(
            effective_map,
            (f"{env_prefix}_API_KEYS", f"{env_prefix}_API_KEY"),
        )

    @classmethod
    def _has_setup_runtime_source_for_model(cls, model: str, effective_map: Dict[str, str]) -> bool:
        normalized_model = (model or "").strip()
        if not normalized_model:
            return False
        provider = _get_litellm_provider(normalized_model)
        return cls._provider_has_setup_credentials(provider, effective_map)

    @classmethod
    def _collect_setup_channel_models(cls, effective_map: Dict[str, str]) -> List[str]:
        models: List[str] = []
        seen: Set[str] = set()
        for raw_name in cls._split_csv(effective_map.get("LLM_CHANNELS") or ""):
            name = raw_name.strip()
            if not name:
                continue
            prefix = f"LLM_{name.upper()}"
            enabled_raw = effective_map.get(f"{prefix}_ENABLED")
            if name.lower() == "anspire" and not (enabled_raw or "").strip():
                enabled_raw = effective_map.get("ANSPIRE_LLM_ENABLED")
            enabled = parse_env_bool(enabled_raw, default=True)
            if not enabled:
                continue

            base_url = (effective_map.get(f"{prefix}_BASE_URL") or "").strip()
            if name.lower() == "anspire" and not base_url:
                base_url = (
                    effective_map.get("ANSPIRE_LLM_BASE_URL")
                    or ANSPIRE_LLM_BASE_URL_DEFAULT
                ).strip()
            protocol = (effective_map.get(f"{prefix}_PROTOCOL") or "").strip()
            if name.lower() == "anspire" and not protocol:
                protocol = "openai"
            api_key = (
                (effective_map.get(f"{prefix}_API_KEYS") or "").strip()
                or (effective_map.get(f"{prefix}_API_KEY") or "").strip()
            )
            if name.lower() == "anspire" and not api_key:
                api_key = (effective_map.get("ANSPIRE_API_KEYS") or "").strip()
            raw_models = cls._split_csv(effective_map.get(f"{prefix}_MODELS") or "")
            if name.lower() == "anspire" and not raw_models:
                raw_models = [
                    (
                        effective_map.get("ANSPIRE_LLM_MODEL")
                        or ANSPIRE_LLM_MODEL_DEFAULT
                    ).strip()
                ]
            resolved_protocol = resolve_llm_channel_protocol(
                protocol,
                base_url=base_url,
                models=raw_models,
                channel_name=name,
            )
            if not raw_models or not resolved_protocol:
                continue
            if not api_key and not channel_allows_empty_api_key(resolved_protocol, base_url):
                continue

            for raw_model in raw_models:
                normalized_model = normalize_llm_channel_model(raw_model, resolved_protocol, base_url)
                if normalized_model and normalized_model not in seen:
                    seen.add(normalized_model)
                    models.append(normalized_model)
        return models

    @classmethod
    def _infer_setup_legacy_primary_model(cls, effective_map: Dict[str, str]) -> str:
        if cls._has_any_config_value(effective_map, ("GEMINI_API_KEYS", "GEMINI_API_KEY")):
            model = (effective_map.get("GEMINI_MODEL") or "gemini-3.1-pro-preview").strip()
            return model if "/" in model else f"gemini/{model}"
        if cls._has_any_config_value(effective_map, ("ANTHROPIC_API_KEYS", "ANTHROPIC_API_KEY")):
            model = (effective_map.get("ANTHROPIC_MODEL") or "claude-sonnet-4-6").strip()
            return model if "/" in model else f"anthropic/{model}"
        if cls._has_any_config_value(effective_map, ("DEEPSEEK_API_KEYS", "DEEPSEEK_API_KEY")):
            return "deepseek/deepseek-chat"
        if cls._has_any_config_value(effective_map, ("OPENAI_API_KEYS", "OPENAI_API_KEY", "AIHUBMIX_KEY")):
            model = (effective_map.get("OPENAI_MODEL") or "gpt-5.5").strip()
            return model if "/" in model else f"openai/{model}"
        if (
            cls._anspire_legacy_llm_enabled(effective_map)
            and cls._has_any_config_value(effective_map, ("ANSPIRE_API_KEYS",))
        ):
            model = (
                effective_map.get("ANSPIRE_LLM_MODEL")
                or effective_map.get("OPENAI_MODEL")
                or ANSPIRE_LLM_MODEL_DEFAULT
            ).strip()
            return model if "/" in model else f"openai/{model}"
        if (effective_map.get("OLLAMA_API_BASE") or "").strip():
            model = (effective_map.get("OLLAMA_MODEL") or "").strip()
            return model if model.startswith("ollama/") else (f"ollama/{model}" if model else "ollama/local")
        return ""

    def _resolve_setup_primary_model(self, effective_map: Dict[str, str]) -> Tuple[str, str]:
        explicit_model = (effective_map.get("LITELLM_MODEL") or "").strip()
        yaml_models = self._collect_yaml_models_from_map(effective_map)
        channel_models = self._collect_setup_channel_models(effective_map)

        if explicit_model:
            if _uses_direct_env_provider(explicit_model):
                return explicit_model, "explicit"
            has_direct_source = self._has_setup_runtime_source_for_model(explicit_model, effective_map)
            if yaml_models and explicit_model not in set(yaml_models):
                return "", "主模型未出现在当前 LiteLLM YAML model_list 中"
            if channel_models and explicit_model not in set(channel_models):
                return "", "主模型未出现在当前启用渠道模型列表中"
            if yaml_models or channel_models or has_direct_source:
                return explicit_model, "explicit"
            return "", "主模型缺少可用渠道或匹配的 API Key"

        if yaml_models:
            return yaml_models[0], "yaml"
        if channel_models:
            return channel_models[0], "channel"

        legacy_model = self._infer_setup_legacy_primary_model(effective_map)
        if legacy_model:
            return legacy_model, "legacy"

        return "", "尚未检测到主模型配置"

    def _build_setup_primary_llm_check(self, effective_map: Dict[str, str]) -> Dict[str, Any]:
        model, source = self._resolve_setup_primary_model(effective_map)
        if model:
            source_label = {
                "explicit": "显式主模型",
                "yaml": "LiteLLM YAML",
                "channel": "LLM 渠道",
                "legacy": "legacy provider",
            }.get(source, source)
            return self._setup_check(
                "llm_primary",
                "LLM 主渠道",
                "ai_model",
                True,
                "configured",
                f"已检测到 {source_label}: {model}",
            )
        return self._setup_check(
            "llm_primary",
            "LLM 主渠道",
            "ai_model",
            True,
            "needs_action",
            source,
            "请配置 LITELLM_MODEL、LLM_CHANNELS、LITELLM_CONFIG 或 legacy provider API Key。",
        )

    def _build_setup_agent_llm_check(
        self,
        effective_map: Dict[str, str],
        primary_check: Dict[str, Any],
    ) -> Dict[str, Any]:
        agent_model_raw = (effective_map.get("AGENT_LITELLM_MODEL") or "").strip()
        if not agent_model_raw:
            if primary_check["status"] == "configured":
                return self._setup_check(
                    "llm_agent",
                    "Agent 渠道",
                    "agent",
                    True,
                    "inherited",
                    "未单独配置 Agent 主模型，将继承 LLM 主渠道。",
                )
            return self._setup_check(
                "llm_agent",
                "Agent 渠道",
                "agent",
                True,
                "needs_action",
                "Agent 未配置独立模型，且 LLM 主渠道尚不可用。",
                "请先补齐 LLM 主渠道配置。",
            )

        configured_models = set(
            self._collect_yaml_models_from_map(effective_map)
            or self._collect_setup_channel_models(effective_map)
        )
        agent_model = _normalize_agent_model(agent_model_raw, configured_models=configured_models)
        if _uses_direct_env_provider(agent_model):
            return self._setup_check(
                "llm_agent",
                "Agent 渠道",
                "agent",
                True,
                "configured",
                f"已配置 Agent 主模型: {agent_model}",
            )
        if (
            not configured_models
            and self._has_setup_runtime_source_for_model(agent_model, effective_map)
        ) or agent_model in configured_models:
            return self._setup_check(
                "llm_agent",
                "Agent 渠道",
                "agent",
                True,
                "configured",
                f"已配置 Agent 主模型: {agent_model}",
            )

        return self._setup_check(
            "llm_agent",
            "Agent 渠道",
            "agent",
            True,
            "needs_action",
            f"Agent 主模型 {agent_model} 缺少可用渠道或匹配的 API Key。",
            "请调整 AGENT_LITELLM_MODEL 或补齐对应渠道配置。",
        )

    def _build_setup_stock_list_check(self, effective_map: Dict[str, str]) -> Dict[str, Any]:
        stocks = self._split_csv(effective_map.get("STOCK_LIST") or "")
        if stocks:
            return self._setup_check(
                "stock_list",
                "自选股",
                "base",
                True,
                "configured",
                f"已配置 {len(stocks)} 只股票。",
            )
        return self._setup_check(
            "stock_list",
            "自选股",
            "base",
            True,
            "needs_action",
            "当前 STOCK_LIST 为空。",
            "请至少添加 1 只股票用于首次试跑。",
        )

    def _build_setup_notification_check(self, effective_map: Dict[str, str]) -> Dict[str, Any]:
        configured = self._has_any_config_value(effective_map, ("WECHAT_WEBHOOK_URL",))
        if configured:
            return self._setup_check(
                "notification",
                "通知渠道",
                "notification",
                False,
                "configured",
                "已检测到企业微信通知配置。",
            )
        return self._setup_check(
            "notification",
            "通知渠道",
            "notification",
            False,
            "optional",
            "通知为可选项，未配置也不影响首次跑通。",
            "需要推送时可稍后配置企业微信通知渠道。",
        )

    def _build_setup_storage_check(self, effective_map: Dict[str, str]) -> Dict[str, Any]:
        db_path = Path((effective_map.get("DATABASE_PATH") or "./data/stock_analysis.db").strip()).expanduser()
        parent = db_path.parent if db_path.parent != Path("") else Path(".")
        probe = parent
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent

        if not probe.exists() or not probe.is_dir():
            return self._setup_check(
                "storage",
                "数据库 / 本地存储",
                "system",
                True,
                "needs_action",
                f"数据库路径父目录不可用: {parent}",
                "请检查 DATABASE_PATH 或上级目录权限。",
            )

        if os.access(probe, os.W_OK):
            detail = f"数据库路径可用: {db_path}"
            if not parent.exists():
                detail = f"数据库上级目录可创建: {parent}"
            return self._setup_check(
                "storage",
                "数据库 / 本地存储",
                "system",
                True,
                "configured",
                detail,
            )

        return self._setup_check(
            "storage",
            "数据库 / 本地存储",
            "system",
            True,
            "needs_action",
            f"数据库路径上级目录不可写: {probe}",
            "请调整 DATABASE_PATH 或目录权限。",
        )
