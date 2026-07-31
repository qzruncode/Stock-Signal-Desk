"""Normalize assistant-ui history into the provider-neutral chat format.

The HTTP endpoint owns request orchestration, while this module owns the
boundary between assistant-ui's AI SDK parts and the OpenAI-style messages
used by the agent runtime.  Keeping the conversion here makes the format
contract testable without importing the full chat endpoint.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from src.agent.progress import strip_agent_progress

_TOOL_DETAIL_ARRAY_KEYS = (
    "recent",
    "recent_periods",
    "history",
    "items",
    "top_movers",
    "bottom_movers",
    "inflow_top",
    "outflow_top",
    "top_holders",
    "holder_changes",
    "daily_trend",
    "score_trend",
    "search_fallback",
    "criteria",
)
_AI_SDK_PART_TYPES = {"text", "tool-call", "tool-result", "reasoning", "file", "image"}


def slim_tool_content(result_str: str) -> str:
    """Drop verbose historical arrays while retaining the tool summary.

    This only applies to tool messages received from previous turns.  Current
    execution results stay complete in the workflow/artifact layer.
    """
    text = (result_str or "").strip()
    if not text or text[0] != "{":
        return result_str
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return result_str
    if not isinstance(payload, dict) or payload.get("_slimmed"):
        return result_str

    slimmed = {key: value for key, value in payload.items() if key not in _TOOL_DETAIL_ARRAY_KEYS}
    slimmed["_slimmed"] = True
    try:
        return json.dumps(slimmed, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return result_str


def is_aisdk_content(content: Any) -> bool:
    """Return whether content is an AI SDK part array."""
    if not isinstance(content, list) or not content:
        return False
    return any(isinstance(part, dict) and part.get("type") in _AI_SDK_PART_TYPES for part in content)


def join_text_parts(parts: List[Dict[str, Any]]) -> str:
    """Join text parts and intentionally omit reasoning parts."""
    chunks = []
    for part in parts:
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        text = str(part.get("text") or "").strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()


def _convert_assistant_message(message: Dict[str, Any]) -> Dict[str, Any]:
    parts = message.get("content") or []
    content_text = strip_agent_progress(join_text_parts(parts))
    tool_calls: List[Dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict) or part.get("type") != "tool-call":
            continue
        tool_call_id = part.get("toolCallId") or f"call_{uuid.uuid4().hex}"
        try:
            arguments = json.dumps(part.get("input") or {}, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            arguments = "{}"
        tool_calls.append(
            {
                "id": tool_call_id,
                "type": "function",
                "function": {"name": part.get("toolName") or "", "arguments": arguments},
            }
        )

    normalized: Dict[str, Any] = {"role": "assistant", "content": content_text or None}
    if tool_calls:
        normalized["tool_calls"] = tool_calls
    return normalized


def _convert_tool_message(message: Dict[str, Any]) -> Dict[str, Any]:
    parts = message.get("content") or []
    tool_result = next(
        (part for part in parts if isinstance(part, dict) and part.get("type") == "tool-result"),
        None,
    )
    if tool_result is None:
        return {"role": "tool", "tool_call_id": f"call_{uuid.uuid4().hex}", "content": ""}

    tool_call_id = tool_result.get("toolCallId") or f"call_{uuid.uuid4().hex}"
    output = tool_result.get("output") or {}
    value = output.get("value") if isinstance(output, dict) else output
    is_error = bool((isinstance(output, dict) and output.get("type") == "error-json") or tool_result.get("isError"))
    try:
        content = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        content = str(value)
    if is_error:
        content = "[工具执行错误] " + content
    return {"role": "tool", "tool_call_id": tool_call_id, "content": slim_tool_content(content)}


def normalize_incoming_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert assistant-ui/OpenAI history into the runtime message contract."""
    normalized: List[Dict[str, Any]] = []
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        try:
            role = str(raw.get("role") or "").strip()
            content = raw.get("content")

            if role == "tool" and raw.get("tool_call_id") is not None:
                normalized.append(raw)
                continue
            if role == "assistant" and raw.get("tool_calls") is not None:
                normalized.append(raw)
                continue
            if role == "assistant" and isinstance(content, list):
                normalized.append(_convert_assistant_message(raw))
                continue
            if role == "tool" and isinstance(content, list):
                normalized.append(_convert_tool_message(raw))
                continue

            if not is_aisdk_content(content):
                normalized_content = content
                if role == "assistant" and isinstance(content, str):
                    normalized_content = strip_agent_progress(content)
                normalized.append(
                    {
                        "role": role or "user",
                        **{
                            key: (normalized_content if key == "content" else value)
                            for key, value in raw.items()
                            if key != "role"
                        },
                    }
                )
                continue

            normalized.append(
                {
                    "role": role or "user",
                    "content": join_text_parts(content) or None,
                }
            )
        except Exception:
            # A malformed historical message must not discard the rest of the
            # conversation; preserve it for the provider to report if needed.
            normalized.append(raw)
    return normalized


__all__ = [
    "is_aisdk_content",
    "join_text_parts",
    "normalize_incoming_messages",
    "slim_tool_content",
]
