"""Gateway/schema helpers for the professional dimension model call."""

from __future__ import annotations

import json
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from src.services.buy_criteria.professional_analysis import (
    ForcedSchemaResponseError,
    _parse_verdict_json,
)
def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

def _normalized_usage(response: Any) -> dict[str, int]:
    usage = _field(response, "usage")
    return {key: int(_field(usage, key) or 0) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}

def _response_failure_payload(response: Any) -> dict[str, Any]:
    choices = _field(response, "choices") or []
    if not choices:
        return {
            "choices": [],
            "model": _field(response, "model"),
            "usage": _normalized_usage(response),
        }
    choice = choices[0]
    message = _field(choice, "message")
    return {
        "finish_reason": _field(choice, "finish_reason"),
        "content": _field(message, "content"),
        "reasoning_content": _field(message, "reasoning_content"),
        "tool_calls": _field(message, "tool_calls") or [],
        "model": _field(response, "model"),
        "usage": _normalized_usage(response),
    }

def _payload_from_response(response: Any, tool_name: str) -> dict[str, Any]:
    choices = _field(response, "choices") or []
    if not choices:
        payload = _response_failure_payload(response)
        raise ForcedSchemaResponseError(
            "professional analysis response has no choices",
            payload,
            issues=[
                {
                    "pointer": "/choices",
                    "code": "choices_missing",
                    "expected": ("one response choice containing the forced tool call"),
                    "allowed": [tool_name],
                }
            ],
        )
    choice = choices[0]
    message = _field(choice, "message")
    tool_calls = _field(message, "tool_calls") or []
    for call in tool_calls:
        function = _field(call, "function")
        if _field(function, "name") != tool_name:
            continue
        arguments = _field(function, "arguments")
        try:
            payload = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError as exc:
            raise ForcedSchemaResponseError(
                "professional analysis tool arguments are not valid JSON",
                {
                    "tool_name": tool_name,
                    "arguments": arguments,
                },
                issues=[
                    {
                        "pointer": "/arguments",
                        "code": "json_invalid",
                        "expected": "one valid JSON object matching the schema",
                        "allowed": [],
                    }
                ],
            ) from exc
        if not isinstance(payload, dict):
            raise ForcedSchemaResponseError(
                "professional analysis tool arguments are not an object",
                {
                    "tool_name": tool_name,
                    "arguments": payload,
                },
                issues=[
                    {
                        "pointer": "/arguments",
                        "code": "object_type_required",
                        "expected": "JSON object",
                        "allowed": [],
                    }
                ],
            )
        return payload
    # Compatibility fallback for gateways that return forced JSON as text.
    content = _field(message, "content")
    if isinstance(content, str):
        parsed = _parse_verdict_json(content)
        if isinstance(parsed, dict):
            return parsed
    reasoning = _field(message, "reasoning_content")
    failure_payload = _response_failure_payload(response)
    raise ForcedSchemaResponseError(
        (
            "professional analysis response did not call the forced schema"
            f" (finish={_field(choice, 'finish_reason')},"
            f" content_chars={len(content) if isinstance(content, str) else 0},"
            f" reasoning_chars={len(reasoning) if isinstance(reasoning, str) else 0})"
        ),
        failure_payload,
        issues=[
            {
                "pointer": "/choices/0/message/tool_calls",
                "code": "forced_schema_missing",
                "expected": (f"exactly one {tool_name} tool call whose arguments " "match the supplied schema"),
                "allowed": [tool_name],
            }
        ],
    )

def _validation_issues(exc: ValidationError) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for error in exc.errors(include_url=False):
        location = error.get("loc") or ()
        pointer = "/" + "/".join(str(value).replace("~", "~0").replace("/", "~1") for value in location)
        issues.append(
            {
                "pointer": pointer or "/",
                "code": str(error.get("type") or "schema_invalid"),
                "expected": str(error.get("msg") or "value matching schema"),
                "allowed": (error.get("ctx") if isinstance(error.get("ctx"), dict) else []),
            }
        )
    return issues

