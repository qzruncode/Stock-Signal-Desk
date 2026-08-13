# -*- coding: utf-8 -*-
"""Non-persistent model connectivity test for the settings page."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Sequence
from urllib.parse import urlparse

from src.llm.anthropic_gateway import (
    AnthropicGatewayConfigError,
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
)

logger = logging.getLogger(__name__)


class ModelTestMixin:
    """Run a real, isolated request using saved or currently entered model values."""

    _MODEL_TEST_KEYS = (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
    )

    def test_model_connection(
        self,
        *,
        items: Sequence[Dict[str, str]],
        mask_token: str = "******",
        timeout_seconds: float = 30.0,
    ) -> Dict[str, Any]:
        """Send a tiny model request without writing the submitted values."""
        effective_map = self._build_model_test_effective_map(items=items, mask_token=mask_token)
        missing = [key for key in self._MODEL_TEST_KEYS if not (effective_map.get(key) or "").strip()]
        if missing:
            return self._build_model_test_result(
                success=False,
                message=f"模型配置不完整，缺少: {', '.join(missing)}",
                error_code="config_missing",
                stage="config_validation",
                retryable=False,
            )

        base_url = (effective_map.get("ANTHROPIC_BASE_URL") or "").strip()
        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            return self._build_model_test_result(
                success=False,
                message="模型接入地址无效，请填写带协议和主机的 HTTP(S) 地址。",
                error_code="config_invalid",
                stage="config_validation",
                retryable=False,
            )

        try:
            gateway_config = resolve_anthropic_gateway_config(effective_map)
            import litellm

            started_at = time.perf_counter()
            response = litellm.completion(
                **build_litellm_kwargs(
                    gateway_config,
                    stream=False,
                    messages=[
                        {
                            "role": "user",
                            "content": "请只回复 OK，不要输出其他内容。",
                        }
                    ],
                    max_tokens=16,
                    timeout=float(timeout_seconds),
                )
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            if not self._has_model_response_content(response):
                return self._build_model_test_result(
                    success=False,
                    message="模型请求已返回，但没有收到有效文本响应。",
                    error_code="empty_response",
                    stage="model_response",
                    retryable=False,
                    latency_ms=latency_ms,
                )

            return self._build_model_test_result(
                success=True,
                message=f"模型连接成功（{gateway_config['model']}），已收到模型响应。",
                error_code=None,
                stage="model_response",
                retryable=False,
                latency_ms=latency_ms,
            )
        except AnthropicGatewayConfigError as exc:
            return self._build_model_test_result(
                success=False,
                message=self._safe_model_error_message(exc, effective_map),
                error_code="config_missing",
                stage="config_validation",
                retryable=False,
            )
        except Exception as exc:  # pragma: no cover - provider-specific exception types vary
            error_code, retryable = self._classify_model_test_exception(exc)
            logger.warning("Model connection test failed (%s)", type(exc).__name__)
            return self._build_model_test_result(
                success=False,
                message=f"模型连接失败：{self._safe_model_error_message(exc, effective_map)}",
                error_code=error_code,
                stage="model_request",
                retryable=retryable,
            )

    def _build_model_test_effective_map(
        self,
        *,
        items: Sequence[Dict[str, str]],
        mask_token: str,
    ) -> Dict[str, str]:
        allowed_keys = set(self._MODEL_TEST_KEYS)
        effective = {
            key: value
            for key, value in self._build_display_config_map(self._manager.read_config_map()).items()
            if key in allowed_keys
        }

        for raw_key, raw_value in os.environ.items():
            key = str(raw_key).upper()
            if key in allowed_keys:
                effective[key] = "" if raw_value is None else str(raw_value)

        for item in items:
            key = str(item.get("key", "")).strip().upper()
            if key not in allowed_keys:
                continue
            value = "" if item.get("value") is None else str(item.get("value"))
            if value == mask_token:
                continue
            effective[key] = value

        return effective

    @staticmethod
    def _has_model_response_content(response: Any) -> bool:
        if isinstance(response, dict):
            choices = response.get("choices") or []
        else:
            choices = getattr(response, "choices", None) or []
        if not choices:
            return False

        first_choice = choices[0]
        if isinstance(first_choice, dict):
            message = first_choice.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else None
        else:
            message = getattr(first_choice, "message", None)
            content = getattr(message, "content", None) if message is not None else None

        if isinstance(content, list):
            return any(str(part).strip() for part in content)
        return bool(str(content or "").strip())

    @classmethod
    def _safe_model_error_message(cls, exc: Exception, effective_map: Dict[str, str]) -> str:
        message = str(exc).strip()
        token = (effective_map.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
        if token:
            message = message.replace(token, "[REDACTED]")

        lowered = message.lower()
        for marker in ("authorization", "api_key", "api-key", "bearer"):
            if marker in lowered:
                message = "网关拒绝了请求，请检查接入地址、鉴权令牌和模型名称。"
                break
        message = " ".join(message.split())
        return message[:300] or "未收到模型响应，请检查配置和网关状态。"

    @staticmethod
    def _classify_model_test_exception(exc: Exception) -> tuple[str, bool]:
        message = str(exc).lower()
        if isinstance(exc, TimeoutError) or "timeout" in message or "timed out" in message:
            return "timeout", True
        if isinstance(exc, OSError) or any(marker in message for marker in ("connection", "connect", "dns")):
            return "network_error", True
        return "provider_error", False

    @staticmethod
    def _build_model_test_result(
        *,
        success: bool,
        message: str,
        error_code: str | None,
        stage: str,
        retryable: bool,
        latency_ms: int | None = None,
    ) -> Dict[str, Any]:
        return {
            "success": success,
            "message": " ".join(str(message).split())[:300],
            "error_code": error_code,
            "stage": stage,
            "retryable": retryable,
            "latency_ms": latency_ms,
        }
