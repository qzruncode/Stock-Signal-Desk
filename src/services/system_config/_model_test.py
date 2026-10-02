# -*- coding: utf-8 -*-
"""Non-persistent model connectivity test for the settings page."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Sequence
from urllib.parse import urlparse

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage

from src.llm.anthropic_gateway import AnthropicGatewayConfigError, resolve_anthropic_gateway_config
logger = logging.getLogger(__name__)


class ModelTestMixin:
    """Run a real, isolated request using saved or currently entered model values."""

    _MODEL_TEST_KEYS = (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
    )

    async def test_model_connection(
        self,
        *,
        items: Sequence[Dict[str, str]],
        mask_token: str = "******",
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

        started_at = time.perf_counter()
        try:
            gateway_config = resolve_anthropic_gateway_config(effective_map)
            model = ChatAnthropic(
                model_name=gateway_config["model"],
                api_key=gateway_config["api_key"],
                base_url=gateway_config["api_base"],
                default_headers=gateway_config["extra_headers"],
                max_tokens_to_sample=8,
                temperature=0.1,
                # Keep the connectivity probe on the same no-deadline SDK
                # contract as interactive model responses.
                timeout=None,
                max_retries=0,
            )

            has_content = False
            for attempt in range(2):
                try:
                    response = await model.ainvoke([HumanMessage(content="Reply with OK.")])
                    has_content = self._has_model_response_content(response)
                    break
                except Exception as exc:
                    _, retryable = self._classify_model_test_exception(exc)
                    if attempt == 0 and retryable:
                        await asyncio.sleep(0.5)
                        continue
                    raise

            if not has_content:
                return self._build_model_test_result(
                    success=False,
                    message="模型服务已连接，但没有返回文本或思考内容。",
                    error_code="empty_response",
                    stage="model_response",
                    retryable=False,
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                )

            latency_ms = int((time.perf_counter() - started_at) * 1000)
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
            status_code = getattr(exc, "status_code", None)
            raw_message = str(exc).lower()
            if status_code == 502 and any(
                marker in raw_message
                for marker in ("connecttimeouterror", "connectionpool", "connect timeout")
            ):
                safe_message = (
                    "模型网关返回 HTTP 502：连接上游模型服务超时，请检查网关到模型服务的网络或实例状态。"
                )
            else:
                safe_message = self._safe_model_error_message(exc, effective_map)
            logger.warning(
                "Model connection test failed (%s, status=%s)",
                type(exc).__name__,
                status_code,
            )
            return self._build_model_test_result(
                success=False,
                message=f"模型连接失败：{safe_message}",
                error_code=error_code,
                stage="model_request",
                retryable=retryable,
                latency_ms=int((time.perf_counter() - started_at) * 1000),
            )

    def _has_model_response_content(self, response: Any) -> bool:
        if isinstance(response, dict):
            choices = response.get("choices") or []
            for choice in choices:
                if isinstance(choice, dict):
                    delta = choice.get("delta") or choice.get("message") or {}
                else:
                    delta = getattr(choice, "delta", None) or getattr(choice, "message", None)
                if delta is None:
                    continue
                for key in ("content", "reasoning_content", "reasoning"):
                    value = delta.get(key) if isinstance(delta, dict) else getattr(delta, key, None)
                    if self._has_non_empty_response_part(value):
                        return True
            return False
        else:
            content = getattr(response, "content", None)
            additional = getattr(response, "additional_kwargs", {}) or {}
            return self._has_non_empty_response_part(content) or any(
                self._has_non_empty_response_part(additional.get(key))
                for key in ("reasoning_content", "reasoning", "thinking")
            )

    def _has_non_empty_response_part(self, value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple)):
            return any(self._has_non_empty_response_part(item) for item in value)
        if isinstance(value, dict):
            return any(self._has_non_empty_response_part(item) for item in value.values())
        return False

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
        status_code = getattr(exc, "status_code", None)
        if status_code in {502, 503, 504}:
            return "upstream_unavailable", True
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
