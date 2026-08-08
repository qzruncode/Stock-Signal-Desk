"""Complete operation and source directory supplied to every Agent turn.

There is deliberately no lexical pre-ranking, capability bucket, or
``load_schemas`` phase.  ``create_agent`` binds every operation schema and the
model receives this compact directory to understand each operation's available
sources before it decides whether to call a tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from src.tools.registry import ToolRegistry


def _fields(parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
    properties = parameters.get("properties")
    if not isinstance(properties, Mapping):
        return []
    required = {str(value) for value in parameters.get("required") or []}
    fields: list[dict[str, Any]] = []
    for name, raw in properties.items():
        schema = raw if isinstance(raw, Mapping) else {}
        entry: dict[str, Any] = {
            "name": str(name),
            "required": str(name) in required,
        }
        if schema.get("description"):
            entry["description"] = str(schema["description"])[:300]
        if isinstance(schema.get("enum"), list):
            # Values are important for simple operation fields.  Source ids
            # are repeated in the richer source catalog below.
            values = [str(value) for value in schema["enum"]]
            entry["enum"] = values if str(name) != "source_id" else f"{len(values)} source ids below"
        if "default" in schema:
            entry["default"] = schema["default"]
        fields.append(entry)
    return fields


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    effect: str
    category: str
    fields: tuple[dict[str, Any], ...]
    sources: tuple[dict[str, Any], ...]

    def compact(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "operation": self.name,
            "description": self.description,
            "effect": self.effect,
            "fields": list(self.fields),
        }
        if self.sources:
            entry["sources"] = list(self.sources)
        return entry


class ToolCatalog:
    """Static view of all model-callable operations and their source IDs."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        descriptors: list[ToolDescriptor] = []
        for name in registry.get_tool_names():
            spec = registry.get_tool(name)
            if spec is None:
                continue
            descriptors.append(
                ToolDescriptor(
                    name=name,
                    description=" ".join(str(spec.description or "").split()),
                    effect=spec.effect,
                    category=str(spec.category or "data"),
                    fields=tuple(_fields(spec.model_parameters())),
                    sources=tuple(dict(item) for item in spec.source_catalog),
                )
            )
        self._descriptors = tuple(descriptors)
        self._by_name = {item.name: item for item in descriptors}

    @property
    def size(self) -> int:
        return len(self._descriptors)

    def compact_catalog(self) -> list[dict[str, Any]]:
        """Compatibility name for the complete, non-ranked operation directory."""
        return [item.compact() for item in self._descriptors]

    def descriptor(self, name: str) -> dict[str, Any] | None:
        item = self._by_name.get(str(name))
        return item.compact() if item is not None else None

    def model_context(self) -> str:
        """Return all available operations and sources in one compact payload."""
        return json.dumps(
            self.compact_catalog(),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )


__all__ = ["ToolCatalog", "ToolDescriptor"]
