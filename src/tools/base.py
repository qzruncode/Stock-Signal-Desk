# -*- coding: utf-8 -*-
"""Shared contracts for LLM-callable, atomic operations.

An operation is model-callable; an upstream source is data selected by that
operation through an explicit ``source_id``.  This keeps a growing RSS/quote/
web source catalog visible without turning every source into a separate tool
schema or hiding a workflow behind one opaque convenience function.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Annotated, Any, Callable, Dict, Iterable, Iterator, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, JsonValue, create_model, field_validator
from pydantic_core import PydanticUndefined


@dataclass(frozen=True)
class ToolProgressUpdate:
    """Task-local progress emitted by one atomic tool invocation."""

    message: str
    progress: int | None = None
    reasoning_delta: str | None = None


ToolProgressObserver = Callable[[ToolProgressUpdate], None]
ToolEffect = Literal["read", "side_effect"]
ApprovalPolicy = Literal["required_for_side_effect"]
DataTimeProvenance = Literal["source", "inferred", "unavailable"]
ToolEffectResolver = Callable[[Mapping[str, Any]], ToolEffect]


def parse_iso_data_time(value: Any) -> datetime | None:
    """Parse a source timestamp into an aware datetime when possible.

    Source adapters may return either an ISO datetime or an ISO calendar date.
    Naive values are interpreted in the host's local timezone so the common
    result contract can compare them without silently treating them as UTC.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str) and value.strip():
        normalized = value.strip()
        if normalized.endswith(("Z", "z")):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(normalized), datetime.min.time())
            except ValueError:
                return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed

# These are the common structural shapes used by the existing source tools.
# They describe the result envelope only; a tool still owns the meaning of its
# domain fields.  Keeping this list here gives the executor, evidence ledger,
# audit projection and evaluators one semantic boundary instead of each
# re-implementing a different ``success``/``empty`` heuristic.
_RESULT_COLLECTION_FIELDS = frozenset(
    {
        "items",
        "results",
        "rows",
        "records",
        "entries",
        "data",
        "documents",
        "articles",
        "result_items",
        "resultitems",
        "boards",
        "segments",
    }
)
_RESULT_SINGLETON_FIELDS = frozenset(
    {"item", "calculation", "summary", "content", "output", "message"}
)
_RESULT_COUNT_FIELDS = frozenset(
    {"count", "total", "result_count", "item_count", "returned_count", "record_count", "row_count", "total_count"}
)
_RESULT_METADATA_FIELDS = frozenset(
    {
        "success",
        "partial",
        "id",
        "action_id",
        "tool_name",
        "tool_call_id",
        "effect",
        "fingerprint",
        "arguments",
        "display_arguments",
        "display_result",
        "outcome",
        "result",
        "errors",
        "warnings",
        "error_code",
        "data_time",
        "data_time_provenance",
        "data_time_note",
        "data_time_inferred",
        "data_time_applicable",
        "is_stale",
        "freshness_unknown",
        "fallback_used",
        "fallback_provider",
        "fallback_attempted",
        "fallback_recommended",
        "source",
        "sources",
        "source_id",
        "source_key",
        "source_scope",
        "source_origin",
        "source_refs",
        "source_attempts",
        "reference_links",
        "reference_urls",
        "requested_symbols",
        "missing_symbols",
        "invalid_symbols",
        "symbol",
        "name",
        "_cached",
        "_fetched_at",
        "retrieved_at",
        "has_data",
        "data_status",
        "usable",
        "evidence_eligible",
    }
)


