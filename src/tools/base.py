# -*- coding: utf-8 -*-
"""Shared contracts for LLM-callable tools.

Every registered tool owns its schema and executor in the module whose file
name matches the tool name.  The registry only discovers these definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable


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

