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
    """Task-local progress emitted by one atomic tool invocation."""

    message: str
    progress: int | None = None
    reasoning_delta: str | None = None


ToolProgressObserver = Callable[[ToolProgressUpdate], None]
ToolEffect = Literal["read", "side_effect"]
ApprovalPolicy = Literal["required_for_side_effect"]
ToolEffectResolver = Callable[[Mapping[str, Any]], ToolEffect]
_TOOL_PROGRESS_OBSERVER: ContextVar[ToolProgressObserver | None] = ContextVar(
    "tool_progress_observer",
    default=None,
)
_TOOL_IDEMPOTENCY_KEY: ContextVar[str | None] = ContextVar(
    "tool_idempotency_key",
    default=None,
)
_TOOL_EXECUTION_CONTEXT: ContextVar[dict[str, str]] = ContextVar(
    "tool_execution_context",
    default={},
)
_TOOL_EFFECT_APPROVED: ContextVar[bool] = ContextVar(
    "tool_effect_approved",
    default=False,
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


@contextmanager
def tool_idempotency_context(
    idempotency_key: str | None,
) -> Iterator[None]:
    """Expose the durable step key to side-effect adapters.

    Providers that support their own idempotency field can forward this key.
    The value is task-local and is reset before the worker handles other work.
    """
    token = _TOOL_IDEMPOTENCY_KEY.set(idempotency_key or None)
    try:
        yield
    finally:
        _TOOL_IDEMPOTENCY_KEY.reset(token)


def current_tool_idempotency_key() -> str | None:
    """Return the durable idempotency key for the current tool invocation."""
    return _TOOL_IDEMPOTENCY_KEY.get()


@contextmanager
def tool_execution_context(
    *,
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> Iterator[None]:
    token = _TOOL_EXECUTION_CONTEXT.set(
        {
            "conversation_id": str(conversation_id or ""),
            "run_id": str(run_id or ""),
        }
    )
    try:
        yield
    finally:
        _TOOL_EXECUTION_CONTEXT.reset(token)


def current_tool_execution_context() -> dict[str, str]:
    return dict(_TOOL_EXECUTION_CONTEXT.get())


@contextmanager
def tool_effect_approval(approved: bool) -> Iterator[None]:
    """Mark one server-controlled dispatch as approved for side effects."""
    token = _TOOL_EFFECT_APPROVED.set(bool(approved))
    try:
        yield
    finally:
        _TOOL_EFFECT_APPROVED.reset(token)


def current_tool_effect_approval() -> bool:
    return bool(_TOOL_EFFECT_APPROVED.get())


def report_tool_progress(
    message: str,
    *,
    progress: int | None = None,
    reasoning_delta: str | None = None,
) -> None:
    observer = _TOOL_PROGRESS_OBSERVER.get()
    if observer is None:
        return
    observer(
        ToolProgressUpdate(
            message=message,
            progress=(max(0, min(100, int(progress))) if progress is not None else None),
            reasoning_delta=reasoning_delta,
        )
    )


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
        raise ValueError(f"{tool_name} result.is_stale must be null when freshness is unknown")
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
    effect: ToolEffect = "read"
    effect_resolver: ToolEffectResolver | None = None
    approval_policy: ApprovalPolicy = "required_for_side_effect"
    retrieval_text: str = ""
    timeout_seconds: float | None = 120.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.5
    idempotent: bool = True
    sensitive_fields: tuple[str, ...] = ()
    server_controlled_fields: tuple[str, ...] = ("confirmed",)

    def __post_init__(self) -> None:
        if self.effect not in {"read", "side_effect"}:
            raise ValueError(f"{self.name} has invalid effect: {self.effect}")
        if self.approval_policy != "required_for_side_effect":
            raise ValueError(f"{self.name} has invalid approval policy")
        if self.max_attempts < 1:
            raise ValueError(f"{self.name} max_attempts must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError(f"{self.name} timeout_seconds must be positive")
        if not self.retrieval_text.strip():
            object.__setattr__(self, "retrieval_text", self.description)
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
                raise ValueError(f"{self.name} parameters must be generated from args_model")
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

    def model_parameters(self) -> Dict[str, Any]:
        """Return the schema visible to a planning model.

        Confirmation and other server-owned fields are deliberately removed.
        They are restored by the execution policy only after an interrupt has
        been approved, so a model can never self-authorize an effect.
        """
        parameters = dict(self.parameters or {})
        properties = dict(parameters.get("properties") or {})
        controlled = set(self.server_controlled_fields)
        for field_name in controlled:
            properties.pop(field_name, None)
        parameters["properties"] = properties
        parameters["required"] = [
            name for name in parameters.get("required") or [] if name not in controlled
        ]
        return parameters

    def effect_for(self, arguments: Mapping[str, Any]) -> ToolEffect:
        resolved = self.effect_resolver(arguments) if self.effect_resolver is not None else self.effect
        if resolved not in {"read", "side_effect"}:
            raise ValueError(f"{self.name} effect resolver returned invalid value: {resolved}")
        return resolved

    @property
    def effect_mode(self) -> Literal["fixed", "argument_dependent"]:
        """Describe whether effect policy can change with validated arguments."""
        return "argument_dependent" if self.effect_resolver is not None else "fixed"

    def to_openai_schema(self, *, include_server_controlled: bool = False) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": (
                    self.parameters
                    if include_server_controlled
                    else self.model_parameters()
                ),
            },
        }


def effect_by_argument(
    field_name: str,
    side_effect_values: Iterable[str],
) -> ToolEffectResolver:
    """Build a generic per-call effect resolver for mixed read/write tools."""
    normalized = frozenset(str(value).strip().lower() for value in side_effect_values)

    def resolve(arguments: Mapping[str, Any]) -> ToolEffect:
        value = str(arguments.get(field_name) or "").strip().lower()
        return "side_effect" if value in normalized else "read"

    return resolve


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
        return _union(
            [
                _schema_type(f"{name}Choice{index}", item)
                for index, item in enumerate(choices, 1)
                if isinstance(item, dict)
            ]
        )
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        return _union(
            [
                (
                    type(None)
                    if item == "null"
                    else _schema_type(
                        name,
                        {**schema, "type": item},
                    )
                )
                for item in raw_type
            ]
        )
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
        item_type = _schema_type(f"{name}Item", item_schema) if isinstance(item_schema, dict) else Any
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
    return Annotated[value_type, Field(**constraints)] if constraints else value_type


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
        default = ... if field_name in required else field_schema.get("default") if "default" in field_schema else None
        if default is None and field_name not in required:
            field_type = field_type | type(None)
        fields[field_name] = (field_type, default)
    return create_model(
        _model_name(name),
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
