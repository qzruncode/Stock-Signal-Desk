# -*- coding: utf-8 -*-
"""Diagnostic helpers for LLM channel test / discovery flows.

Covers URL helpers, error classification, response extraction, and sanitization.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urlunparse

import requests

from src.config import Config

from ._types import _LLMDiagnostic


class LLMDiagnosticsMixin:
    """Reusable diagnostics for LLM error classification and sanitization."""

    @staticmethod
    def _is_safe_base_url(value: str) -> bool:
        """Block link-local and cloud metadata addresses to prevent SSRF.

        Allows localhost / private-LAN addresses (e.g. Ollama on 192.168.x.x)
        but blocks 169.254.x.x (AWS/Azure/GCP/Alibaba instance-metadata service)
        and other known metadata hostnames.
        """
        import ipaddress

        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if not host:
            return True
        # Known cloud metadata hostnames
        _BLOCKED_HOSTS = frozenset({
            "169.254.169.254",
            "metadata.google.internal",
            "100.100.100.200",
        })
        if host in _BLOCKED_HOSTS:
            return False
        # Numeric IPs: block link-local range (169.254.0.0/16)
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_link_local:
                return False
        except ValueError:
            pass  # hostname, not an IP — already checked against blocklist above
        return True

    @staticmethod
    def _build_llm_models_url(base_url: str) -> str:
        """Convert a channel base URL into a `/models` endpoint."""
        parsed = urlparse(base_url.strip())
        normalized = (parsed.path or "").rstrip("/")
        for suffix in ("/chat/completions", "/completions"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
                break
        if normalized.endswith("/models"):
            models_path = normalized or "/models"
        else:
            models_path = f"{normalized}/models" if normalized else "/models"
        return urlunparse(parsed._replace(path=models_path, params="", query="", fragment=""))

    @staticmethod
    def _get_runtime_llm_temperature() -> float:
        """Return the current configured LLM temperature for ad-hoc channel tests."""
        config = Config._load_from_env()
        try:
            return float(getattr(config, "llm_temperature", 0.7))
        except (TypeError, ValueError):
            return 0.7

    @classmethod
    def _build_llm_channel_result(
        cls,
        *,
        success: bool,
        message: str,
        error: Optional[str],
        stage: Optional[str],
        error_code: Optional[str],
        retryable: Optional[bool],
        details: Optional[Dict[str, Any]] = None,
        resolved_protocol: Optional[str] = None,
        resolved_model: Optional[str] = None,
        models: Optional[List[str]] = None,
        latency_ms: Optional[int] = None,
        capability_results: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "success": success,
            "message": cls._sanitize_llm_error_text(message),
            "error": cls._sanitize_llm_error_text(error) if error else None,
            "stage": stage,
            "error_code": error_code,
            "retryable": retryable,
            "details": cls._sanitize_llm_details(details),
            "resolved_protocol": resolved_protocol,
            "latency_ms": latency_ms,
        }
        if resolved_model is not None or models is None:
            payload["resolved_model"] = resolved_model
        if models is not None:
            payload["models"] = models
        if capability_results is not None:
            payload["capability_results"] = cls._sanitize_llm_details(capability_results)
        return payload

    @staticmethod
    def _merge_llm_diagnostic_details(
        base_details: Optional[Dict[str, Any]],
        diagnostic: _LLMDiagnostic,
    ) -> Dict[str, Any]:
        details: Dict[str, Any] = dict(base_details or {})
        if diagnostic.reason:
            details.setdefault("reason", diagnostic.reason)
        details.update(diagnostic.details)
        return details

    @staticmethod
    def _sanitize_llm_error_text(text: Any) -> str:
        if text is None:
            return ""
        sanitized = str(text).strip()
        if not sanitized:
            return ""

        patterns = [
            (r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?([^\s,;]+)", r"\1[REDACTED]"),
            (r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)", r"\1[REDACTED]"),
            (r"(?i)(cookie\s*[:=]\s*)([^\s,;]+)", r"\1[REDACTED]"),
            (r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer [REDACTED]"),
            (r"(?i)sk-[a-z0-9_\-]+", "[REDACTED]"),
        ]
        for pattern, replacement in patterns:
            sanitized = re.sub(pattern, replacement, sanitized)
        sanitized = " ".join(sanitized.split())
        return sanitized[:300]

    @classmethod
    def _sanitize_llm_details(cls, details: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not details:
            return {}
        sanitized: Dict[str, Any] = {}
        for key, value in details.items():
            if isinstance(value, str):
                sanitized[key] = cls._sanitize_llm_error_text(value)
            elif isinstance(value, dict):
                sanitized[key] = cls._sanitize_llm_details(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    cls._sanitize_llm_error_text(item) if isinstance(item, str) else item
                    for item in value
                ]
            else:
                sanitized[key] = value
        return sanitized

    @classmethod
    def _classify_llm_http_error(cls, status_code: int, error_text: str) -> _LLMDiagnostic:
        lowered = (error_text or "").lower()
        if cls._has_model_access_denied_signal(error_text or ""):
            return _LLMDiagnostic(
                "model_not_found",
                False,
                "Configured model is not available for this channel",
                "model_access_denied",
            )
        if "model" in lowered and any(token in lowered for token in ("not found", "does not exist", "unknown")):
            return _LLMDiagnostic(
                "model_not_found",
                False,
                "Configured model could not be found on this channel",
                "model_not_found",
            )
        if status_code == 402 or any(token in lowered for token in ("billing", "balance", "insufficient balance")):
            return _LLMDiagnostic(
                "quota",
                True,
                "LLM request was rejected by quota or billing limits",
                "insufficient_balance",
            )
        if any(token in lowered for token in ("quota", "insufficient_quota", "quota exceeded")):
            return _LLMDiagnostic(
                "quota",
                True,
                "LLM request was rejected by quota or rate limiting",
                "quota_exceeded",
            )
        if status_code == 429 or any(token in lowered for token in ("rate limit", "too many requests", "rpm", "tpm")):
            return _LLMDiagnostic(
                "quota",
                True,
                "LLM request was rejected by quota or rate limiting",
                "rate_limit",
            )
        if cls._has_transport_blocked_signal(error_text or ""):
            return _LLMDiagnostic(
                "network_error",
                True,
                "LLM request failed before a valid response was returned",
                "network_error",
            )
        if cls._has_request_blocked_signal(error_text or ""):
            return _LLMDiagnostic(
                "request_blocked",
                False,
                "LLM request was blocked by provider or gateway policy",
                "provider_blocked",
            )
        if status_code in {401, 403} or any(token in lowered for token in ("unauthorized", "forbidden", "invalid api key", "authentication")):
            return _LLMDiagnostic("auth", False, "LLM authentication failed", "api_key_rejected")
        if status_code == 404:
            return _LLMDiagnostic(
                "network_error",
                False,
                "LLM model discovery endpoint could not be found",
                "endpoint_not_found",
            )
        if any(token in lowered for token in ("timeout", "timed out")):
            return _LLMDiagnostic("timeout", True, "LLM request timed out", "timeout")
        return _LLMDiagnostic(
            "network_error",
            status_code >= 500,
            "LLM request failed before a valid response was returned",
            "http_error",
        )

    @staticmethod
    def _has_model_not_found_signal(text: str) -> bool:
        lowered = text.lower()

        model_candidates = [
            re.search(r"model\s+not\s+found\s*[:：]?\s*[`\"']?\s*([a-z0-9._/-]{2,})", lowered),
            re.search(r"model\s*[`\"']?\s*([a-z0-9._/-]{2,})\s*[`\"']?\s+does\s+not\s+exist", lowered),
            re.search(r"model\s+does\s+not\s+exist\s*[:：]?\s*[`\"']?\s*([a-z0-9._/-]{2,})", lowered),
            re.search(r"unknown\s+model\s*[:：]?\s*[`\"']?\s*([a-z0-9._/-]{2,})", lowered),
            re.search(r"no\s+such\s+model\s*[:：]?\s*[`\"']?\s*([a-z0-9._/-]{2,})", lowered),
        ]

        for match in model_candidates:
            if not match:
                continue
            model_id = match.group(1).strip()
            if model_id and not model_id.startswith("/") and "http" not in model_id:
                return True

        return False

    @staticmethod
    def _has_model_access_denied_signal(text: str) -> bool:
        lowered = text.lower()
        if "model" not in lowered:
            return False

        # Best-effort classifier for observed provider messages. Keep it gated by
        # an explicit "model" mention plus access/disabled/unavailable signals so
        # unrelated provider-specific failures continue to use the fallback path.
        access_denied_tokens = (
            "not authorized",
            "not allowed",
            "access denied",
            "permission denied",
            "model disabled",
            "model is disabled",
            "disabled model",
            "model has been disabled",
            "model not enabled",
            "model not available",
            "model is not available",
        )
        return any(token in lowered for token in access_denied_tokens)

    @classmethod
    def _has_request_blocked_signal(cls, text: str) -> bool:
        lowered = text.lower()
        if cls._has_transport_blocked_signal(lowered):
            return False
        blocked_tokens = (
            "your request was blocked",
            "the request was blocked",
            "request blocked by policy",
            "blocked by policy",
            "blocked due to policy",
            "moderation_blocked",
            "policy_blocked",
            "请求被拦截",
        )
        return any(token in lowered for token in blocked_tokens)

    @staticmethod
    def _has_transport_blocked_signal(text: str) -> bool:
        lowered = text.lower()
        transport_tokens = (
            "connection blocked",
            "connection request was blocked",
            "network blocked",
            "blocked by network policy",
            "blocked by firewall",
            "firewall blocked",
        )
        return any(token in lowered for token in transport_tokens)

    @staticmethod
    def _has_provider_prefix_mismatch_signal(text: str) -> bool:
        lowered = text.lower()
        mismatch_tokens = (
            "provider prefix",
            "llm provider not provided",
            "invalid provider",
            "unknown provider",
            "custom_llm_provider",
            "not a valid llm provider",
        )
        return any(token in lowered for token in mismatch_tokens)

    @classmethod
    def _classify_llm_exception(cls, exc: Exception) -> _LLMDiagnostic:
        exc_name = type(exc).__name__.lower()
        text = str(exc).lower()
        if isinstance(exc, TimeoutError) or "timeout" in exc_name or "timed out" in text:
            return _LLMDiagnostic("timeout", True, "LLM request timed out", "timeout")
        if any(token in text for token in ("billing", "balance", "insufficient balance")):
            return _LLMDiagnostic(
                "quota",
                True,
                "LLM request was rejected by quota or billing limits",
                "insufficient_balance",
            )
        if any(token in text for token in ("quota", "insufficient_quota", "quota exceeded")):
            return _LLMDiagnostic(
                "quota",
                True,
                "LLM request was rejected by quota or rate limiting",
                "quota_exceeded",
            )
        if "ratelimit" in exc_name or any(token in text for token in ("rate limit", "too many requests", "rpm", "tpm")):
            return _LLMDiagnostic(
                "quota",
                True,
                "LLM request was rejected by quota or rate limiting",
                "rate_limit",
            )
        if cls._has_provider_prefix_mismatch_signal(text):
            return _LLMDiagnostic(
                "model_not_found",
                False,
                "Configured model prefix does not match this channel",
                "provider_prefix_mismatch",
            )
        if cls._has_model_access_denied_signal(str(exc)):
            return _LLMDiagnostic(
                "model_not_found",
                False,
                "Configured model is not available for this channel",
                "model_access_denied",
            )
        if cls._has_request_blocked_signal(str(exc)):
            return _LLMDiagnostic(
                "request_blocked",
                False,
                "LLM request was blocked by provider or gateway policy",
                "provider_blocked",
            )
        if any(token in exc_name for token in ("auth", "permission")) or any(token in text for token in ("unauthorized", "forbidden", "invalid api key", "authentication")):
            return _LLMDiagnostic("auth", False, "LLM authentication failed", "api_key_rejected")
        if ("notfound" in exc_name or "model" in text) and (
            "not found" in text or "does not exist" in text or "unknown model" in text
        ) and cls._has_model_not_found_signal(text):
            return _LLMDiagnostic(
                "model_not_found",
                False,
                "Configured model could not be found on this channel",
                "model_not_found",
            )
        if "dns" in text or "name resolution" in text or "temporary failure in name resolution" in text:
            return _LLMDiagnostic("network_error", True, "LLM request failed before a valid response was returned", "dns_error")
        if "refused" in text or "connection refused" in text:
            return _LLMDiagnostic("network_error", True, "LLM request failed before a valid response was returned", "connection_refused")
        if "ssl" in text or "tls" in text or "certificate" in text:
            return _LLMDiagnostic("network_error", True, "LLM request failed before a valid response was returned", "tls_error")
        if any(token in exc_name for token in ("connection", "network")) or any(
            token in text for token in ("connection", "network", "firewall")
        ):
            return _LLMDiagnostic("network_error", True, "LLM request failed before a valid response was returned", "network_error")
        return _LLMDiagnostic("network_error", False, "LLM channel test failed", "unknown_error")

    @staticmethod
    def _extract_llm_completion_content(response: Any) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
        if response is None:
            return "", "empty_response", "Completion returned no response object", "null_response"

        choices = getattr(response, "choices", None)
        if not choices:
            return "", "format_error", "Completion response did not include choices", "malformed_choices"

        choice = choices[0]
        content_blocks = getattr(choice, "content_blocks", None)
        if content_blocks is None:
            message = getattr(choice, "message", None)
            if message is not None:
                content_blocks = getattr(message, "content_blocks", None)
        message = getattr(choice, "message", None)
        if content_blocks is not None:
            text_parts: List[str] = []
            for block in content_blocks:
                if getattr(block, "type", None) == "text":
                    text = getattr(block, "text", "") or ""
                    if text:
                        text_parts.append(str(text))
                elif hasattr(block, "content") and block.content:
                    text_parts.append(str(block.content))
            content = "".join(text_parts).strip()
            if content:
                return content, None, None, None

        if message is None:
            return "", "format_error", "Completion response did not include a message object", "malformed_choices"
        if not hasattr(message, "content"):
            return "", "format_error", "Completion message did not include a content field", "malformed_choices"
        raw_content = message.content
        if raw_content is None:
            return "", "empty_response", "Completion returned null message content", "null_content"
        content = str(raw_content).strip()
        if not content:
            return "", "empty_response", "Completion returned an empty message content", "empty_content"
        return content, None, None, None

    @staticmethod
    def _extract_llm_discovery_error(response: requests.Response) -> str:
        """Extract a concise error message from a failed model discovery response."""
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                message = str(
                    error_payload.get("message")
                    or error_payload.get("code")
                    or ""
                ).strip()
                if message:
                    return message

            message = str(payload.get("message") or payload.get("detail") or "").strip()
            if message:
                return message

        text = response.text.strip()
        if text:
            return text[:200]
        return f"HTTP {response.status_code}"

    @staticmethod
    def _extract_discovered_llm_models(payload: Any) -> List[str]:
        """Normalize common `/models` response shapes into a unique model ID list."""
        raw_models: List[Any] = []
        if isinstance(payload, dict):
            if isinstance(payload.get("data"), list):
                raw_models = payload["data"]
            elif isinstance(payload.get("models"), list):
                raw_models = payload["models"]
        elif isinstance(payload, list):
            raw_models = payload

        models: List[str] = []
        seen: Set[str] = set()
        for entry in raw_models:
            if isinstance(entry, str):
                model_id = entry.strip()
            elif isinstance(entry, dict):
                model_id = str(
                    entry.get("id") or entry.get("model") or entry.get("name") or ""
                ).strip()
            else:
                model_id = ""

            if not model_id or model_id in seen:
                continue

            seen.add(model_id)
            models.append(model_id)

        return models
