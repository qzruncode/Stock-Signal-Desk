# -*- coding: utf-8 -*-
"""Two-stage, schema-driven semantic planner for Agent Orchestrator V2."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
import inspect
import json
import logging
import os
import re
import time
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Literal, Mapping
import uuid

from pydantic import BaseModel, ValidationError

from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    AgentStage,
    AgentStageEventV2,
    AssumptionRecord,
    InputReferenceV2,
    IntentOutlineNodeV2,
    IntentOutlineV2,
    Capability,
    OrchestratorV2Error,
    PlanningTraceV2,
    PlannerVerificationV2,
    RepairIssueV2,
    RepairRecordV2,
    ResourceType,
    StageObserver,
    StageStatus,
)
from src.agent.orchestrator_v2.registry import (
    capability_catalog,
    capability_for,
    normalize_capability_intent,
)
from src.agent.task_planner import current_user_request
from src.llm.anthropic_gateway import build_litellm_kwargs


V2_SCHEMA_VERSION = "orchestrator-3.0"
MODEL_PROGRESS_HEARTBEAT_SECONDS = 5.0
logger = logging.getLogger(__name__)


def _runtime_float(name: str, default: float, *, minimum: float) -> float:
    try:
        return max(minimum, float((os.getenv(name) or str(default)).strip()))
    except (TypeError, ValueError):
        return default


def _runtime_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        return min(
            maximum,
            max(minimum, int((os.getenv(name) or str(default)).strip())),
        )
    except (TypeError, ValueError):
        return default


class RawProviderPayloadError(ValueError):
    def __init__(self, payload: str, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.payload = payload


class MissingProviderPayloadError(ValueError):
    """The provider returned neither the requested function nor usable JSON."""


class ExactContractValidationError(ValueError):
    """Program-owned cross-field rules generated from the same capability spec."""

    def __init__(self, issues: tuple[RepairIssueV2, ...]) -> None:
        super().__init__("; ".join(item.message for item in issues))
        self.issues = issues


@dataclass(frozen=True)
class _AvailableArtifact:
    artifact_id: str
    resource_type: ResourceType
    producer_node_id: str


@dataclass(frozen=True)
class PlannedIntentNodeV2:
    outline: IntentOutlineNodeV2
    intent: BaseModel
    execution_parameters: Mapping[str, Any]
    assumptions: tuple[AssumptionRecord, ...]


@dataclass(frozen=True)
class PlannedIntentGraphV2:
    run_id: str
    outline: IntentOutlineV2
    nodes: tuple[PlannedIntentNodeV2, ...]
    trace: PlanningTraceV2


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    candidates = [stripped]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:json)?\s*(\{[\s\S]*?\})\s*```",
            text,
            flags=re.IGNORECASE,
        )
    )
    last_error: json.JSONDecodeError | None = None
    value: Any = None
    for candidate in dict.fromkeys(candidates):
        try:
            value = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            last_error = exc
    else:
        assert last_error is not None
        raise RawProviderPayloadError(text, last_error) from last_error
    if not isinstance(value, dict):
        raise ValueError("provider payload must be a JSON object")
    return value


def _payload_from_response(response: Any, function_name: str) -> dict[str, Any]:
    def field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)

    parse_errors: list[RawProviderPayloadError] = []
    content_candidates: list[str] = []
    for choice in field(response, "choices") or []:
        message = field(choice, "message")
        if message is None:
            continue
        for tool_call in field(message, "tool_calls") or []:
            function = field(tool_call, "function")
            if field(function, "name") != function_name:
                continue
            arguments = field(function, "arguments")
            if isinstance(arguments, dict):
                return arguments
            if arguments:
                try:
                    return _json_object(str(arguments))
                except RawProviderPayloadError as exc:
                    parse_errors.append(exc)
        content = field(message, "content")
        if isinstance(content, str) and content.strip():
            content_candidates.append(content)
        for reasoning_field in ("reasoning_content", "reasoning"):
            reasoning = field(message, reasoning_field)
            if isinstance(reasoning, str) and reasoning.strip():
                content_candidates.append(reasoning)
    for content in content_candidates:
        try:
            return _json_object(content)
        except RawProviderPayloadError as exc:
            parse_errors.append(exc)
    if parse_errors:
        # Keep the original invalid function payload for the targeted repair
        # record while still accepting a valid provider JSON fallback.
        raise parse_errors[0]
    raise MissingProviderPayloadError(
        f"model returned no {function_name} payload"
    )


def _pointer(location: tuple[Any, ...]) -> str:
    if not location:
        return "/"
    escaped = [
        str(item).replace("~", "~0").replace("/", "~1")
        for item in location
    ]
    return "/" + "/".join(escaped)


def _deref_schema(
    value: Mapping[str, Any],
    root: Mapping[str, Any],
) -> Mapping[str, Any]:
    ref = value.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return value
    current: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(current, Mapping):
            return value
        current = current.get(part)
    return current if isinstance(current, Mapping) else value


def _schema_at_location(
    model: type[BaseModel],
    location: tuple[Any, ...],
) -> Mapping[str, Any]:
    root = model.model_json_schema()
    current: Mapping[str, Any] = root
    for part in location:
        current = _deref_schema(current, root)
        branches = current.get("anyOf") or current.get("oneOf")
        if isinstance(branches, list):
            resolved = [
                _deref_schema(branch, root)
                for branch in branches
                if isinstance(branch, Mapping)
            ]
            titled = next((
                branch
                for branch in resolved
                if branch.get("title") == str(part)
            ), None)
            if titled is not None:
                current = titled
                continue
            property_branch = next((
                branch
                for branch in resolved
                if isinstance(branch.get("properties"), Mapping)
                and str(part) in branch["properties"]
            ), None)
            if property_branch is not None:
                current = property_branch
        properties = current.get("properties")
        if isinstance(properties, Mapping) and str(part) in properties:
            child = properties[str(part)]
            if isinstance(child, Mapping):
                current = child
                continue
        if isinstance(part, int) and isinstance(current.get("items"), Mapping):
            current = current["items"]
            continue
    return _deref_schema(current, root)


def _schema_expectation(schema: Mapping[str, Any]) -> tuple[str, tuple[Any, ...]]:
    allowed: tuple[Any, ...] = ()
    if isinstance(schema.get("enum"), list):
        allowed = tuple(schema["enum"])
    elif "const" in schema:
        allowed = (schema["const"],)
    parts: list[str] = []
    expected_type = schema.get("type")
    if expected_type is not None:
        parts.append(str(expected_type))
    if allowed:
        parts.append("allowed=" + json.dumps(allowed, ensure_ascii=False))
    for key in ("minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum"):
        if key in schema:
            parts.append(f"{key}={schema[key]}")
    return "; ".join(parts) or "schema", allowed


def _repair_issues(
    exc: BaseException,
    model: type[BaseModel],
) -> tuple[RepairIssueV2, ...]:
    if isinstance(exc, ExactContractValidationError):
        return exc.issues
    if isinstance(exc, ValidationError):
        issues: list[RepairIssueV2] = []
        for error in exc.errors(include_url=False):
            context = error.get("ctx") or {}
            location = tuple(error.get("loc") or ())
            schema_expected, schema_allowed = _schema_expectation(
                _schema_at_location(model, location)
            )
            expected = str(
                context.get("expected")
                or context.get("class_name")
                or schema_expected
                or error.get("type")
            )
            issues.append(RepairIssueV2(
                pointer=_pointer(location),
                code=str(error.get("type") or "validation_error"),
                expected=expected,
                allowed=schema_allowed,
                message=str(error.get("msg") or "invalid value"),
            ))
        return tuple(issues)
    return (
        RepairIssueV2(
            pointer="/",
            code=type(exc).__name__,
            expected="valid JSON matching the supplied schema",
            message=str(exc),
        ),
    )


def _function_tool(
    name: str,
    description: str,
    model: type[BaseModel],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "strict": True,
            "parameters": model.model_json_schema(),
        },
    }


def _exact_contract_messages(
    *,
    function_name: str,
    system_prompt: str,
    request_context: Mapping[str, Any],
    model: type[BaseModel],
    json_content_transport: bool,
) -> list[dict[str, str]]:
    context = dict(request_context)
    prompt = system_prompt
    if json_content_transport:
        is_targeted_repair = "targeted_repair" in context
        context["output_contract_name"] = function_name
        context["exact_output_schema"] = model.model_json_schema()
        context["output_transport"] = {
            "type": "json_content",
            "instruction": (
                "最终回答只能是一个完整 JSON 对象，不得包含解释、Markdown、"
                "代码围栏或 JSON 之外的文字。"
            ),
        }
        prompt = (
            system_prompt
            + (
                "\n\n当前严格函数参数通道未返回可解析 JSON，现改用 JSON 内容"
                "通道完成同一次定点修复。"
                if is_targeted_repair
                else "\n\n本能力按程序策略使用 JSON 内容通道提交精确契约。"
            )
            + "分析可放在供应商独立 reasoning 字段；"
            "最终 content 只能包含与 exact_output_schema 完全一致的完整 JSON 对象。"
        )
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(
                context,
                ensure_ascii=False,
                default=str,
            ),
        },
    ]


async def _emit(
    observer: StageObserver | None,
    *,
    run_id: str,
    stage: AgentStage,
    status: StageStatus,
    task_id: str | None = None,
    error_code: AgentErrorCode | None = None,
    summary: str = "",
) -> None:
    if observer is None:
        return
    result = observer(AgentStageEventV2(
        run_id=run_id,
        stage=stage,
        status=status,
        task_id=task_id,
        error_code=error_code,
        summary=summary,
    ))
    if inspect.isawaitable(result):
        await result


async def call_model_exact_v2(
    *,
    llm_cfg: Mapping[str, Any],
    completion: Callable[..., Awaitable[Any]],
    function_name: str,
    description: str,
    model: type[BaseModel],
    system_prompt: str,
    semantic_context: Mapping[str, Any],
    node_id: str | None,
    value_validator: Callable[[BaseModel], None] | None = None,
    payload_normalizer: (
        Callable[[dict[str, Any]], dict[str, Any]] | None
    ) = None,
    progress_observer: Callable[[int], Awaitable[None] | None] | None = None,
    provider_error_code: AgentErrorCode = AgentErrorCode.PLANNER_TIMEOUT,
    schema_error_code: AgentErrorCode = AgentErrorCode.PLANNER_SCHEMA_INVALID,
    max_tokens: int = 4_000,
    contract_transport: Literal["function", "json_content"] = "function",
    timeout_seconds: float | None = None,
) -> tuple[BaseModel, Any, RepairRecordV2 | None]:
    request_timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else _runtime_float(
            "AGENT_PLANNER_REQUEST_TIMEOUT_SECONDS",
            90.0,
            minimum=1.0,
        )
    )
    tool = _function_tool(function_name, description, model)
    first_payload: Any = None
    first_error: BaseException | None = None
    for attempt in range(2):
        request_context = dict(semantic_context)
        json_content_transport = (
            contract_transport == "json_content"
            or (
                attempt == 1
                and isinstance(
                    first_error,
                    (
                        RawProviderPayloadError,
                        MissingProviderPayloadError,
                    ),
                )
            )
        )
        if attempt == 1:
            assert first_error is not None
            issues = _repair_issues(first_error, model)
            request_context["targeted_repair"] = {
                "invalid_payload": first_payload,
                "issues": [item.model_dump(mode="json") for item in issues],
                "instruction": (
                    "只修正列出的字段路径，保持其余字段和用户目标不变；"
                    + (
                        "通过最终 content 返回完整 JSON 对象。"
                        if json_content_transport
                        else "返回同一函数的完整参数对象。"
                    )
                ),
            }
        structured_kwargs: dict[str, Any] = {
            "messages": _exact_contract_messages(
                function_name=function_name,
                system_prompt=system_prompt,
                request_context=request_context,
                model=model,
                json_content_transport=json_content_transport,
            ),
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if not json_content_transport:
            structured_kwargs.update({
                "tools": [tool],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": function_name},
                },
            })
        kwargs = build_litellm_kwargs(
            dict(llm_cfg),
            stream=False,
            **structured_kwargs,
        )
        raw_payload: Any = None
        response: Any = None
        completion_task: asyncio.Task | None = None
        try:
            # GuardedModelRuntime is the sole transport-retry owner. This
            # contract layer performs one request plus, when needed, one
            # schema-repair request with a different semantic purpose.
            completion_task = asyncio.create_task(completion(**kwargs))
            wait_started = time.monotonic()
            deadline = wait_started + request_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"{function_name} exceeded {request_timeout:.1f}s"
                    )
                done, _ = await asyncio.wait(
                    {completion_task},
                    timeout=min(
                        MODEL_PROGRESS_HEARTBEAT_SECONDS,
                        remaining,
                    ),
                )
                if done:
                    response = completion_task.result()
                    break
                if progress_observer is not None:
                    progress_result = progress_observer(
                        max(1, int(time.monotonic() - wait_started))
                    )
                    if inspect.isawaitable(progress_result):
                        await progress_result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise OrchestratorV2Error(
                provider_error_code,
                (
                    f"{function_name} provider request failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                task_id=node_id,
                metadata={
                    "provider_attempts": 1,
                    "timeout_seconds": request_timeout,
                },
            ) from exc
        finally:
            if completion_task is not None and not completion_task.done():
                completion_task.cancel()
                await asyncio.gather(
                    completion_task,
                    return_exceptions=True,
                )

        try:
            raw_payload = _payload_from_response(response, function_name)
            payload = (
                payload_normalizer(raw_payload)
                if payload_normalizer is not None
                else raw_payload
            )
            validated = model.model_validate(payload)
            if value_validator is not None:
                value_validator(validated)
            repair = (
                RepairRecordV2(
                    node_id=node_id,
                    function_name=function_name,
                    invalid_payload=first_payload,
                    issues=_repair_issues(first_error, model),
                    succeeded=True,
                )
                if attempt == 1 and first_error is not None
                else None
            )
            return validated, raw_payload, repair
        except asyncio.CancelledError:
            raise
        except OrchestratorV2Error:
            raise
        except Exception as exc:
            if attempt == 0:
                first_payload = (
                    exc.payload
                    if isinstance(exc, RawProviderPayloadError)
                    else raw_payload
                )
                first_error = exc
                continue
            raise OrchestratorV2Error(
                schema_error_code,
                (
                    f"{function_name} remained invalid after one targeted repair: "
                    f"{exc}"
                ),
                task_id=node_id,
                metadata={
                    "repair": RepairRecordV2(
                        node_id=node_id,
                        function_name=function_name,
                        invalid_payload=first_payload,
                        issues=_repair_issues(first_error, model),
                        succeeded=False,
                    ).model_dump(mode="json"),
                },
            ) from exc
    raise AssertionError("unreachable")


def _validate_outline_capability_contracts(value: BaseModel) -> None:
    outline = IntentOutlineV2.model_validate(value)
    issues: list[RepairIssueV2] = []
    for index, node in enumerate(outline.nodes):
        spec = capability_for(node.capability)
        if (
            node.result_selection is not None
            and not spec.supports_result_selection
        ):
            issues.append(RepairIssueV2(
                pointer=f"/nodes/{index}/result_selection",
                code="capability_result_selection_forbidden",
                expected=(
                    "null because capability "
                    f"{node.capability.value} does not support result selection"
                ),
                allowed=(None,),
                message=(
                    f"{node.capability.value} does not support result_selection"
                ),
            ))
    if issues:
        raise ExactContractValidationError(tuple(issues))


def _collapse_subsumed_capabilities(
    outline: IntentOutlineV2,
) -> tuple[IntentOutlineV2, int]:
    """Collapse redundant siblings into their program-owned composite Workflow."""
    selected_composites = {
        node.capability
        for node in outline.nodes
        if capability_for(node.capability).subsumes_capabilities
    }
    if not selected_composites:
        return outline, 0
    by_id = {node.node_id: node for node in outline.nodes}
    removable = {
        node.node_id
        for node in outline.nodes
        if any(
            node.capability
            in capability_for(composite).subsumes_capabilities
            for composite in selected_composites
        )
    }
    for candidate_id in tuple(removable):
        candidate = by_id[candidate_id]
        for consumer in outline.nodes:
            if consumer.node_id in removable:
                continue
            references_candidate = any(
                ref.source == "node" and ref.node_id == candidate_id
                for ref in consumer.input_refs
            )
            if not references_candidate:
                continue
            consumer_spec = capability_for(consumer.capability)
            consumer_owns_candidate = (
                candidate.capability
                in consumer_spec.subsumes_capabilities
                and consumer_spec.allow_direct_entities
            )
            if not consumer_owns_candidate:
                removable.discard(candidate_id)
                break
    if not removable:
        return outline, 0
    nodes = [
        node.model_copy(update={
            "input_refs": tuple(
                ref
                for ref in node.input_refs
                if not (
                    ref.source == "node"
                    and ref.node_id in removable
                )
            ),
        })
        for node in outline.nodes
        if node.node_id not in removable
    ]
    collapsed = IntentOutlineV2.model_validate({
        **outline.model_dump(mode="python"),
        "nodes": tuple(nodes),
    })
    return collapsed, len(removable)


def _available_artifacts(
    semantic_context: Mapping[str, Any] | None,
) -> tuple[_AvailableArtifact, ...]:
    result: dict[str, _AvailableArtifact] = {}
    if not isinstance(semantic_context, Mapping):
        return ()
    for turn in semantic_context.get("turns") or []:
        if not isinstance(turn, Mapping):
            continue
        for artifact in turn.get("terminal_artifacts") or []:
            if not isinstance(artifact, Mapping):
                continue
            artifact_id = str(artifact.get("artifact_id") or "").strip()
            try:
                resource_type = ResourceType(artifact.get("resource_type"))
            except (TypeError, ValueError):
                continue
            if artifact_id:
                result[artifact_id] = _AvailableArtifact(
                    artifact_id=artifact_id,
                    resource_type=resource_type,
                    producer_node_id=str(
                        artifact.get("producer_node_id") or ""
                    ).strip(),
                )
    return tuple(result.values())


def _normalize_outline_resource_refs(
    payload: dict[str, Any],
    *,
    available_artifacts: tuple[_AvailableArtifact, ...],
) -> dict[str, Any]:
    """Resolve model-visible resource relations to program-owned artifact IDs.

    A provider sometimes copies ``producer_node_id`` from a previous turn and
    labels it as a node in the current graph. Historical nodes are not graph
    dependencies. Resolve that semantic producer reference only when it names
    one unique compatible artifact (or when there is only one compatible
    artifact at all). Never guess among multiple resources.
    """
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, (list, tuple)):
        return payload
    available_by_id = {
        artifact.artifact_id: artifact
        for artifact in available_artifacts
    }

    # Providers occasionally insert an identity bridge between a historical
    # resource and its real consumer: a node receives resource R even though
    # its capability cannot consume R, produces only R, and the next node
    # consumes R from that bridge. The bridge cannot execute as described and
    # used to survive after its invalid input edge was dropped, which caused a
    # fresh workflow and a second semantic binding pass. Collapse this
    # structurally invalid identity bridge back to the immutable artifact.
    bridge_artifacts: dict[str, tuple[str, ResourceType]] = {}
    raw_nodes_by_id = {
        str(node.get("node_id") or "").strip(): node
        for node in raw_nodes
        if isinstance(node, Mapping)
        and str(node.get("node_id") or "").strip()
    }
    for node_id, raw_node in raw_nodes_by_id.items():
        try:
            spec = capability_for(raw_node.get("capability"))
        except (KeyError, TypeError, ValueError):
            continue
        refs = raw_node.get("input_refs")
        if not isinstance(refs, (list, tuple)) or len(refs) != 1:
            continue
        raw_ref = refs[0]
        if not isinstance(raw_ref, Mapping):
            continue
        try:
            resource_type = ResourceType(raw_ref.get("resource_type"))
        except (TypeError, ValueError):
            continue
        artifact_id = str(raw_ref.get("artifact_id") or "").strip()
        artifact = available_by_id.get(artifact_id)
        if (
            str(raw_ref.get("source") or "").strip() != "artifact"
            or artifact is None
            or artifact.resource_type != resource_type
            or resource_type in spec.input_resources
            or spec.output_resources != frozenset({resource_type})
        ):
            continue
        consumer_refs = [
            ref
            for candidate_id, candidate in raw_nodes_by_id.items()
            if candidate_id != node_id
            for ref in candidate.get("input_refs") or ()
            if isinstance(ref, Mapping)
            and str(ref.get("source") or "node").strip() == "node"
            and str(ref.get("node_id") or "").strip() == node_id
        ]
        if not consumer_refs:
            continue
        if any(
            ref.get("resource_type") != resource_type.value
            for ref in consumer_refs
        ):
            continue
        bridge_artifacts[node_id] = (artifact_id, resource_type)

    if bridge_artifacts:
        collapsed_nodes: list[Any] = []
        for raw_node in raw_nodes:
            if not isinstance(raw_node, Mapping):
                collapsed_nodes.append(raw_node)
                continue
            node_id = str(raw_node.get("node_id") or "").strip()
            if node_id in bridge_artifacts:
                continue
            node = dict(raw_node)
            refs: list[Any] = []
            for raw_ref in node.get("input_refs") or ():
                if not isinstance(raw_ref, Mapping):
                    refs.append(raw_ref)
                    continue
                bridge = bridge_artifacts.get(
                    str(raw_ref.get("node_id") or "").strip()
                )
                if (
                    str(raw_ref.get("source") or "node").strip() == "node"
                    and bridge is not None
                    and raw_ref.get("resource_type") == bridge[1].value
                ):
                    refs.append({
                        **raw_ref,
                        "source": "artifact",
                        "node_id": None,
                        "artifact_id": bridge[0],
                        "resource_type": bridge[1].value,
                    })
                else:
                    refs.append(raw_ref)
            node["input_refs"] = refs
            collapsed_nodes.append(node)
        raw_nodes = collapsed_nodes

    current_node_ids = {
        str(node.get("node_id") or "").strip()
        for node in raw_nodes
        if isinstance(node, Mapping)
        and str(node.get("node_id") or "").strip()
    }
    normalized_nodes: list[Any] = []
    issues: list[RepairIssueV2] = []
    for node_index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, Mapping):
            normalized_nodes.append(raw_node)
            continue
        node = dict(raw_node)
        raw_refs = node.get("input_refs")
        if not isinstance(raw_refs, (list, tuple)):
            normalized_nodes.append(node)
            continue
        try:
            capability_spec = capability_for(node.get("capability"))
        except (KeyError, TypeError, ValueError):
            capability_spec = None
        normalized_refs: list[Any] = []
        for ref_index, raw_ref in enumerate(raw_refs):
            if not isinstance(raw_ref, Mapping):
                normalized_refs.append(raw_ref)
                continue
            ref = dict(raw_ref)
            try:
                resource_type = ResourceType(ref.get("resource_type"))
            except (TypeError, ValueError):
                normalized_refs.append(ref)
                continue
            # Resource edges are program-owned. A model can describe a
            # semantically related but type-impossible extra edge (for
            # example evidence_collection on a direct-entity investment
            # decision). Such an edge can never be consumed by the selected
            # capability, so remove it deterministically instead of letting an
            # irrelevant model field abort the whole run.
            if (
                capability_spec is not None
                and resource_type not in capability_spec.input_resources
            ):
                continue
            source = str(ref.get("source") or "node").strip()
            node_id = str(ref.get("node_id") or "").strip()
            if (
                source != "node"
                or not node_id
                or node_id in current_node_ids
            ):
                normalized_refs.append(ref)
                continue
            compatible = tuple(
                artifact
                for artifact in available_artifacts
                if artifact.resource_type == resource_type
            )
            producer_matches = tuple(
                artifact
                for artifact in compatible
                if artifact.producer_node_id == node_id
            )
            candidates = (
                producer_matches
                if producer_matches
                else compatible
            )
            if len(candidates) == 1:
                artifact = candidates[0]
                normalized_refs.append({
                    **ref,
                    "source": "artifact",
                    "node_id": None,
                    "artifact_id": artifact.artifact_id,
                    "resource_type": resource_type.value,
                })
                continue
            if candidates:
                raise OrchestratorV2Error(
                    AgentErrorCode.CLARIFICATION_REQUIRED,
                    (
                        f"找到多个可用的 {resource_type.value} 历史结果，"
                        "请说明要使用哪一轮或哪一次筛选结果。"
                    ),
                    task_id=str(node.get("node_id") or "") or None,
                )
            issues.append(RepairIssueV2(
                pointer=f"/nodes/{node_index}/input_refs/{ref_index}",
                code="unknown_node_reference",
                expected=(
                    "a current-graph node_id or one uniquely resolvable "
                    "historical artifact"
                ),
                message=(
                    f"{node_id!r} is not a node in this graph and "
                    f"resolved to {len(candidates)} compatible artifacts"
                ),
            ))
            normalized_refs.append(ref)
        node["input_refs"] = normalized_refs
        normalized_nodes.append(node)
    if issues:
        raise ExactContractValidationError(tuple(issues))
    return {
        **payload,
        "nodes": normalized_nodes,
    }


def _bind_outline_resources(
    outline: IntentOutlineV2,
    *,
    available_artifacts: Mapping[str, ResourceType],
    has_direct_entities: bool = False,
) -> IntentOutlineV2:
    """Validate explicit edges and deterministically add unambiguous bindings."""
    nodes = list(outline.nodes)
    by_id = {node.node_id: node for node in nodes}
    updated: list[IntentOutlineNodeV2] = []
    for node in nodes:
        spec = capability_for(node.capability)
        refs = list(node.input_refs)
        for ref in refs:
            if ref.resource_type not in spec.input_resources:
                raise OrchestratorV2Error(
                    AgentErrorCode.PLANNER_SCHEMA_INVALID,
                    (
                        f"{node.node_id} does not accept "
                        f"{ref.resource_type.value}"
                    ),
                    task_id=node.node_id,
                )
            if ref.source == "node":
                assert ref.node_id is not None
                producer = by_id[ref.node_id]
                producer_spec = capability_for(producer.capability)
                if ref.resource_type not in producer_spec.output_resources:
                    raise OrchestratorV2Error(
                        AgentErrorCode.PLANNER_SCHEMA_INVALID,
                        (
                            f"{ref.node_id} does not produce "
                            f"{ref.resource_type.value}"
                        ),
                        task_id=node.node_id,
                    )
            else:
                assert ref.artifact_id is not None
                actual = available_artifacts.get(ref.artifact_id)
                if actual != ref.resource_type:
                    raise OrchestratorV2Error(
                        AgentErrorCode.RESOURCE_UNAVAILABLE,
                        (
                            f"artifact {ref.artifact_id} is missing or does not "
                            f"contain {ref.resource_type.value}"
                        ),
                        task_id=node.node_id,
                    )
        bound_types = {ref.resource_type for ref in refs}
        for required in spec.input_resources - bound_types:
            if spec.allow_direct_entities and has_direct_entities:
                continue
            node_producers = [
                candidate
                for candidate in nodes
                if candidate.node_id != node.node_id
                and required in capability_for(candidate.capability).output_resources
            ]
            artifact_producers = [
                artifact_id
                for artifact_id, resource_type in available_artifacts.items()
                if resource_type == required
            ]
            if len(node_producers) + len(artifact_producers) == 1:
                if node_producers:
                    refs.append(InputReferenceV2(
                        source="node",
                        node_id=node_producers[0].node_id,
                        resource_type=required,
                    ))
                else:
                    refs.append(InputReferenceV2(
                        source="artifact",
                        artifact_id=artifact_producers[0],
                        resource_type=required,
                    ))
                continue
            if not node_producers and not artifact_producers and spec.allow_direct_entities:
                continue
            if len(node_producers) + len(artifact_producers) > 1:
                raise OrchestratorV2Error(
                    AgentErrorCode.CLARIFICATION_REQUIRED,
                    (
                        f"{node.node_id} 有多个可用的 {required.value} 来源，"
                        "请明确要使用哪一个集合。"
                    ),
                    task_id=node.node_id,
                )
            raise OrchestratorV2Error(
                AgentErrorCode.RESOURCE_UNAVAILABLE,
                (
                    f"{node.node_id} 缺少 {required.value}，"
                    "不会改用新闻或公网搜索生成替代集合。"
                ),
                task_id=node.node_id,
            )
        updated.append(node.model_copy(update={"input_refs": tuple(refs)}))
    # ``model_copy`` does not rerun graph validators. Re-validate the complete
    # outline after deterministic edges are added so automatic binding cannot
    # turn a valid provider graph into a cyclic execution graph.
    try:
        return IntentOutlineV2.model_validate({
            **outline.model_dump(mode="python"),
            "nodes": tuple(updated),
        })
    except ValidationError as exc:
        raise OrchestratorV2Error(
            AgentErrorCode.PLANNER_SCHEMA_INVALID,
            f"resource-bound intent graph is invalid: {exc}",
        ) from exc


_OUTLINE_SYSTEM_PROMPT = """\
你是 Agent Orchestrator V2 的语义拆解器。你只表达用户目标、能力和资源关系。
禁止输出任何工具名、批次、并发、超时、重试、缓存、抓取深度、搜索条数、内部字段或默认值。
source=node 的 input_refs 只能引用本张图中的上游 node_id 和它真实产生的资源类型。
引用 conversation_context 中的跨轮终态资源时必须使用 source=artifact 和对应 artifact_id；
禁止把历史 producer_node_id 伪装成本张图节点。若用户目标不明确，返回最小澄清。
不要把数据源缺失改写成公开新闻或网络搜索任务。不要把一组联合财务条件拆成多个筛选节点。
只保留完成当前用户明确请求所必需的最小能力图；不得添加为潜在追问准备的下游任务。
每个 objective 只能复述用户已经表达的目标，不得擅自增加细分领域、条件、动作或输出类型。
不得把某个能力的输出继续消费成用户没有明确要求的另一类结果。
最小能力图仍必须足以产生用户本轮要求的终态结论，不能只完成前置候选发现。
theme_stock_discovery 只建立结构化板块成分股候选全集；候选成员关系不等于公司业务匹配、
受益程度、投入强度或发展状态。若用户本轮要求任何公司级事实判断，必须继续选择能产生
该判断的终态能力。对领域候选逐家公司核验主题业务与发展强度时，theme_business_evidence
必须同时消费上游 theme_stock_discovery 产生的 domain_collection 和 security_collection；
不得把当前消息中偶然解析出的证券实体当成上游候选集合。
capability_catalog 中 subsumes_capabilities 非空的能力是自包含复合能力；选择它时，不得再添加
其中列出的并列能力节点，复合能力自己的固定 Workflow 会按顺序完成全部证据与判断。
result_selection 只能用于 capability_catalog 中 supports_result_selection=true 的能力，否则必须为 null。
只有用户明确要求唯一一个、明确数量或“全部相关”时才填写 result_selection；用户没有表达数量时必须
返回 null，由程序采用能力自己的默认值并记录假设。
"""


_INTENT_SYSTEM_PROMPT = """\
你是 Agent Orchestrator V2 的单能力参数化器。任务图与能力已经冻结。
只能按本次提供的唯一精确 Schema 填写用户明确表达的业务语义，不得增加任务、工具名或执行参数。
没有由用户表达的可选字段保持缺省；程序会负责默认值并记录假设。
主题、对象和研究主体字段只能填写用户明确说出的具体主体；分析目标留在 frozen_node.objective。
禁止把“最受益、比较、分析”等目标扩展成用户没有点名的细分行业、零部件、应用场景或结论。
财务指标必须区分资产负债率、营收、归母净利润和扣非净利润，金额保留用户表达的单位。
"""

_VERIFIER_SYSTEM_PROMPT = """\
你是独立的 Agent 计划验收器。只比较 current_request 与 frozen_outline：
1. 图必须覆盖用户本轮明确要求的每一种终态能力，不能只做前置发现；
2. 不得包含用户没有要求的能力；
3. 每条资源边必须让下游消费上游真实的结构化结果；
4. 复合能力已经包含的子能力不能重复出现；
5. 不评价工具、参数、实现方式或答案内容。
严格输出验收 Schema。没有问题时 accepted=true 且所有问题数组为空；
存在任何遗漏、越界或资源错误时 accepted=false。不得替计划辩护。\
"""


def _planner_verifier_mode() -> str:
    configured = (
        os.getenv("AGENT_PLANNER_VERIFIER_MODE") or ""
    ).strip().lower()
    if configured in {"off", "shadow", "enforce"}:
        return configured
    environment = str(
        os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or ""
    ).strip().lower()
    return "enforce" if environment in {"prod", "production"} else "off"


async def plan_intent_graph_v2(
    messages: list[dict[str, Any]],
    llm_cfg: Mapping[str, Any],
    *,
    completion: Callable[..., Awaitable[Any]],
    semantic_context: Mapping[str, Any] | None = None,
    stage_observer: StageObserver | None = None,
    run_id: str | None = None,
    today: date | None = None,
    current_entities: list[dict[str, str]] | None = None,
) -> PlannedIntentGraphV2:
    """Produce a frozen V2 graph under bounded provider and total deadlines."""
    active_run_id = run_id or uuid.uuid4().hex
    request = current_user_request(messages)
    if not request:
        raise ValueError("conversation has no user text")
    now = today or date.today()
    repairs: list[RepairRecordV2] = []
    durations: dict[str, int] = {}
    started = time.monotonic()
    raw_outline: Any = None
    raw_intents: dict[str, Any] = {}
    verification: PlannerVerificationV2 | None = None
    available_artifacts = _available_artifacts(semantic_context)

    async def execute() -> PlannedIntentGraphV2:
        nonlocal raw_outline, verification
        stage_started = time.monotonic()
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.OUTLINE,
            status=StageStatus.STARTED,
            summary="正在识别能力与资源关系",
        )
        outline_value, raw_outline, repair = await call_model_exact_v2(
            llm_cfg=llm_cfg,
            completion=completion,
            function_name="submit_intent_outline_v2",
            description="Submit only the capability graph and resource references.",
            model=IntentOutlineV2,
            system_prompt=_OUTLINE_SYSTEM_PROMPT,
            semantic_context={
                "current_request": request,
                "conversation_context": dict(semantic_context or {}),
                "capability_catalog": capability_catalog(),
                "runtime_date": now.isoformat(),
            },
            node_id=None,
            value_validator=_validate_outline_capability_contracts,
            payload_normalizer=lambda payload: _normalize_outline_resource_refs(
                payload,
                available_artifacts=available_artifacts,
            ),
            progress_observer=lambda elapsed: _emit(
                stage_observer,
                run_id=active_run_id,
                stage=AgentStage.OUTLINE,
                status=StageStatus.STARTED,
                summary=(
                    "模型正在识别能力与资源关系，"
                    f"已持续分析 {elapsed} 秒"
                ),
            ),
        )
        if repair is not None:
            repairs.append(repair)
        outline = IntentOutlineV2.model_validate(outline_value)
        if outline.needs_clarification:
            raise OrchestratorV2Error(
                AgentErrorCode.CLARIFICATION_REQUIRED,
                outline.clarification_question or "需要补充任务目标。",
            )
        outline, collapsed_count = _collapse_subsumed_capabilities(outline)
        outline = _bind_outline_resources(
            outline,
            available_artifacts={
                artifact.artifact_id: artifact.resource_type
                for artifact in available_artifacts
            },
            has_direct_entities=bool(current_entities),
        )
        verifier_mode = _planner_verifier_mode()
        if verifier_mode != "off":
            verification_value, _raw_verification, _repair = (
                await call_model_exact_v2(
                    llm_cfg=llm_cfg,
                    completion=completion,
                    function_name="verify_intent_outline_v2",
                    description=(
                        "Independently verify semantic coverage, minimality, "
                        "and resource edges of the frozen intent graph."
                    ),
                    model=PlannerVerificationV2,
                    system_prompt=_VERIFIER_SYSTEM_PROMPT,
                    semantic_context={
                        "current_request": request,
                        "frozen_outline": outline.model_dump(mode="json"),
                        "capability_catalog": capability_catalog(),
                        "conversation_context": dict(semantic_context or {}),
                    },
                    node_id=None,
                    max_tokens=1_500,
                )
            )
            verification = PlannerVerificationV2.model_validate(
                verification_value
            )
            minimum_confidence = _runtime_float(
                "AGENT_PLANNER_VERIFIER_MIN_CONFIDENCE",
                0.8,
                minimum=0.0,
            )
            rejected = (
                not verification.accepted
                or verification.confidence < minimum_confidence
            )
            if rejected and verifier_mode == "enforce":
                raise OrchestratorV2Error(
                    AgentErrorCode.PLANNER_SCHEMA_INVALID,
                    "independent semantic verifier rejected the capability graph",
                    metadata={
                        "verification": verification.model_dump(mode="json"),
                        "minimum_confidence": minimum_confidence,
                    },
                )
            if rejected:
                logger.warning(
                    "[AgentPlanner] shadow verifier rejected run=%s: %s",
                    active_run_id,
                    verification.model_dump(mode="json"),
                )
        durations[AgentStage.OUTLINE.value] = int(
            (time.monotonic() - stage_started) * 1000
        )
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.OUTLINE,
            status=StageStatus.SUCCEEDED,
            summary=(
                f"已冻结 {len(outline.nodes)} 个能力节点"
                + (
                    f"，并入复合 Workflow {collapsed_count} 个重复节点"
                    if collapsed_count
                    else ""
                )
            ),
        )

        parameter_started = time.monotonic()
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.PARAMETERIZATION,
            status=StageStatus.STARTED,
            summary="正在按节点填充精确业务 Schema",
        )

        parameter_semaphore = asyncio.Semaphore(_runtime_int(
            "AGENT_PLANNER_PARAMETER_CONCURRENCY",
            4,
            minimum=1,
            maximum=12,
        ))

        async def parameterize(
            node: IntentOutlineNodeV2,
        ) -> tuple[IntentOutlineNodeV2, BaseModel, Any, RepairRecordV2 | None]:
            spec = capability_for(node.capability)
            if not spec.intent_model.model_fields:
                empty = spec.intent_model.model_validate({})
                return node, empty, {}, None
            async with parameter_semaphore:
                value, raw, node_repair = await call_model_exact_v2(
                    llm_cfg=llm_cfg,
                    completion=completion,
                    function_name=f"submit_{node.capability.value}_intent_v2",
                    description=f"Submit semantic intent for {spec.title}.",
                    model=spec.intent_model,
                    system_prompt=_INTENT_SYSTEM_PROMPT,
                    semantic_context={
                        "current_request": request,
                        "frozen_node": node.model_dump(mode="json"),
                        "upstream_resources": [
                            ref.model_dump(mode="json") for ref in node.input_refs
                        ],
                        "conversation_context": dict(semantic_context or {}),
                        "runtime_date": now.isoformat(),
                    },
                    node_id=node.node_id,
                    progress_observer=lambda elapsed: _emit(
                        stage_observer,
                        run_id=active_run_id,
                        stage=AgentStage.PARAMETERIZATION,
                        status=StageStatus.STARTED,
                        summary=(
                            f"模型正在填写“{spec.title}”业务 Schema，"
                            f"已持续分析 {elapsed} 秒"
                        ),
                    ),
                )
            return node, value, raw, node_repair

        parameterized = await asyncio.gather(*(
            parameterize(node) for node in outline.nodes
        ))
        for node, _, raw, repair in parameterized:
            raw_intents[node.node_id] = raw
            if repair is not None:
                repairs.append(repair)
        durations[AgentStage.PARAMETERIZATION.value] = int(
            (time.monotonic() - parameter_started) * 1000
        )
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.PARAMETERIZATION,
            status=StageStatus.SUCCEEDED,
            summary="所有能力参数已通过本地同源模型校验",
        )

        normalize_started = time.monotonic()
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.NORMALIZATION,
            status=StageStatus.STARTED,
            summary="正在应用程序默认值并记录假设",
        )
        planned_nodes: list[PlannedIntentNodeV2] = []
        all_assumptions: list[AssumptionRecord] = []
        normalized_intents: dict[str, Any] = {}
        for node, intent, _, _ in parameterized:
            normalized = normalize_capability_intent(
                node_id=node.node_id,
                objective=node.objective,
                capability=node.capability,
                intent=intent,
                input_refs=node.input_refs,
                result_selection=node.result_selection,
                current_year=now.year,
            )
            all_assumptions.extend(normalized.assumptions)
            normalized_intents[node.node_id] = {
                "semantic_intent": normalized.intent.model_dump(mode="json"),
                "execution_parameters": dict(normalized.execution_parameters),
            }
            planned_nodes.append(PlannedIntentNodeV2(
                outline=node,
                intent=normalized.intent,
                execution_parameters=MappingProxyType(
                    dict(normalized.execution_parameters)
                ),
                assumptions=normalized.assumptions,
            ))
        durations[AgentStage.NORMALIZATION.value] = int(
            (time.monotonic() - normalize_started) * 1000
        )
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=AgentStage.NORMALIZATION,
            status=StageStatus.SUCCEEDED,
            summary=f"记录了 {len(all_assumptions)} 项程序假设",
        )
        durations["total"] = int((time.monotonic() - started) * 1000)
        trace = PlanningTraceV2(
            run_id=active_run_id,
            schema_version=V2_SCHEMA_VERSION,
            raw_outline=raw_outline,
            normalized_outline=outline.model_dump(mode="json"),
            raw_intents=raw_intents,
            normalized_intents=normalized_intents,
            assumptions=tuple(all_assumptions),
            repairs=tuple(repairs),
            verification=(
                verification.model_dump(mode="json")
                if verification is not None
                else None
            ),
            stage_durations_ms=durations,
        )
        return PlannedIntentGraphV2(
            run_id=active_run_id,
            outline=outline,
            nodes=tuple(planned_nodes),
            trace=trace,
        )

    try:
        total_timeout = _runtime_float(
            "AGENT_PLANNER_TOTAL_TIMEOUT_SECONDS",
            240.0,
            minimum=5.0,
        )
        async with asyncio.timeout(total_timeout):
            graph = await execute()
    except TimeoutError as exc:
        timeout_error = OrchestratorV2Error(
            AgentErrorCode.PLANNER_TIMEOUT,
            "Agent planner exceeded its total deadline",
            metadata={"timeout_seconds": total_timeout},
        )
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=(
                AgentStage.OUTLINE
                if raw_outline is None
                else AgentStage.PARAMETERIZATION
            ),
            status=StageStatus.FAILED,
            error_code=AgentErrorCode.PLANNER_TIMEOUT,
            summary=str(timeout_error),
        )
        raise timeout_error from exc
    except OrchestratorV2Error as exc:
        await _emit(
            stage_observer,
            run_id=active_run_id,
            stage=(
                AgentStage.OUTLINE
                if raw_outline is None
                else AgentStage.PARAMETERIZATION
            ),
            status=StageStatus.BLOCKED
            if exc.code in {
                AgentErrorCode.CLARIFICATION_REQUIRED,
                AgentErrorCode.RESOURCE_UNAVAILABLE,
            }
            else StageStatus.FAILED,
            task_id=exc.task_id,
            error_code=exc.code,
            summary=(
                "任务图未通过内部强类型契约校验；"
                "本轮没有调用任何数据工具"
                if exc.code == AgentErrorCode.PLANNER_SCHEMA_INVALID
                else str(exc)
            ),
        )
        raise
    return graph


__all__ = [
    "ExactContractValidationError",
    "PlannedIntentGraphV2",
    "PlannedIntentNodeV2",
    "call_model_exact_v2",
    "plan_intent_graph_v2",
]