def _meaningful_result_value(
    value: Any,
    *,
    key: str = "",
    depth: int = 0,
    data_context: bool = False,
) -> bool:
    """Return whether a value contains answerable source data.

    The check is intentionally structural.  It recognizes singleton payloads
    such as ``item`` and ``calculation`` but ignores transport/provenance
    metadata such as ``source_attempts``.  It is not a domain validator.
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, Mapping):
        if depth >= 4:
            return bool(value)
        return any(
            _meaningful_result_value(
                child,
                key=str(raw_key),
                depth=depth + 1,
                data_context=data_context,
            )
            for raw_key, child in value.items()
            if (
                str(raw_key).strip().lower() not in _RESULT_METADATA_FIELDS
                or (data_context and str(raw_key).strip().lower() in {"name", "symbol"})
            )
        )
    if isinstance(value, (list, tuple, set)):
        return any(
            _meaningful_result_value(
                child,
                depth=depth + 1,
                data_context=data_context,
            )
            for child in value
        )
    return True


def result_has_data(result: Mapping[str, Any] | None) -> bool:
    """Determine whether a successful result contains answerable data.

    ``success`` describes execution, not payload usability.  An empty list,
    missing scalar calculation, or a response that only contains provider
    attempts therefore does not qualify as data.  A non-empty singleton
    ``item``/``calculation`` does qualify.
    """
    if not isinstance(result, Mapping):
        return False

    for raw_key, value in result.items():
        key = str(raw_key).strip().lower()
        if key in _RESULT_COUNT_FIELDS or key.endswith("_count"):
            try:
                if int(value) > 0:
                    return True
            except (TypeError, ValueError):
                pass

    for raw_key, value in result.items():
        key = str(raw_key).strip().lower()
        if key in _RESULT_COLLECTION_FIELDS or key in _RESULT_SINGLETON_FIELDS:
            if _meaningful_result_value(value, key=key, data_context=True):
                return True

    # Some legacy tools expose a domain scalar directly instead of using one
    # of the conventional collection/singleton keys.  Count any non-envelope
    # value, while keeping routing and freshness metadata out of the result.
    return any(
        _meaningful_result_value(value, key=key)
        for raw_key, value in result.items()
        if (key := str(raw_key).strip().lower()) not in _RESULT_METADATA_FIELDS
        and key not in _RESULT_COUNT_FIELDS
        and not key.startswith("_")
    )


def classify_result_semantics(result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the common execution/data/evidence semantics for one result."""
    payload = result if isinstance(result, Mapping) else {}
    success = payload.get("success") is True
    has_data = bool(success and result_has_data(payload))
    stale = payload.get("is_stale") is True
    partial = payload.get("partial") is True
    freshness_unknown = payload.get("freshness_unknown") is True
    data_time_applicable = payload.get("data_time_applicable") is not False
    if not success:
        data_status = "error"
    elif not has_data:
        data_status = "empty"
    elif stale:
        data_status = "stale"
    elif partial:
        data_status = "partial"
    elif payload.get("fallback_used") is True:
        data_status = "fallback"
    elif freshness_unknown and data_time_applicable:
        data_status = "freshness_unknown"
    else:
        data_status = "usable"

    requested_eligibility = payload.get("evidence_eligible")
    evidence_eligible = (
        bool(requested_eligibility)
        if isinstance(requested_eligibility, bool)
        else has_data
    )
    return {
        "has_data": has_data,
        "data_status": data_status,
        "usable": has_data,
        "evidence_eligible": bool(evidence_eligible and has_data),
    }


def evidence_record_is_eligible(record: Mapping[str, Any] | None) -> bool:
    """Return whether an executor evidence record may support a claim.

    This deliberately keeps a successful-but-empty observation in the audit
    trail while preventing it from becoming a selectable citation.  Historical
    or time-unknown data can still be evidence for a claim that states the
    corresponding limitation; freshness rules are enforced by the claim
    validator rather than silently deleting that context.
    """
    if not isinstance(record, Mapping):
        return False
    if record.get("success") is not True:
        return False
    if str(record.get("effect") or "read") == "side_effect":
        return False
    if record.get("evidence_eligible") is False:
        return False
    if isinstance(record.get("has_data"), bool):
        return bool(record["has_data"])
    payload = record.get("result")
    if not isinstance(payload, Mapping):
        payload = record
    # Historical evidence rows predate the nested result contract and store
    # ``success`` on the envelope while keeping the payload as a plain mapping
    # (for example ``{"headline": ...}``).  Preserve their valid data without
    # treating the envelope's action metadata as source data.
    if payload is not record and "success" not in payload and record.get("success") is True:
        payload = {**payload, "success": True}
    return bool(classify_result_semantics(payload)["evidence_eligible"])


