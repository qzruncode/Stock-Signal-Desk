# -*- coding: utf-8 -*-
"""LLM capability probes (json/tools/stream/vision)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.llm.generation_params import apply_litellm_generation_params

from ._types import _LLMDiagnostic

logger = logging.getLogger(__name__)


class LLMCapabilitiesMixin:
    """Probe a configured LLM channel for optional capabilities."""

    _LLM_CAPABILITY_ORDER: Tuple[str, ...] = ("json", "tools", "stream", "vision")
    _LLM_STREAM_CHUNK_LIMIT = 8
    _LLM_CAPABILITY_PROBE_IMAGE = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )

    @classmethod
    def _normalize_llm_capability_checks(cls, capability_checks: Sequence[str]) -> List[str]:
        requested = {str(check).strip().lower() for check in capability_checks if str(check).strip()}
        return [check for check in cls._LLM_CAPABILITY_ORDER if check in requested]

    @classmethod
    def _build_skipped_capability_results(
        cls,
        capability_checks: Sequence[str],
        reason: str,
        message: str,
    ) -> Dict[str, Dict[str, Any]]:
        return {
            capability: cls._build_llm_capability_result(
                capability=capability,
                status="skipped",
                message=message,
                error_code="skipped",
                retryable=False,
                details={"reason": reason},
            )
            for capability in capability_checks
        }

    @classmethod
    def _run_llm_capability_checks(
        cls,
        *,
        litellm_module: Any,
        resolved_model: str,
        selected_api_key: str,
        base_url: str,
        timeout_seconds: float,
        capability_checks: Sequence[str],
    ) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        for capability in capability_checks:
            if capability == "json":
                results[capability] = cls._run_json_capability_check(
                    litellm_module=litellm_module,
                    resolved_model=resolved_model,
                    selected_api_key=selected_api_key,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                )
            elif capability == "tools":
                results[capability] = cls._run_tools_capability_check(
                    litellm_module=litellm_module,
                    resolved_model=resolved_model,
                    selected_api_key=selected_api_key,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                )
            elif capability == "stream":
                results[capability] = cls._run_stream_capability_check(
                    litellm_module=litellm_module,
                    resolved_model=resolved_model,
                    selected_api_key=selected_api_key,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                )
            elif capability == "vision":
                results[capability] = cls._run_vision_capability_check(
                    litellm_module=litellm_module,
                    resolved_model=resolved_model,
                    selected_api_key=selected_api_key,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                )
        return results

    @classmethod
    def _run_json_capability_check(
        cls,
        *,
        litellm_module: Any,
        resolved_model: str,
        selected_api_key: str,
        base_url: str,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        try:
            started_at = time.perf_counter()
            response = litellm_module.completion(
                **cls._build_llm_capability_completion_kwargs(
                    resolved_model=resolved_model,
                    selected_api_key=selected_api_key,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                    messages=[{"role": "user", "content": 'Return exactly this JSON object: {"status":"ok"}'}],
                    max_tokens=64,
                    extra={"response_format": {"type": "json_object"}},
                )
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            content, parse_error_code, parse_error, parse_reason = cls._extract_llm_completion_content(response)
            if parse_error_code:
                return cls._build_llm_capability_result(
                    capability="json",
                    status="failed",
                    message="JSON capability check returned no parseable content",
                    error_code=parse_error_code,
                    retryable=False,
                    latency_ms=latency_ms,
                    details={"reason": parse_reason, "response_error": parse_error},
                )
            try:
                payload = json.loads(content)
            except ValueError:
                return cls._build_llm_capability_result(
                    capability="json",
                    status="failed",
                    message="JSON capability check returned non-JSON content",
                    error_code="format_error",
                    retryable=False,
                    latency_ms=latency_ms,
                    details={"reason": "non_json", "response_preview": content[:80]},
                )
            if not isinstance(payload, dict) or payload.get("status") != "ok":
                return cls._build_llm_capability_result(
                    capability="json",
                    status="failed",
                    message="JSON capability check returned unexpected JSON",
                    error_code="format_error",
                    retryable=False,
                    latency_ms=latency_ms,
                    details={"reason": "non_json", "response_preview": content[:80]},
                )
            return cls._build_llm_capability_result(
                capability="json",
                status="passed",
                message="JSON output capability check passed",
                latency_ms=latency_ms,
                details={"reason": "json_valid"},
            )
        except Exception as exc:
            diagnostic = cls._classify_llm_capability_exception(exc, "json")
            return cls._build_llm_capability_result_from_diagnostic("json", diagnostic, str(exc))

    @classmethod
    def _run_tools_capability_check(
        cls,
        *,
        litellm_module: Any,
        resolved_model: str,
        selected_api_key: str,
        base_url: str,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "dsa_probe_echo",
                    "description": "Return the provided text.",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
            }
        ]
        try:
            started_at = time.perf_counter()
            response = litellm_module.completion(
                **cls._build_llm_capability_completion_kwargs(
                    resolved_model=resolved_model,
                    selected_api_key=selected_api_key,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                    messages=[{"role": "user", "content": "Call the dsa_probe_echo tool with text set to ok."}],
                    max_tokens=64,
                    extra={
                        "tools": tools,
                        "tool_choice": {"type": "function", "function": {"name": "dsa_probe_echo"}},
                    },
                )
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            tool_names = cls._extract_llm_tool_call_names(response)
            if "dsa_probe_echo" not in tool_names:
                return cls._build_llm_capability_result(
                    capability="tools",
                    status="failed",
                    message="Tool calling capability check did not return the probe tool call",
                    error_code="capability_unsupported",
                    retryable=False,
                    latency_ms=latency_ms,
                    details={"reason": "tool_calls_missing", "tool_calls": tool_names},
                )
            return cls._build_llm_capability_result(
                capability="tools",
                status="passed",
                message="Tool calling capability check passed",
                latency_ms=latency_ms,
                details={"reason": "tool_call_returned"},
            )
        except Exception as exc:
            diagnostic = cls._classify_llm_capability_exception(exc, "tools")
            return cls._build_llm_capability_result_from_diagnostic("tools", diagnostic, str(exc))

    @classmethod
    def _run_stream_capability_check(
        cls,
        *,
        litellm_module: Any,
        resolved_model: str,
        selected_api_key: str,
        base_url: str,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        stream = None
        started_at = time.perf_counter()
        try:
            stream = litellm_module.completion(
                **cls._build_llm_capability_completion_kwargs(
                    resolved_model=resolved_model,
                    selected_api_key=selected_api_key,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                    messages=[{"role": "user", "content": "Reply with OK"}],
                    max_tokens=32,
                    extra={"stream": True},
                )
            )
            for index, chunk in enumerate(stream):
                content = cls._extract_llm_stream_chunk_content(chunk)
                if content:
                    latency_ms = int((time.perf_counter() - started_at) * 1000)
                    return cls._build_llm_capability_result(
                        capability="stream",
                        status="passed",
                        message="Streaming capability check passed",
                        latency_ms=latency_ms,
                        details={"reason": "stream_chunk_received"},
                    )
                if index + 1 >= cls._LLM_STREAM_CHUNK_LIMIT:
                    break
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            return cls._build_llm_capability_result(
                capability="stream",
                status="failed",
                message="Streaming capability check returned no content chunks",
                error_code="empty_response",
                retryable=False,
                latency_ms=latency_ms,
                details={"reason": "stream_no_content"},
            )
        except Exception as exc:
            diagnostic = cls._classify_llm_capability_exception(exc, "stream")
            return cls._build_llm_capability_result_from_diagnostic("stream", diagnostic, str(exc))
        finally:
            close_stream = getattr(stream, "close", None)
            if callable(close_stream):
                try:
                    close_stream()
                except Exception as exc:
                    logger.debug("Failed to close LLM stream capability probe: %s", exc)

    @classmethod
    def _run_vision_capability_check(
        cls,
        *,
        litellm_module: Any,
        resolved_model: str,
        selected_api_key: str,
        base_url: str,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        try:
            started_at = time.perf_counter()
            response = litellm_module.completion(
                **cls._build_llm_capability_completion_kwargs(
                    resolved_model=resolved_model,
                    selected_api_key=selected_api_key,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Reply with OK if this image is visible."},
                                {"type": "image_url", "image_url": {"url": cls._LLM_CAPABILITY_PROBE_IMAGE}},
                            ],
                        }
                    ],
                    max_tokens=32,
                )
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            content, parse_error_code, parse_error, parse_reason = cls._extract_llm_completion_content(response)
            if parse_error_code:
                return cls._build_llm_capability_result(
                    capability="vision",
                    status="failed",
                    message="Vision capability check returned no parseable content",
                    error_code=parse_error_code,
                    retryable=False,
                    latency_ms=latency_ms,
                    details={"reason": parse_reason, "response_error": parse_error},
                )
            return cls._build_llm_capability_result(
                capability="vision",
                status="passed",
                message="Vision capability check passed",
                latency_ms=latency_ms,
                details={"reason": "vision_response_received", "response_preview": content[:80]},
            )
        except Exception as exc:
            diagnostic = cls._classify_llm_capability_exception(exc, "vision")
            return cls._build_llm_capability_result_from_diagnostic("vision", diagnostic, str(exc))

    @classmethod
    def _build_llm_capability_completion_kwargs(
        cls,
        *,
        resolved_model: str,
        selected_api_key: str,
        base_url: str,
        timeout_seconds: float,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            timeout = 10.0
        call_kwargs: Dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "timeout": min(max(5.0, timeout), 10.0),
        }
        if selected_api_key:
            call_kwargs["api_key"] = selected_api_key
        if base_url.strip():
            call_kwargs["api_base"] = base_url.strip()
        if extra:
            call_kwargs.update(extra)
        call_kwargs = apply_litellm_generation_params(
            call_kwargs,
            resolved_model,
            0.0,
        )
        return call_kwargs

    @classmethod
    def _build_llm_capability_result(
        cls,
        *,
        capability: str,
        status: str,
        message: str,
        error_code: Optional[str] = None,
        retryable: bool = False,
        latency_ms: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "status": status,
            "message": cls._sanitize_llm_error_text(message),
            "error_code": error_code,
            "stage": f"capability_{capability}",
            "retryable": retryable,
            "latency_ms": latency_ms,
            "details": cls._sanitize_llm_details({"capability": capability, **(details or {})}),
        }

    @classmethod
    def _build_llm_capability_result_from_diagnostic(
        cls,
        capability: str,
        diagnostic: _LLMDiagnostic,
        error: str,
    ) -> Dict[str, Any]:
        details = cls._merge_llm_diagnostic_details({"error": error}, diagnostic)
        return cls._build_llm_capability_result(
            capability=capability,
            status="failed",
            message=diagnostic.message,
            error_code=diagnostic.error_code,
            retryable=diagnostic.retryable,
            details=details,
        )

    @staticmethod
    def _extract_llm_tool_call_names(response: Any) -> List[str]:
        choices = response.get("choices") if isinstance(response, dict) else getattr(response, "choices", None)
        if not choices:
            return []
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)
        if isinstance(message, dict):
            tool_calls = message.get("tool_calls")
        else:
            tool_calls = getattr(message, "tool_calls", None) if message is not None else None
        names: List[str] = []
        for call in tool_calls or []:
            function = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
            if isinstance(function, dict):
                name = str(function.get("name") or "").strip()
            else:
                name = str(getattr(function, "name", "") or "").strip()
            if name:
                names.append(name)
        return names

    @staticmethod
    def _extract_llm_stream_chunk_content(chunk: Any) -> str:
        choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
        if not choices:
            return ""
        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice, dict) else getattr(choice, "delta", None)
        message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)
        for container in (delta, message):
            if not container:
                continue
            content = container.get("content") if isinstance(container, dict) else getattr(container, "content", None)
            if content:
                return str(content)
        content = choice.get("text") if isinstance(choice, dict) else getattr(choice, "text", None)
        return str(content or "")

    @classmethod
    def _classify_llm_capability_exception(cls, exc: Exception, capability: str) -> _LLMDiagnostic:
        text = str(exc).lower()
        capability_tokens = {
            "json": ("response_format", "json_object", "json mode"),
            "tools": ("tool_choice", "tools", "function calling", "tool call"),
            "stream": ("stream", "streaming"),
            "vision": ("image", "image_url", "vision", "multimodal", "multi-modal"),
        }
        unsupported_markers = (
            "unsupported",
            "not support",
            "not supported",
            "unknown parameter",
            "unrecognized parameter",
            "invalid parameter",
            "unexpected keyword",
            "not allowed",
        )
        has_unsupported_marker = any(marker in text for marker in unsupported_markers)
        has_capability_token = any(token in text for token in capability_tokens.get(capability, ()))
        if has_unsupported_marker and (has_capability_token or capability in text):
            return _LLMDiagnostic(
                "capability_unsupported",
                False,
                f"LLM channel does not support {capability} capability",
                "capability_unsupported",
                {"capability": capability},
            )
        return cls._classify_llm_exception(exc)