def _collect_streamed_response(
    response_stream: Any,
    *,
    on_reasoning: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    tool_arguments: dict[int, list[str]] = {}
    tool_names: dict[int, str] = {}
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    model_used = "anthropic-gateway"
    usage: dict[str, int] = {}
    finish_reason: Any = None
    for chunk in response_stream:
        model_used = str(_field(chunk, "model") or model_used)
        chunk_usage = _normalized_usage(chunk)
        if any(chunk_usage.values()):
            usage = chunk_usage
        choices = _field(chunk, "choices") or []
        if not choices:
            continue
        choice = choices[0]
        finish_reason = _field(choice, "finish_reason") or finish_reason
        delta = _field(choice, "delta")
        content = _field(delta, "content")
        if isinstance(content, str) and content:
            content_parts.append(content)
        reasoning_values: list[str] = []
        for name in ("reasoning_content", "reasoning"):
            value = _field(delta, name)
            if isinstance(value, str) and value:
                reasoning_values.append(value)
        model_extra = _field(delta, "model_extra")
        if isinstance(model_extra, dict):
            for name in ("reasoning_content", "reasoning"):
                value = model_extra.get(name)
                if isinstance(value, str) and value and value not in reasoning_values:
                    reasoning_values.append(value)
        reasoning_delta = "".join(reasoning_values)
        if reasoning_delta:
            reasoning_parts.append(reasoning_delta)
            if on_reasoning:
                on_reasoning(reasoning_delta)
        for tool_call in _field(delta, "tool_calls") or []:
            raw_index = _field(tool_call, "index")
            try:
                index = int(raw_index or 0)
            except (TypeError, ValueError):
                index = 0
            function = _field(tool_call, "function")
            name = _field(function, "name")
            if isinstance(name, str) and name:
                current_name = tool_names.get(index, "")
                tool_names[index] = name if not current_name or current_name == name else current_name + name
            arguments = _field(function, "arguments")
            if isinstance(arguments, str) and arguments:
                tool_arguments.setdefault(index, []).append(arguments)
    return {
        "model": model_used,
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "content": "".join(content_parts),
                    "reasoning_content": "".join(reasoning_parts),
                    "tool_calls": [
                        {
                            "function": {
                                "name": tool_names.get(index),
                                "arguments": "".join(parts),
                            },
                        }
                        for index, parts in sorted(tool_arguments.items())
                    ],
                },
            }
        ],
        "usage": usage,
    }

def _forced_call(
    *,
    evidence: dict[str, Any],
    on_reasoning: Callable[[str], None] | None,
    inline_json_schema: Callable[[dict[str, Any]], dict[str, Any]],
    tool_name: str,
    description: str,
    system_prompt: str,
    user_prompt: str,
    response_model: type[BaseModel],
    max_tokens: int,
    call_type: str,
) -> BaseModel:
    from src.llm.anthropic_gateway import completion_gateway
    from src.storage import persist_llm_usage

    schema = inline_json_schema(response_model.model_json_schema())
    tool = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": schema,
        },
    }
    request = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [tool],
        "tool_choice": {
            "type": "function",
            "function": {"name": tool_name},
        },
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if on_reasoning:
        response = _collect_streamed_response(
            completion_gateway(
                **request,
                stream=True,
                stream_options={"include_usage": True},
            ),
            on_reasoning=on_reasoning,
        )
    else:
        response = completion_gateway(
            **request,
        )
    persist_llm_usage(
        _normalized_usage(response),
        str(_field(response, "model") or "anthropic-gateway"),
        call_type,
        stock_code=str(evidence.get("symbol") or "").strip() or None,
    )
    payload = _payload_from_response(response, tool_name)
    try:
        parsed = response_model.model_validate(payload)
    except ValidationError as exc:
        raise ForcedSchemaResponseError(
            "professional analysis payload failed exact schema validation",
            payload,
            issues=_validation_issues(exc),
        ) from exc
    return parsed