def evidence_records_with_citations(values: Iterable[Any]) -> list[dict[str, Any]]:
    """Expand stable, individually addressable result hits into evidence rows.

    The original action record remains available for compatibility and audit.
    A child row narrows its source and payload to one result item, allowing a
    final answer to cite the exact hit instead of every candidate returned by
    the same tool call.
    """
    expanded: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        parent = dict(value)
        expanded.append(parent)
        result = parent.get("result")
        if not isinstance(result, Mapping):
            continue
        hits = result.get("results")
        if not isinstance(hits, (list, tuple)):
            hits = result.get("result_items")
        if not isinstance(hits, (list, tuple)):
            continue
        parent_id = str(parent.get("evidence_id") or parent.get("id") or "").strip()
        for raw_hit in hits:
            if not isinstance(raw_hit, Mapping):
                continue
            hit = dict(raw_hit)
            evidence_id = str(hit.get("evidence_id") or "").strip()
            if not evidence_id:
                citation_id = str(hit.get("citation_id") or hit.get("id") or "").strip()
                if citation_id.startswith("kb_"):
                    evidence_id = f"ev_{citation_id}"
            if not evidence_id.startswith("ev_") or evidence_id == parent_id:
                continue
            source = str(hit.get("source_url") or hit.get("url") or "").strip()
            if not source:
                continue
            expanded.append({
                **parent,
                "id": evidence_id,
                "evidence_id": evidence_id,
                "source_refs": [source],
                "result": hit,
                "citation_item": True,
                "citation_parent_id": parent_id or None,
            })
    return expanded


def citation_scoped_evidence_records(values: Iterable[Any]) -> list[dict[str, Any]]:
    """Prefer exact hit records over their aggregate action-level envelope."""
    records = evidence_records_with_citations(values)
    parents_with_hits = {
        str(item.get("citation_parent_id") or "").strip()
        for item in records
        if item.get("citation_item") is True
        and str(item.get("citation_parent_id") or "").strip()
    }
    return [
        item
        for item in records
        if item.get("citation_item") is True
        or str(item.get("evidence_id") or item.get("id") or "") not in parents_with_hits
    ]
# A model never chooses an operation, workflow, capability, or provider route
# through an arbitrary argument.  ``source_id`` is the one intentional
# exception: generic read operations use it to name one entry from their
# declared, model-visible source catalog.  Most source operations remain
# strict; the completed daily-bar operations may use their declared fallback
# gateway and report the actual provider in the result.
MODEL_TOOL_SELECTOR_FIELDS = frozenset(
    {
        "action",
        "operation",
        "workflow",
        "capability",
        "tool",
        "tool_name",
        "provider",
        "provider_name",
        "source",
        "source_name",
        "source_type",
        "route",
        "route_path",
        "namespace",
        "params",
        "fallback",
        "fallback_provider",
    }
)
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
    tenant_id: str | None = None,
    owner_id: str | None = None,
    knowledge_base_ids: Iterable[str] | None = None,
) -> Iterator[None]:
    context: dict[str, Any] = {
        "conversation_id": str(conversation_id or ""),
        "run_id": str(run_id or ""),
        "tenant_id": str(tenant_id or "local"),
        "owner_id": str(owner_id or "admin"),
        "knowledge_base_ids": ",".join(
            dict.fromkeys(str(value).strip() for value in knowledge_base_ids or [] if str(value).strip())
        ),
    }
    token = _TOOL_EXECUTION_CONTEXT.set(context)
    try:
        yield
    finally:
        _TOOL_EXECUTION_CONTEXT.reset(token)


