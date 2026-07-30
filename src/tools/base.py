# -*- coding: utf-8 -*-
"""Shared contracts for LLM-callable tools.

Every registered tool owns its schema and executor in the module whose file
name matches the tool name.  The registry only discovers these definitions.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Annotated, Any, Callable, Dict, Iterable, Iterator, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, JsonValue, create_model


@dataclass(frozen=True)
class ToolProgressUpdate:
    """Task-local progress emitted by a tool into its owning Workflow."""

    message: str
    progress: int | None = None
    reasoning_delta: str | None = None


ToolProgressObserver = Callable[[ToolProgressUpdate], None]
GuardBlockedResultProjector = Callable[
    [Mapping[str, Any]],
    Dict[str, Any],
]
_TOOL_PROGRESS_OBSERVER: ContextVar[ToolProgressObserver | None] = ContextVar(
    "tool_progress_observer",
    default=None,
)


@contextmanager
def tool_progress_observer(
    observer: ToolProgressObserver | None,
) -> Iterator[None]:
    token = _TOOL_PROGRESS_OBSERVER.set(observer)
    try:
        yield
    finally:
        _TOOL_PROGRESS_OBSERVER.reset(token)


def report_tool_progress(
    message: str,
    *,
    progress: int | None = None,
    reasoning_delta: str | None = None,
) -> None:
    observer = _TOOL_PROGRESS_OBSERVER.get()
    if observer is None:
        return
    observer(ToolProgressUpdate(
        message=message,
        progress=(
            max(0, min(100, int(progress)))
            if progress is not None
            else None
        ),
        reasoning_delta=reasoning_delta,
    ))


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
    parameters: Dict[str, Any] | None
    executor: Callable[..., Any]
    category: str = "data"
    args_model: type[BaseModel] | None = None
    result_model: type[BaseModel] | None = None
    failure_result: Callable[
        [Mapping[str, Any], str, int],
        Dict[str, Any],
    ] | None = None
    guard_blocked_result: GuardBlockedResultProjector | None = None

    def __post_init__(self) -> None:
        if self.args_model is None and self.parameters is None:
            raise ValueError(f"{self.name} requires args_model or parameters")
        if self.args_model is None:
            generated_model = model_from_object_schema(
                f"{self.name}_args",
                self.parameters or {},
            )
            object.__setattr__(self, "args_model", generated_model)
            object.__setattr__(
                self,
                "parameters",
                generated_model.model_json_schema(),
            )
        else:
            generated = self.args_model.model_json_schema()
            if self.parameters is not None and self.parameters != generated:
                raise ValueError(
                    f"{self.name} parameters must be generated from args_model"
                )
            object.__setattr__(self, "parameters", generated)
        if self.result_model is None:
            object.__setattr__(
                self,
                "result_model",
                create_model(
                    f"{_model_name(self.name)}Result",
                    __base__=LegacyCompatibleToolResult,
                ),
            )

    def to_openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class TypedToolResult(BaseModel):
    """Closed result base for tools migrated to an exact output contract."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    partial: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_time: str | None = None
    is_stale: bool | None = None
    freshness_unknown: bool = False


class LegacyCompatibleToolResult(TypedToolResult):
    """Temporary typed envelope for unchanged legacy business payloads.

    The common status/freshness fields are still validated.  Only tools that
    have not yet declared an exact result model receive this compatibility
    adapter; migrated control-plane tools inherit the closed base above.
    """

    model_config = ConfigDict(extra="allow")


def _model_name(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value)
    return "".join(part[:1].upper() + part[1:] for part in parts) or "Anonymous"


def _union(types: list[Any]) -> Any:
    unique: list[Any] = []
    for item in types:
        if item not in unique:
            unique.append(item)
    if not unique:
        return Any
    result = unique[0]
    for item in unique[1:]:
        result = result | item
    return result


def _schema_type(
    name: str,
    schema: Dict[str, Any],
) -> Any:
    if "const" in schema:
        return Literal.__getitem__((schema["const"],))
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return Literal.__getitem__(tuple(enum))
    choices = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(choices, list) and choices:
        return _union([
            _schema_type(f"{name}Choice{index}", item)
            for index, item in enumerate(choices, 1)
            if isinstance(item, dict)
        ])
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        return _union([
            type(None) if item == "null" else _schema_type(
                name,
                {**schema, "type": item},
            )
            for item in raw_type
        ])
    if raw_type == "object" or isinstance(schema.get("properties"), dict):
        properties = schema.get("properties")
        additional = schema.get("additionalProperties")
        if not properties and additional is True:
            return dict[str, JsonValue]
        if not properties and isinstance(additional, dict):
            return dict[str, _schema_type(f"{name}Value", additional)]
        return model_from_object_schema(name, schema)
    if raw_type == "array":
        item_schema = schema.get("items")
        item_type = (
            _schema_type(f"{name}Item", item_schema)
            if isinstance(item_schema, dict)
            else Any
        )
        return list[item_type]
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "null": type(None),
    }.get(raw_type, Any)


def _annotated_type(
    name: str,
    schema: Dict[str, Any],
) -> Any:
    value_type = _schema_type(name, schema)
    constraints: dict[str, Any] = {}
    for source, target in (
        ("minimum", "ge"),
        ("maximum", "le"),
        ("exclusiveMinimum", "gt"),
        ("exclusiveMaximum", "lt"),
        ("minLength", "min_length"),
        ("maxLength", "max_length"),
        ("minItems", "min_length"),
        ("maxItems", "max_length"),
        ("pattern", "pattern"),
        ("description", "description"),
    ):
        if source in schema:
            constraints[target] = schema[source]
    return (
        Annotated[value_type, Field(**constraints)]
        if constraints
        else value_type
    )


def model_from_object_schema(
    name: str,
    schema: Dict[str, Any],
) -> type[BaseModel]:
    """Convert a closed legacy JSON object schema into its runtime model once.

    Registered tools expose only the generated model schema after this point;
    argument validation and function-calling documentation therefore consume
    the exact same runtime model.
    """

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = set(schema.get("required") or ())
    fields: dict[str, tuple[Any, Any]] = {}
    for field_name, raw in properties.items():
        field_schema = raw if isinstance(raw, dict) else {}
        field_type = _annotated_type(
            f"{_model_name(name)}{_model_name(field_name)}",
            field_schema,
        )
        default = (
            ...
            if field_name in required
            else field_schema.get("default")
            if "default" in field_schema
            else None
        )
        if default is None and field_name not in required:
            field_type = field_type | type(None)
        fields[field_name] = (field_type, default)
    return create_model(
        _model_name(name),
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
