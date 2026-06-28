# -*- coding: utf-8 -*-
"""Public LLM channel operations: model discovery + connectivity test."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Sequence

import requests

from src.config import (
    normalize_llm_channel_model,
    resolve_llm_channel_protocol,
)
from src.llm.errors import call_litellm_with_param_recovery
from src.llm.generation_params import apply_litellm_generation_params

logger = logging.getLogger(__name__)


class LLMOperationsMixin:
    """Discover models and run minimal completion tests against one channel."""

    def discover_llm_channel_models(
        self,
        *,
        name: str,
        protocol: str,
        base_url: str,
        api_key: str,
        models: Sequence[str] = (),
        timeout_seconds: float = 20.0,
        ) -> Dict[str, Any]:
        """Discover available models from an OpenAI-compatible `/models` endpoint."""
        channel_name = name.strip() or "channel"
        existing_models = [str(m).strip() for m in models if str(m).strip()]
        validation_issues, resolved_protocol = self._validate_llm_channel_connection(
            channel_name=channel_name,
            protocol_value=protocol,
            base_url_value=base_url,
            api_key_value=api_key,
            model_values=existing_models,
            field_prefix="discover_channel",
            require_base_url=True,
        )
        if not resolved_protocol and existing_models:
            resolved_protocol = resolve_llm_channel_protocol(
                protocol,
                base_url=base_url,
                models=existing_models,
                channel_name=channel_name,
            )
        errors = [issue for issue in validation_issues if issue["severity"] == "error"]
        if errors:
            return self._build_llm_channel_result(
                success=False,
                message="LLM channel configuration is invalid",
                error=errors[0]["message"],
                stage="model_discovery",
                error_code="invalid_config",
                retryable=False,
                details={
                    "issue_key": errors[0]["key"],
                    "issue_code": errors[0]["code"],
                    "reason": errors[0]["code"],
                },
                resolved_protocol=resolved_protocol or None,
                models=[],
                latency_ms=None,
            )

        if resolved_protocol not in {"openai", "deepseek"}:
            return self._build_llm_channel_result(
                success=False,
                message="Model discovery is not supported for this protocol",
                error=(
                    f"LLM channel '{channel_name}' protocol '{resolved_protocol}' "
                    "does not support /models discovery yet"
                ),
                stage="model_discovery",
                error_code="unsupported_protocol",
                retryable=False,
                details={"protocol": resolved_protocol or None},
                resolved_protocol=resolved_protocol or None,
                models=[],
                latency_ms=None,
            )

        api_keys = [segment.strip() for segment in api_key.split(",") if segment.strip()]
        selected_api_key = api_keys[0] if api_keys else ""
        request_headers = {"Accept": "application/json"}
        if selected_api_key:
            request_headers["Authorization"] = f"Bearer {selected_api_key}"

        models_url = self._build_llm_models_url(base_url)

        try:
            started_at = time.perf_counter()
            response = requests.get(
                models_url,
                headers=request_headers,
                timeout=max(5.0, float(timeout_seconds)),
                allow_redirects=False,
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
        except requests.RequestException as exc:
            logger.warning("LLM channel model discovery failed for %s: %s", channel_name, exc)
            diagnostic = self._classify_llm_exception(exc)
            return self._build_llm_channel_result(
                success=False,
                message=diagnostic.message,
                error=str(exc),
                stage="model_discovery",
                error_code=diagnostic.error_code,
                retryable=diagnostic.retryable,
                details=self._merge_llm_diagnostic_details({"endpoint": models_url}, diagnostic),
                resolved_protocol=resolved_protocol or None,
                models=[],
                latency_ms=None,
            )

        if 300 <= response.status_code < 400:
            return self._build_llm_channel_result(
                success=False,
                message="Model discovery request was redirected",
                error="Redirect responses are not allowed for model discovery",
                stage="model_discovery",
                error_code="network_error",
                retryable=False,
                details={"endpoint": models_url, "http_status": response.status_code},
                resolved_protocol=resolved_protocol or None,
                models=[],
                latency_ms=latency_ms,
            )

        if not response.ok:
            error_text = self._extract_llm_discovery_error(response)
            diagnostic = self._classify_llm_http_error(
                status_code=response.status_code,
                error_text=error_text,
            )
            return self._build_llm_channel_result(
                success=False,
                message=diagnostic.message,
                error=error_text,
                stage="model_discovery",
                error_code=diagnostic.error_code,
                retryable=diagnostic.retryable,
                details=self._merge_llm_diagnostic_details(
                    {"endpoint": models_url, "http_status": response.status_code},
                    diagnostic,
                ),
                resolved_protocol=resolved_protocol or None,
                models=[],
                latency_ms=latency_ms,
            )

        try:
            payload = response.json()
        except ValueError:
            return self._build_llm_channel_result(
                success=False,
                message="Model discovery returned invalid JSON",
                error="The /models endpoint did not return valid JSON",
                stage="response_parse",
                error_code="format_error",
                retryable=False,
                details={"endpoint": models_url, "http_status": response.status_code, "reason": "non_json"},
                resolved_protocol=resolved_protocol or None,
                models=[],
                latency_ms=latency_ms,
            )

        models = self._extract_discovered_llm_models(payload)
        if not models:
            return self._build_llm_channel_result(
                success=False,
                message="Model discovery returned no models",
                error="The /models endpoint did not return any model IDs",
                stage="response_parse",
                error_code="empty_response",
                retryable=False,
                details={"endpoint": models_url, "http_status": response.status_code, "reason": "empty_models"},
                resolved_protocol=resolved_protocol or None,
                models=[],
                latency_ms=latency_ms,
            )

        return self._build_llm_channel_result(
            success=True,
            message="LLM channel model discovery succeeded",
            error=None,
            stage="model_discovery",
            error_code=None,
            retryable=False,
            details={"endpoint": models_url, "model_count": len(models)},
            resolved_protocol=resolved_protocol or None,
            models=models,
            latency_ms=latency_ms,
        )

    def test_llm_channel(
        self,
        *,
        name: str,
        protocol: str,
        base_url: str,
        api_key: str,
        models: Sequence[str],
        enabled: bool = True,
        timeout_seconds: float = 20.0,
        capability_checks: Sequence[str] = (),
    ) -> Dict[str, Any]:
        """Run a minimal completion call against one channel definition."""
        requested_capabilities = self._normalize_llm_capability_checks(capability_checks)
        raw_models = [str(model).strip() for model in models if str(model).strip()]
        channel_name = name.strip() or "channel"
        validation_issues = self._validate_llm_channel_definition(
            channel_name=channel_name,
            protocol_value=protocol,
            base_url_value=base_url,
            api_key_value=api_key,
            model_values=raw_models,
            enabled=enabled,
            field_prefix="test_channel",
            require_complete=True,
        )
        errors = [issue for issue in validation_issues if issue["severity"] == "error"]
        if errors:
            return self._build_llm_channel_result(
                success=False,
                message="LLM channel configuration is invalid",
                error=errors[0]["message"],
                stage="chat_completion",
                error_code="invalid_config",
                retryable=False,
                details={
                    "issue_key": errors[0]["key"],
                    "issue_code": errors[0]["code"],
                    "reason": errors[0]["code"],
                },
                resolved_protocol=None,
                resolved_model=None,
                latency_ms=None,
                capability_results=self._build_skipped_capability_results(
                    requested_capabilities,
                    "base_test_failed",
                    "Skipped because the base channel test did not pass",
                ),
            )

        resolved_protocol = resolve_llm_channel_protocol(protocol, base_url=base_url, models=raw_models, channel_name=name)
        resolved_models = [normalize_llm_channel_model(model, resolved_protocol, base_url) for model in raw_models]
        resolved_model = resolved_models[0]
        api_keys = [segment.strip() for segment in api_key.split(",") if segment.strip()]
        selected_api_key = api_keys[0] if api_keys else ""

        call_kwargs: Dict[str, Any] = {
            "model": resolved_model,
            "messages": [{"role": "user", "content": "Reply with OK"}],
            "max_tokens": 256,  # Increased to allow MiniMax-M2.7 thinking process + response
            "timeout": max(5.0, float(timeout_seconds)),
        }
        if selected_api_key:
            call_kwargs["api_key"] = selected_api_key
        if base_url.strip():
            call_kwargs["api_base"] = base_url.strip()
        call_kwargs = apply_litellm_generation_params(
            call_kwargs,
            resolved_model,
            self._get_runtime_llm_temperature(),
        )

        try:
            import litellm

            # Register custom model pricing for MiniMax models not in LiteLLM's built-in list
            # This must be done before litellm.completion() to prevent cost calculation errors
            _CUSTOM_MODEL_PRICING = {
                "MiniMax-M2.7": {
                    "supports_function_calling": True,
                    "supports_vision": False,
                    "supports_audio_input": False,
                    "supports_audio_output": False,
                    "context_window": 100000,
                    "max_tokens": 10000,
                    "input_cost_per_token": 0.0000003,
                    "output_cost_per_token": 0.0000012,
                },
                "MiniMax-M2.5": {
                    "supports_function_calling": True,
                    "supports_vision": False,
                    "supports_audio_input": False,
                    "supports_audio_output": False,
                    "context_window": 100000,
                    "max_tokens": 10000,
                    "input_cost_per_token": 0.0000003,
                    "output_cost_per_token": 0.0000012,
                },
            }
            for model_name, pricing in _CUSTOM_MODEL_PRICING.items():
                try:
                    litellm.register_model({model_name: pricing})
                except Exception:
                    logger.warning("[LLM] 注册自定义模型定价失败: %s", model_name, exc_info=True)

            started_at = time.perf_counter()
            response = call_litellm_with_param_recovery(
                lambda kwargs: litellm.completion(**kwargs),
                model=resolved_model,
                call_kwargs=call_kwargs,
                logger=logger,
                log_label="[LLM channel test]",
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            content, parse_error_code, parse_error, parse_reason = self._extract_llm_completion_content(response)
            if parse_error_code:
                message = (
                    "LLM channel returned an empty response"
                    if parse_error_code == "empty_response"
                    else "LLM channel returned an unexpected response format"
                )
                return self._build_llm_channel_result(
                    success=False,
                    message=message,
                    error=parse_error,
                    stage="response_parse",
                    error_code=parse_error_code,
                    retryable=False,
                    details={"response_error": parse_error, "reason": parse_reason},
                    resolved_protocol=resolved_protocol or None,
                    resolved_model=resolved_model,
                    latency_ms=latency_ms,
                    capability_results=self._build_skipped_capability_results(
                        requested_capabilities,
                        "base_test_failed",
                        "Skipped because the base channel test did not pass",
                    ),
                )

            capability_results = (
                self._run_llm_capability_checks(
                    litellm_module=litellm,
                    resolved_model=resolved_model,
                    selected_api_key=selected_api_key,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                    capability_checks=requested_capabilities,
                )
                if requested_capabilities
                else {}
            )
            return self._build_llm_channel_result(
                success=True,
                message="LLM channel test succeeded",
                error=None,
                stage="chat_completion",
                error_code=None,
                retryable=False,
                details={"response_preview": content[:80]},
                resolved_protocol=resolved_protocol or None,
                resolved_model=resolved_model,
                latency_ms=latency_ms,
                capability_results=capability_results,
            )
        except Exception as exc:
            logger.warning("LLM channel test failed for %s: %s", channel_name, exc)
            diagnostic = self._classify_llm_exception(exc)
            return self._build_llm_channel_result(
                success=False,
                message=diagnostic.message,
                error=str(exc),
                stage="chat_completion",
                error_code=diagnostic.error_code,
                retryable=diagnostic.retryable,
                details=self._merge_llm_diagnostic_details({"model": resolved_model}, diagnostic),
                resolved_protocol=resolved_protocol or None,
                resolved_model=resolved_model,
                latency_ms=None,
                capability_results=self._build_skipped_capability_results(
                    requested_capabilities,
                    "base_test_failed",
                    "Skipped because the base channel test did not pass",
                ),
            )