def current_tool_execution_context() -> dict[str, Any]:
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
    """Validate and complete the common Agent-facing result envelope.

    ``data_time`` is reserved for the source's own timestamp. Transport
    timestamps such as ``_fetched_at`` and ``retrieved_at`` describe when this
    service obtained a response, not when the source data was true.
    """
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
    data_time = payload["data_time"]
    invalid_data_time = isinstance(data_time, str) and bool(data_time.strip()) and parse_iso_data_time(data_time) is None
    if invalid_data_time:
        payload["data_time"] = None
        payload["data_time_provenance"] = "unavailable"
        payload["is_stale"] = None
    elif isinstance(data_time, str) and not data_time.strip():
        payload["data_time"] = None
        payload["data_time_provenance"] = "unavailable"
        payload["is_stale"] = None
    data_time = payload["data_time"]
    expected_provenance: DataTimeProvenance = (
        "unavailable"
        if data_time is None
        else ("inferred" if payload.get("data_time_inferred") is True else "source")
    )
    provenance = payload.get("data_time_provenance")
    if provenance is None:
        payload["data_time_provenance"] = expected_provenance
    elif provenance not in {"source", "inferred", "unavailable"}:
        raise ValueError(
            f"{tool_name} result.data_time_provenance must be source, inferred, or unavailable"
        )
    elif provenance != expected_provenance:
        raise ValueError(
            f"{tool_name} result.data_time_provenance does not match data_time semantics"
        )
    data_time_note = payload.get("data_time_note")
    if data_time_note is not None and not isinstance(data_time_note, str):
        raise ValueError(f"{tool_name} result.data_time_note must be a string or null")
    if invalid_data_time:
        invalid_note = "来源返回的 data_time 无法解析为 ISO 8601，已降级为时间未知。"
        data_time_note = f"{data_time_note} {invalid_note}".strip() if data_time_note else invalid_note
        payload["data_time_note"] = data_time_note
        warnings = payload.setdefault("warnings", [])
        if not isinstance(warnings, list):
            raise ValueError(f"{tool_name} result.warnings must be an array")
        warning = "来源返回了无法解析的 data_time；已按时间未知处理。"
        if warning not in warnings:
            warnings.append(warning)
    if data_time is None and not data_time_note:
        payload["data_time_note"] = "数据源未提供原始数据时间；仅能确认本次查询已完成。"
    payload.setdefault("is_stale", None)
    if payload["is_stale"] is not None and not isinstance(payload["is_stale"], bool):
        raise ValueError(f"{tool_name} result.is_stale must be boolean or null")
    if data_time is None:
        # A transport completion time must never make source freshness known.
        payload["freshness_unknown"] = True
    else:
        payload.setdefault("freshness_unknown", False)
    if not isinstance(payload["freshness_unknown"], bool):
        raise ValueError(f"{tool_name} result.freshness_unknown must be boolean")
    if data_time is None and payload["is_stale"] is not None:
        raise ValueError(f"{tool_name} result.is_stale must be null when data_time is null")
    if payload["freshness_unknown"] and payload["is_stale"] is not None:
        raise ValueError(f"{tool_name} result.is_stale must be null when freshness is unknown")
    payload.setdefault("warnings", [])
    if not isinstance(payload["warnings"], list):
        raise ValueError(f"{tool_name} result.warnings must be an array")
    # Populate the semantic part of the common contract at the one boundary
    # every registry execution crosses.  ``success`` remains the execution
    # outcome; these fields describe whether the returned payload contains
    # answerable data and may enter the evidence ledger.
    payload.update(classify_result_semantics(payload))
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
    """A complete, module-owned atomic tool definition."""

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
    timeout_seconds: float | None = 120.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.5
    idempotent: bool = True
    sensitive_fields: tuple[str, ...] = ()
    server_controlled_fields: tuple[str, ...] = ("confirmed",)
    # Free-text retrieval inputs are declared by their owning atomic tool.
    # The control plane can then preserve user-specified temporal intent when
    # it normalizes model-authored searches, without guessing from field names.
    retrieval_query_fields: tuple[str, ...] = ()
    # Optional, declarative source directory for a generic operation.  It is
    # pure metadata: selecting ``source_id`` can only choose one listed source
    # for the current atomic operation, never another operation or fallback.
    source_catalog: tuple[dict[str, Any], ...] = ()
    # ``None`` keeps the shared category default; external tools whose legacy
    # category is also used by local/persisted reads can explicitly opt in or
    # out without duplicating fallback logic in their executor.
    web_fallback: bool | None = None

    def __post_init__(self) -> None:
        if self.effect not in {"read", "side_effect"}:
            raise ValueError(f"{self.name} has invalid effect: {self.effect}")
        if self.web_fallback is not None and not isinstance(self.web_fallback, bool):
            raise ValueError(f"{self.name} web_fallback must be boolean or null")
        if self.approval_policy != "required_for_side_effect":
            raise ValueError(f"{self.name} has invalid approval policy")
        if self.max_attempts < 1:
            raise ValueError(f"{self.name} max_attempts must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError(f"{self.name} timeout_seconds must be positive")
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
        parameter_fields = set((self.parameters or {}).get("properties") or {})
        selector_fields = sorted(parameter_fields & MODEL_TOOL_SELECTOR_FIELDS)
        if selector_fields:
            raise ValueError(
                f"{self.name} exposes an operation or source selector: "
                + ", ".join(selector_fields)
            )
        if "source_id" in parameter_fields and not self.source_catalog:
            raise ValueError(
                f"{self.name} exposes source_id without a declared source_catalog"
            )
        if self.source_catalog:
            declared_source_ids = {
                str(item.get("id") or "").strip()
                for item in self.source_catalog
                if isinstance(item, Mapping)
            }
            if not declared_source_ids or "" in declared_source_ids:
                raise ValueError(f"{self.name} source_catalog entries require stable id values")
            source_schema = (self.parameters or {}).get("properties", {}).get("source_id")
            enum_values = set(source_schema.get("enum") or ()) if isinstance(source_schema, Mapping) else set()
            if enum_values and enum_values != declared_source_ids:
                raise ValueError(
                    f"{self.name} source_id enum must exactly match source_catalog ids"
                )
        unknown_query_fields = sorted(set(self.retrieval_query_fields) - parameter_fields)
        if unknown_query_fields:
            raise ValueError(
                f"{self.name} retrieval_query_fields are absent from parameters: "
                + ", ".join(unknown_query_fields)
            )
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

    def model_args_model(self) -> type[BaseModel]:
        """Return a validation model containing only model-authored fields.

        ``args_model`` is the complete execution model and may contain fields
        owned by the server, such as ``confirmed``.  LangChain uses the model
        passed as ``args_schema`` to generate the provider-facing tool schema,
        so passing ``args_model`` directly would leak those fields back to the
        model.  Copy the original Pydantic fields instead of rebuilding from
        JSON schema so custom field metadata and provider-specific schema
        shapes remain unchanged; the execution boundary still validates with
        the complete ``args_model``.
        """
        fields = {
            field_name: (field.annotation, field)
            for field_name, field in self.args_model.model_fields.items()
            if field_name not in set(self.server_controlled_fields)
        }
        validators: dict[str, Any] = {}
        decorators = getattr(self.args_model, "__pydantic_decorators__", None)
        for validator_name, decorator in (getattr(decorators, "field_validators", {}) or {}).items():
            target_fields = tuple(decorator.info.fields)
            if "*" not in target_fields and not set(target_fields).issubset(fields):
                continue
            validator_kwargs: dict[str, Any] = {"mode": decorator.info.mode}
            if decorator.info.check_fields is not None:
                validator_kwargs["check_fields"] = decorator.info.check_fields
            if decorator.info.json_schema_input_type is not PydanticUndefined:
                validator_kwargs["json_schema_input_type"] = decorator.info.json_schema_input_type
            validator_function = getattr(decorator.func, "__func__", decorator.func)
            validators[validator_name] = field_validator(
                *target_fields,
                **validator_kwargs,
            )(validator_function)
        return create_model(
            f"{_model_name(self.name)}ModelArgs",
            __config__=ConfigDict(extra="forbid"),
            __validators__=validators,
            **fields,
        )

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


class TypedToolResult(BaseModel):
    """Closed result base for tools migrated to an exact output contract."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    partial: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_time: str | None = None
    data_time_provenance: DataTimeProvenance = "unavailable"
    data_time_note: str | None = None
    is_stale: bool | None = None
    freshness_unknown: bool = False
    has_data: bool = False
    data_status: str = "empty"
    usable: bool = False
    evidence_eligible: bool = False


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
    *,
    nullable: bool = False,
) -> Any:
    value_type = _schema_type(name, schema)
    if nullable:
        value_type = value_type | type(None)
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
            nullable=field_name not in required,
        )
        default = ... if field_name in required else field_schema.get("default") if "default" in field_schema else None
        fields[field_name] = (field_type, default)
    return create_model(
        _model_name(name),
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
