# -*- coding: utf-8 -*-
"""Cross-field and LLM channel validation for system config service."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Set, Tuple

from src.config import (
    ANSPIRE_LLM_BASE_URL_DEFAULT,
    ANSPIRE_LLM_MODEL_DEFAULT,
    SUPPORTED_LLM_CHANNEL_PROTOCOLS,
    Config,
    _get_litellm_provider,
    _uses_direct_env_provider,
    canonicalize_llm_channel_protocol,
    channel_allows_empty_api_key,
    get_configured_llm_models,
    normalize_llm_channel_model,
    parse_env_bool,
    resolve_llm_channel_protocol,
)

from ._types import _normalize_agent_model


class LLMChannelValidationMixin:
    """Validate cross-field, channel-map, runtime model and channel connection."""

    @classmethod
    def _validate_cross_field(cls, effective_map: Dict[str, str], updated_keys: Set[str]) -> List[Dict[str, Any]]:
        """Validate dependencies across multiple keys."""
        issues: List[Dict[str, Any]] = []

        token_value = (effective_map.get("TELEGRAM_BOT_TOKEN") or "").strip()
        chat_id_value = (effective_map.get("TELEGRAM_CHAT_ID") or "").strip()
        if token_value and not chat_id_value and (
            "TELEGRAM_BOT_TOKEN" in updated_keys or "TELEGRAM_CHAT_ID" in updated_keys
        ):
            issues.append(
                {
                    "key": "TELEGRAM_CHAT_ID",
                    "code": "missing_dependency",
                    "message": "TELEGRAM_CHAT_ID is required when TELEGRAM_BOT_TOKEN is set",
                    "severity": "error",
                    "expected": "non-empty TELEGRAM_CHAT_ID",
                    "actual": chat_id_value,
                }
            )

        feishu_relevant_keys = {
            "FEISHU_APP_ID",
            "FEISHU_APP_SECRET",
            "FEISHU_WEBHOOK_URL",
            "FEISHU_WEBHOOK_SECRET",
            "FEISHU_WEBHOOK_KEYWORD",
            "FEISHU_STREAM_ENABLED",
            "FEISHU_FOLDER_TOKEN",
        }
        has_feishu_app_id = bool((effective_map.get("FEISHU_APP_ID") or "").strip())
        has_feishu_app_secret = bool((effective_map.get("FEISHU_APP_SECRET") or "").strip())
        has_feishu_app_credentials = has_feishu_app_id or has_feishu_app_secret
        has_feishu_webhook = bool((effective_map.get("FEISHU_WEBHOOK_URL") or "").strip())
        has_feishu_folder_token = bool((effective_map.get("FEISHU_FOLDER_TOKEN") or "").strip())
        has_feishu_full_cloud_doc_credentials = (
            has_feishu_app_id
            and has_feishu_app_secret
            and has_feishu_folder_token
        )
        # Match runtime semantics: Config.from_env only enables stream mode
        # when the value is exactly "true" (case-insensitive).
        feishu_stream_enabled = (
            (effective_map.get("FEISHU_STREAM_ENABLED") or "false")
            .strip()
            .lower()
            == "true"
        )
        if (
            has_feishu_app_credentials
            and not has_feishu_full_cloud_doc_credentials
            and not has_feishu_webhook
            and not (feishu_stream_enabled and has_feishu_app_id and has_feishu_app_secret)
            and (updated_keys & feishu_relevant_keys)
        ):
            issues.append(
                {
                    "key": "FEISHU_WEBHOOK_URL",
                    "code": "feishu_mode_mismatch",
                    "message": (
                        "仅配置 FEISHU_APP_ID / FEISHU_APP_SECRET 不会开启飞书群 Webhook 推送；"
                        "如需通知推送请填写 FEISHU_WEBHOOK_URL，若要使用应用机器人请同时开启 "
                        "FEISHU_STREAM_ENABLED 并完成应用发布与权限配置。"
                    ),
                    "severity": "warning",
                    "expected": "FEISHU_WEBHOOK_URL or FEISHU_STREAM_ENABLED=true",
                    "actual": "app credentials only",
                }
            )

        issues.extend(
            cls._validate_llm_channel_map(
                effective_map=effective_map,
                updated_keys=updated_keys,
            )
        )
        issues.extend(cls._validate_llm_runtime_selection(effective_map=effective_map))

        if parse_env_bool(effective_map.get("NOTIFICATION_DAILY_DIGEST_ENABLED"), default=False):
            issues.append(
                {
                    "key": "NOTIFICATION_DAILY_DIGEST_ENABLED",
                    "code": "reserved_notification_daily_digest",
                    "message": (
                        "NOTIFICATION_DAILY_DIGEST_ENABLED is reserved; "
                        "the current P4 implementation does not send daily digests."
                    ),
                    "severity": "warning",
                    "expected": "reserved flag only",
                    "actual": effective_map.get("NOTIFICATION_DAILY_DIGEST_ENABLED", ""),
                }
            )

        return issues

    @classmethod
    def _validate_llm_channel_map(cls, effective_map: Dict[str, str], updated_keys: Set[str]) -> List[Dict[str, Any]]:
        """Validate channel-style LLM configuration stored in `.env`."""
        issues: List[Dict[str, Any]] = []
        if cls._uses_litellm_yaml(effective_map):
            return issues

        raw_channels = (effective_map.get("LLM_CHANNELS") or "").strip()
        if not raw_channels:
            return issues

        normalized_names: List[str] = []
        seen_names: Set[str] = set()
        for raw_name in raw_channels.split(","):
            name = raw_name.strip()
            if not name:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_]+", name):
                issues.append(
                    {
                        "key": "LLM_CHANNELS",
                        "code": "invalid_channel_name",
                        "message": f"LLM channel name '{name}' may only contain letters, numbers, and underscores",
                        "severity": "error",
                        "expected": "letters/numbers/underscores",
                        "actual": name,
                    }
                )
                continue

            normalized_upper = name.upper()
            if normalized_upper in seen_names:
                issues.append(
                    {
                        "key": "LLM_CHANNELS",
                        "code": "duplicate_channel_name",
                        "message": f"LLM channel '{name}' is declared more than once",
                        "severity": "error",
                        "expected": "unique channel names",
                        "actual": raw_channels,
                    }
                )
                continue

            seen_names.add(normalized_upper)
            normalized_names.append(name)

        for name in normalized_names:
            prefix = f"LLM_{name.upper()}"
            protocol_value = (effective_map.get(f"{prefix}_PROTOCOL") or "").strip()
            if name.lower() == "anspire" and not protocol_value:
                protocol_value = "openai"
            base_url_value = (effective_map.get(f"{prefix}_BASE_URL") or "").strip()
            if name.lower() == "anspire" and not base_url_value:
                base_url_value = (
                    effective_map.get("ANSPIRE_LLM_BASE_URL")
                    or ANSPIRE_LLM_BASE_URL_DEFAULT
                ).strip()
            api_key_value = (
                (effective_map.get(f"{prefix}_API_KEYS") or "").strip()
                or (effective_map.get(f"{prefix}_API_KEY") or "").strip()
            )
            if name.lower() == "anspire" and not api_key_value:
                api_key_value = (effective_map.get("ANSPIRE_API_KEYS") or "").strip()
            models_value = [
                model.strip()
                for model in (effective_map.get(f"{prefix}_MODELS") or "").split(",")
                if model.strip()
            ]
            if name.lower() == "anspire" and not models_value:
                models_value = [
                    (
                        effective_map.get("ANSPIRE_LLM_MODEL")
                        or ANSPIRE_LLM_MODEL_DEFAULT
                    ).strip()
                ]
            enabled_raw = effective_map.get(f"{prefix}_ENABLED")
            if name.lower() == "anspire" and not (enabled_raw or "").strip():
                enabled_raw = effective_map.get("ANSPIRE_LLM_ENABLED")
            enabled = parse_env_bool(enabled_raw, default=True)
            issues.extend(
                cls._validate_llm_channel_definition(
                    channel_name=name,
                    protocol_value=protocol_value,
                    base_url_value=base_url_value,
                    api_key_value=api_key_value,
                    model_values=models_value,
                    enabled=enabled,
                    field_prefix=prefix,
                    require_complete=enabled,
                )
            )

        return issues

    @staticmethod
    def _collect_llm_channel_models_from_map(effective_map: Dict[str, str]) -> List[str]:
        """Collect normalized model names from channel-style env values."""
        raw_channels = (effective_map.get("LLM_CHANNELS") or "").strip()
        if not raw_channels:
            return []

        models: List[str] = []
        seen: Set[str] = set()
        for raw_name in raw_channels.split(","):
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

            base_url_value = (effective_map.get(f"{prefix}_BASE_URL") or "").strip()
            if name.lower() == "anspire" and not base_url_value:
                base_url_value = (
                    effective_map.get("ANSPIRE_LLM_BASE_URL")
                    or ANSPIRE_LLM_BASE_URL_DEFAULT
                ).strip()
            protocol_value = (effective_map.get(f"{prefix}_PROTOCOL") or "").strip()
            if name.lower() == "anspire" and not protocol_value:
                protocol_value = "openai"
            raw_models = [
                model.strip()
                for model in (effective_map.get(f"{prefix}_MODELS") or "").split(",")
                if model.strip()
            ]
            if name.lower() == "anspire" and not raw_models:
                raw_models = [
                    (
                        effective_map.get("ANSPIRE_LLM_MODEL")
                        or ANSPIRE_LLM_MODEL_DEFAULT
                    ).strip()
                ]
            resolved_protocol = resolve_llm_channel_protocol(protocol_value, base_url=base_url_value, models=raw_models, channel_name=name)
            for model in raw_models:
                normalized_model = normalize_llm_channel_model(model, resolved_protocol, base_url_value)
                if not normalized_model or normalized_model in seen:
                    continue
                seen.add(normalized_model)
                models.append(normalized_model)

        return models

    @staticmethod
    def _uses_litellm_yaml(effective_map: Dict[str, str]) -> bool:
        """Return True when a valid LiteLLM YAML config takes precedence over channels."""
        config_path = (effective_map.get("LITELLM_CONFIG") or "").strip()
        if not config_path:
            return False
        return bool(Config._parse_litellm_yaml(config_path))

    @staticmethod
    def _collect_yaml_models_from_map(effective_map: Dict[str, str]) -> List[str]:
        """Collect declared router model names from LiteLLM YAML config."""
        config_path = (effective_map.get("LITELLM_CONFIG") or "").strip()
        if not config_path:
            return []
        return get_configured_llm_models(Config._parse_litellm_yaml(config_path))

    @classmethod
    def _has_legacy_key_for_provider(cls, provider: str, effective_map: Dict[str, str]) -> bool:
        """Return True when legacy env config can still back the provider."""
        normalized_provider = canonicalize_llm_channel_protocol(provider)
        if normalized_provider in {"gemini", "vertex_ai"}:
            return bool(
                (effective_map.get("GEMINI_API_KEYS") or "").strip()
                or (effective_map.get("GEMINI_API_KEY") or "").strip()
            )
        if normalized_provider == "anthropic":
            return bool(
                (effective_map.get("ANTHROPIC_API_KEYS") or "").strip()
                or (effective_map.get("ANTHROPIC_API_KEY") or "").strip()
            )
        if normalized_provider == "deepseek":
            return bool(
                (effective_map.get("DEEPSEEK_API_KEYS") or "").strip()
                or (effective_map.get("DEEPSEEK_API_KEY") or "").strip()
            )
        if normalized_provider == "openai":
            return bool(
                (effective_map.get("OPENAI_API_KEYS") or "").strip()
                or (effective_map.get("AIHUBMIX_KEY") or "").strip()
                or (effective_map.get("OPENAI_API_KEY") or "").strip()
                or (
                    cls._anspire_legacy_llm_enabled(effective_map)
                    and (effective_map.get("ANSPIRE_API_KEYS") or "").strip()
                )
            )
        return False

    @classmethod
    def _has_runtime_source_for_model(cls, model: str, effective_map: Dict[str, str]) -> bool:
        """Whether the selected model still has a backing runtime source."""
        if not model or _uses_direct_env_provider(model):
            return True
        provider = _get_litellm_provider(model)
        return cls._has_legacy_key_for_provider(provider, effective_map)

    @classmethod
    def _validate_llm_runtime_selection(cls, effective_map: Dict[str, str]) -> List[Dict[str, Any]]:
        """Validate selected primary/fallback/vision models against configured channels."""
        issues: List[Dict[str, Any]] = []

        available_models = (
            cls._collect_yaml_models_from_map(effective_map)
            or cls._collect_llm_channel_models_from_map(effective_map)
        )
        available_model_set = set(available_models)
        if not available_model_set:
            raw_channels = (effective_map.get("LLM_CHANNELS") or "").strip()
            if not raw_channels:
                return issues

            configured_agent_model_raw = (effective_map.get("AGENT_LITELLM_MODEL") or "").strip()
            configured_agent_model = _normalize_agent_model(
                configured_agent_model_raw,
                configured_models=available_model_set,
            )
            primary_model = (effective_map.get("LITELLM_MODEL") or "").strip()
            if primary_model and not cls._has_runtime_source_for_model(primary_model, effective_map):
                issues.append(
                    {
                        "key": "LITELLM_MODEL",
                        "code": "missing_runtime_source",
                        "message": (
                            "A primary model is selected, but no usable runtime source was found. "
                            "Enable at least one channel with available models, or provide the "
                            "matching provider API key so the model can be resolved."
                        ),
                        "severity": "error",
                        "expected": "enabled channel model or matching legacy API key",
                        "actual": primary_model,
                    }
                )

            if (
                configured_agent_model_raw
                and configured_agent_model
                and not cls._has_runtime_source_for_model(
                    configured_agent_model,
                    effective_map,
                )
            ):
                issues.append(
                    {
                        "key": "AGENT_LITELLM_MODEL",
                        "code": "missing_runtime_source",
                        "message": (
                            "An Agent primary model is selected, but no usable runtime source was found. "
                            "Enable at least one channel with available models, or provide the "
                            "matching provider API key so the model can be resolved."
                        ),
                        "severity": "error",
                        "expected": "enabled channel model or matching legacy API key",
                        "actual": configured_agent_model,
                    }
                )

            fallback_models = [
                model.strip()
                for model in (effective_map.get("LITELLM_FALLBACK_MODELS") or "").split(",")
                if model.strip()
            ]
            invalid_fallbacks = [
                model for model in fallback_models
                if not cls._has_runtime_source_for_model(model, effective_map)
            ]
            if invalid_fallbacks:
                issues.append(
                    {
                        "key": "LITELLM_FALLBACK_MODELS",
                        "code": "missing_runtime_source",
                        "message": (
                            "Some fallback models do not have an enabled channel "
                            "or matching API key available"
                        ),
                        "severity": "error",
                        "expected": "enabled channel models or matching legacy API keys",
                        "actual": ", ".join(invalid_fallbacks[:3]),
                    }
                )

            vision_model = (effective_map.get("VISION_MODEL") or "").strip()
            if vision_model and not cls._has_runtime_source_for_model(vision_model, effective_map):
                issues.append(
                    {
                        "key": "VISION_MODEL",
                        "code": "missing_runtime_source",
                        "message": (
                            "A Vision model is selected, but there is no enabled channel "
                            "or matching API key available for it"
                        ),
                        "severity": "warning",
                        "expected": "enabled channel model or matching legacy API key",
                        "actual": vision_model,
                    }
                )

            return issues

        primary_model = (effective_map.get("LITELLM_MODEL") or "").strip()
        if primary_model and primary_model not in available_model_set and not _uses_direct_env_provider(primary_model):
            issues.append(
                {
                    "key": "LITELLM_MODEL",
                    "code": "unknown_model",
                    "message": (
                        "The selected primary model is not declared by the current enabled channels "
                        "or advanced model routing config. "
                        f"Available models: {', '.join(available_models[:6])}"
                    ),
                    "severity": "error",
                    "expected": "one configured channel model",
                    "actual": primary_model,
                }
            )

        configured_agent_model_raw = (effective_map.get("AGENT_LITELLM_MODEL") or "").strip()
        configured_agent_model = _normalize_agent_model(
            configured_agent_model_raw,
            configured_models=available_model_set,
        )
        if (
            configured_agent_model_raw
            and configured_agent_model
            and configured_agent_model not in available_model_set
            and not _uses_direct_env_provider(configured_agent_model)
        ):
            issues.append(
                {
                    "key": "AGENT_LITELLM_MODEL",
                    "code": "unknown_model",
                    "message": (
                        "The selected Agent primary model is not declared by the current enabled channels "
                        "or advanced model routing config. "
                        f"Available models: {', '.join(available_models[:6])}"
                    ),
                    "severity": "error",
                    "expected": "one configured channel model",
                    "actual": configured_agent_model,
                }
            )

        fallback_models = [
            model.strip()
            for model in (effective_map.get("LITELLM_FALLBACK_MODELS") or "").split(",")
            if model.strip()
        ]
        invalid_fallbacks = [
            model for model in fallback_models
            if model not in available_model_set and not _uses_direct_env_provider(model)
        ]
        if invalid_fallbacks:
            issues.append(
                {
                    "key": "LITELLM_FALLBACK_MODELS",
                    "code": "unknown_model",
                    "message": (
                        "Fallback models include entries that are not declared by the current enabled channels "
                        "or advanced model routing config"
                    ),
                    "severity": "error",
                    "expected": ",".join(available_models[:6]),
                    "actual": ", ".join(invalid_fallbacks[:3]),
                }
            )

        vision_model = (effective_map.get("VISION_MODEL") or "").strip()
        if vision_model and vision_model not in available_model_set and not _uses_direct_env_provider(vision_model):
            issues.append(
                {
                    "key": "VISION_MODEL",
                    "code": "unknown_model",
                    "message": (
                        "The selected Vision model is not declared by the current enabled channels "
                        "or advanced model routing config"
                    ),
                    "severity": "warning",
                    "expected": ",".join(available_models[:6]),
                    "actual": vision_model,
                }
            )

        return issues

    @classmethod
    def _validate_llm_channel_definition(
        cls,
        *,
        channel_name: str,
        protocol_value: str,
        base_url_value: str,
        api_key_value: str,
        model_values: Sequence[str],
        enabled: bool,
        field_prefix: str,
        require_complete: bool,
    ) -> List[Dict[str, Any]]:
        """Validate one normalized LLM channel definition."""
        if not require_complete:
            return []

        issues, resolved_protocol = cls._validate_llm_channel_connection(
            channel_name=channel_name,
            protocol_value=protocol_value,
            base_url_value=base_url_value,
            api_key_value=api_key_value,
            model_values=model_values,
            field_prefix=field_prefix,
            require_base_url=False,
        )
        models_key = f"{field_prefix}_MODELS" if field_prefix != "test_channel" else "models"

        if not model_values:
            issues.append(
                {
                    "key": models_key,
                    "code": "missing_models",
                    "message": f"LLM channel '{channel_name}' requires at least one model",
                    "severity": "error",
                    "expected": "comma-separated model list",
                    "actual": "",
                }
            )
        elif not resolved_protocol:
            unresolved = [model for model in model_values if "/" not in model]
            if unresolved:
                issues.append(
                    {
                        "key": models_key,
                        "code": "missing_protocol",
                        "message": (
                            f"LLM channel '{channel_name}' uses bare model names. "
                            "Set PROTOCOL or add provider/model prefixes."
                        ),
                        "severity": "error",
                        "expected": "protocol or provider/model",
                        "actual": ", ".join(unresolved[:3]),
                    }
                )

        return issues

    @classmethod
    def _validate_llm_channel_connection(
        cls,
        *,
        channel_name: str,
        protocol_value: str,
        base_url_value: str,
        api_key_value: str,
        model_values: Sequence[str] = (),
        field_prefix: str,
        require_base_url: bool,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Validate connection-level fields shared by test and discovery flows."""
        issues: List[Dict[str, Any]] = []
        protocol_key = f"{field_prefix}_PROTOCOL" if field_prefix != "test_channel" else "protocol"
        base_url_key = f"{field_prefix}_BASE_URL" if field_prefix != "test_channel" else "base_url"
        api_key_key = f"{field_prefix}_API_KEY" if field_prefix != "test_channel" else "api_key"

        normalized_protocol = canonicalize_llm_channel_protocol(protocol_value)
        if normalized_protocol and normalized_protocol not in SUPPORTED_LLM_CHANNEL_PROTOCOLS:
            issues.append(
                {
                    "key": protocol_key,
                    "code": "invalid_protocol",
                    "message": (
                        f"Unsupported LLM channel protocol '{protocol_value}'. "
                        f"Supported: {', '.join(SUPPORTED_LLM_CHANNEL_PROTOCOLS)}"
                    ),
                    "severity": "error",
                    "expected": ",".join(SUPPORTED_LLM_CHANNEL_PROTOCOLS),
                    "actual": protocol_value,
                }
            )

        if require_base_url and not base_url_value.strip():
            issues.append(
                {
                    "key": base_url_key,
                    "code": "missing_base_url",
                    "message": f"LLM channel '{channel_name}' requires a base URL to discover models",
                    "severity": "error",
                    "expected": "http(s)://host/v1",
                    "actual": "",
                }
            )
        elif base_url_value and not cls._is_valid_url(
            base_url_value,
            allowed_schemes=("http", "https"),
        ):
            issues.append(
                {
                    "key": base_url_key,
                    "code": "invalid_url",
                    "message": "LLM channel base URL must be a valid absolute URL",
                    "severity": "error",
                    "expected": "http(s)://host",
                    "actual": base_url_value,
                }
            )
        elif base_url_value and not cls._is_safe_base_url(base_url_value):
            issues.append(
                {
                    "key": base_url_key,
                    "code": "ssrf_blocked",
                    "message": "LLM channel base URL points to a restricted address (cloud metadata services are not allowed)",
                    "severity": "error",
                    "expected": "publicly reachable or local LLM endpoint",
                    "actual": base_url_value,
                }
            )

        resolved_protocol = resolve_llm_channel_protocol(
            protocol_value,
            base_url=base_url_value,
            models=list(model_values) if model_values else None,
            channel_name=channel_name,
        )
        # Validate parsed key segments so that inputs like "," or " , " are
        # treated as empty (they produce zero usable keys after split+strip).
        _parsed_api_keys = [seg.strip() for seg in api_key_value.split(",") if seg.strip()]
        if not _parsed_api_keys and not channel_allows_empty_api_key(resolved_protocol, base_url_value):
            issues.append(
                {
                    "key": api_key_key,
                    "code": "missing_api_key",
                    "message": f"LLM channel '{channel_name}' requires an API key",
                    "severity": "error",
                    "expected": "non-empty API key",
                    "actual": api_key_value,
                }
            )
        return issues, resolved_protocol
