# -*- coding: utf-8 -*-
"""Shared contracts for LLM-callable tools.

Every registered tool owns its schema and executor in the module whose file
name matches the tool name.  The registry only discovers these definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable


def enforce_result_contract(tool_name: str, result: Any) -> Dict[str, Any]:
    """Validate and complete the common Agent-facing result envelope."""
    if not isinstance(result, dict):
        raise TypeError(f"{tool_name} must return an object, got {type(result).__name__}")
    payload = dict(result)
    if not isinstance(payload.get("success"), bool):
        raise ValueError(f"{tool_name} result.success must be boolean")
    errors = payload.get("errors")
    if errors is None:
        payload["errors"] = []
    elif not isinstance(errors, list):
        raise ValueError(f"{tool_name} result.errors must be an array")
    payload.setdefault("partial", payload["success"] and bool(payload["errors"]))
    # A successful fallback can still carry primary-source errors. Such a
    # response is usable but necessarily partial; never let an executor hide
    # that distinction by returning partial=False alongside real errors.
    if payload["success"] and payload["errors"]:
        payload["partial"] = True
    if not isinstance(payload.get("partial"), bool):
        raise ValueError(f"{tool_name} result.partial must be boolean")
    if payload["partial"] and not payload["success"]:
        raise ValueError(f"{tool_name} result.partial cannot be true when success is false")
    data_time = payload.setdefault("data_time", None)
    if isinstance(data_time, (date, datetime)):
        payload["data_time"] = data_time.isoformat()
    elif data_time is not None and not isinstance(data_time, str):
        raise ValueError(f"{tool_name} result.data_time must be an ISO string or null")
    payload.setdefault("is_stale", None)
    if payload["is_stale"] is not None and not isinstance(payload["is_stale"], bool):
        raise ValueError(f"{tool_name} result.is_stale must be boolean or null")
    payload.setdefault("freshness_unknown", data_time is None)
    if not isinstance(payload["freshness_unknown"], bool):
        raise ValueError(f"{tool_name} result.freshness_unknown must be boolean")
    if data_time is None and payload["is_stale"] is not None:
        raise ValueError(f"{tool_name} result.is_stale must be null when data_time is null")
    if payload["freshness_unknown"] and payload["is_stale"] is not None:
        raise ValueError(
            f"{tool_name} result.is_stale must be null when freshness is unknown"
        )
    payload.setdefault("warnings", [])
    if not isinstance(payload["warnings"], list):
        raise ValueError(f"{tool_name} result.warnings must be an array")
    return payload


def object_schema(
    properties: Dict[str, Any] | None = None,
    required: Iterable[str] = (),
) -> Dict[str, Any]:
    """Build the JSON-schema shape used by function calling."""
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class ToolSpec:
    """A complete, module-owned tool definition."""

    name: str
    description: str
    parameters: Dict[str, Any]
    executor: Callable[..., Any]
    category: str = "data"

    def to_openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
