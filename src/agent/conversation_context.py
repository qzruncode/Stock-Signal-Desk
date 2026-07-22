# -*- coding: utf-8 -*-
"""Structured cross-turn context for the standard-task agent.

The planner must not recover execution state by parsing the assistant's prose.
This module stores the task contracts, verified securities and bounded result
artifacts produced by completed turns.  The state is intentionally tool-name
free, so it can be shown to the semantic planner without exposing a data-tool
selection surface.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.agent.task_executor import PlanExecutionResult, action_fingerprint
from src.agent.result_contracts import project_task_output_entities
from src.agent.task_workflows import WORKFLOW_REGISTRY, ResolvedTask, TaskPlan
from src.tools.symbols import resolve_securities_csv


CONVERSATION_CONTEXT_VERSION = "1"
MAX_REFERENCE_TURNS = 4
MAX_RESULT_CONTEXT_CHARS = 8_000
MAX_PLANNER_CONTEXT_CHARS = 6_000
MAX_TASK_SELECTION_CONTEXT_CHARS = 3_000
_TOOL_IDENTITY_KEYS = frozenset({"tool", "toolname", "tool_name"})


class SecurityReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=80)


class TaskReference(BaseModel):
    """One executed semantic task, without its underlying tool identities."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task_id: str
    kind: str
    objective: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    entities: list[SecurityReference] = Field(default_factory=list)
    status: str
    blocked_reason: str | None = None
    action_fingerprint: str = ""
    result_context: Any = Field(default_factory=list)


class TurnReference(BaseModel):
    """Machine-readable output scope from one completed assistant turn."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request: str = Field(max_length=12_000)
    tasks: list[TaskReference] = Field(default_factory=list, max_length=12)
    entities: list[SecurityReference] = Field(default_factory=list, max_length=300)


class ConversationContext(BaseModel):
    """Rolling reference state persisted independently from rendered Markdown."""

    model_config = ConfigDict(extra="ignore")

    version: str = CONVERSATION_CONTEXT_VERSION
    turns: list[TurnReference] = Field(default_factory=list, max_length=MAX_REFERENCE_TURNS)

    @field_validator("turns", mode="before")
    @classmethod
    def _keep_recent_turns(cls, value: Any) -> Any:
        if isinstance(value, list):
            return value[-MAX_REFERENCE_TURNS:]
        return value

    @classmethod
    def from_value(cls, value: Any) -> "ConversationContext":
        if not isinstance(value, Mapping):
            return cls()
        try:
            parsed = cls.model_validate(value)
        except Exception:
            return cls()
        if parsed.version != CONVERSATION_CONTEXT_VERSION:
            return cls()
        return parsed._with_reprojected_collection_turns()

    def _with_reprojected_collection_turns(self) -> "ConversationContext":
        """Migrate v1 filter turns that stored their input rather than output."""
        migrated_turns: list[TurnReference] = []
        changed = False
        for turn in self.turns:
            projected_by_task: list[list[dict[str, str]]] = []
            migrated_tasks: list[TaskReference] = []
            for task in turn.tasks:
                if task.kind != "collection_financial_filter":
                    migrated_tasks.append(task)
                    continue
                raw_context = task.result_context
                result_context = (
                    raw_context
                    if isinstance(raw_context, list)
                    else [raw_context]
                    if isinstance(raw_context, Mapping)
                    else []
                )
                projected = project_task_output_entities(
                    task.kind,
                    [item.model_dump() for item in task.entities],
                    result_context,
                    task.parameters,
                )
                # Bounded historical context may lack one of the old batches.
                # Keep the old scope rather than replacing it with an empty
                # partial projection.
                if not projected:
                    migrated_tasks.append(task)
                    continue
                projected_by_task.append(projected)
                migrated_tasks.append(task.model_copy(update={
                    "entities": _security_references(projected),
                }))
                changed = True
            if projected_by_task:
                allowed = set.intersection(*(
                    {item["symbol"] for item in output}
                    for output in projected_by_task
                ))
                ordered = [
                    item
                    for output in projected_by_task
                    for item in output
                    if item["symbol"] in allowed
                ]
                migrated_turns.append(turn.model_copy(update={
                    "tasks": migrated_tasks,
                    "entities": _security_references(ordered),
                }))
            else:
                migrated_turns.append(turn)
        return (
            self.model_copy(update={"turns": migrated_turns})
            if changed
            else self
        )

    def append(self, turn: TurnReference) -> "ConversationContext":
        return self.model_copy(update={"turns": [*self.turns, turn][-MAX_REFERENCE_TURNS:]})

    def before_request(self, request: str) -> "ConversationContext":
        """Exclude the last completed run when regenerating the same request."""
        normalized = str(request or "").strip()
        if (
            normalized
            and self.turns
            and self.turns[-1].request.strip() == normalized
        ):
            return self.model_copy(update={"turns": self.turns[:-1]})
        return self

    def latest_entities(self) -> list[dict[str, str]]:
        if not self.turns:
            return []
        return [item.model_dump() for item in self.turns[-1].entities]

    def all_entities(self) -> list[dict[str, str]]:
        ordered: list[dict[str, str]] = []
        seen: set[str] = set()
        for turn in reversed(self.turns):
            for item in turn.entities:
                if item.symbol in seen:
                    continue
                seen.add(item.symbol)
                ordered.append(item.model_dump())
        return ordered

    def pending_action_fingerprints(self) -> set[str]:
        """Return only actions stopped for confirmation in the latest turn."""
        if not self.turns:
            return set()
        return {
            task.action_fingerprint
            for task in self.turns[-1].tasks
            if task.blocked_reason == "confirmation_required" and task.action_fingerprint
        }

    def planner_payload(self) -> dict[str, Any]:
        """Return only semantic tasks, verified entities and bounded results."""
        payload = self.model_dump()
        if len(json.dumps(payload, ensure_ascii=False, default=str)) <= MAX_PLANNER_CONTEXT_CHARS:
            return payload
        turns = payload.get("turns") or []
        for turn in turns:
            for task in turn.get("tasks") or []:
                task["result_context"] = {"truncated": True}
                if len(json.dumps(payload, ensure_ascii=False, default=str)) <= MAX_PLANNER_CONTEXT_CHARS:
                    return payload
        while len(turns) > 1 and len(
            json.dumps(payload, ensure_ascii=False, default=str)
        ) > MAX_PLANNER_CONTEXT_CHARS:
            turns.pop(0)
        return payload

    def task_selection_payload(self) -> dict[str, Any]:
        """Return semantic history only; entity rows and result bodies are separate inputs."""
        turns: list[dict[str, Any]] = []
        for turn in self.turns:
            turns.append({
                "request": turn.request[:2_000],
                "tasks": [
                    {
                        "kind": task.kind,
                        "objective": task.objective[:500],
                        "parameters": _bounded_json_value(
                            task.parameters, max_chars=1_200,
                        ),
                        "status": task.status,
                    }
                    for task in turn.tasks
                ],
                "verified_entity_count": len(turn.entities),
            })
        payload = {"version": self.version, "turns": turns}
        while len(turns) > 1 and len(
            json.dumps(payload, ensure_ascii=False, default=str)
        ) > MAX_TASK_SELECTION_CONTEXT_CHARS:
            turns.pop(0)
        return payload


def _without_tool_identity(value: Any) -> Any:
    """Remove execution-layer identities before state reaches the Planner."""
    if isinstance(value, Mapping):
        return {
            str(key): _without_tool_identity(item)
            for key, item in value.items()
            if str(key).replace("-", "_").lower() not in _TOOL_IDENTITY_KEYS
            and str(key).replace("-", "").lower() not in _TOOL_IDENTITY_KEYS
        }
    if isinstance(value, list):
        return [_without_tool_identity(item) for item in value]
    if isinstance(value, tuple):
        return [_without_tool_identity(item) for item in value]
    return value


def _bounded_json_value(value: Any, *, max_chars: int = MAX_RESULT_CONTEXT_CHARS) -> Any:
    """Bound arbitrary result data without interpreting domain language."""
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)[:max_chars]
    normalized = _without_tool_identity(json.loads(serialized))
    serialized = json.dumps(normalized, ensure_ascii=False, default=str)
    if len(serialized) <= max_chars:
        return normalized
    if isinstance(normalized, list):
        kept: list[Any] = []
        for item in normalized:
            candidate = [*kept, item]
            if len(json.dumps(candidate, ensure_ascii=False, default=str)) > max_chars:
                break
            kept.append(item)
        return {"items": kept, "truncated": True, "total_items": len(normalized)}
    if isinstance(normalized, Mapping):
        kept_map: dict[str, Any] = {}
        for key, item in normalized.items():
            candidate = {**kept_map, str(key): item}
            if len(json.dumps(candidate, ensure_ascii=False, default=str)) > max_chars:
                break
            kept_map[str(key)] = item
        kept_map["_truncated"] = True
        return kept_map
    return serialized[:max_chars]


def _message_text_from_structured_state(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, Mapping) and part.get("type") == "text"
    ).strip()


def _semantic_kind_for_legacy_tool(tool_name: str) -> str:
    matches = [
        kind.value
        for kind, spec in WORKFLOW_REGISTRY.items()
        if tool_name in spec.tool_whitelist
    ]
    return matches[0] if len(matches) == 1 else "legacy_structured_execution"


def recover_context_from_thread_state(thread_state: Any) -> ConversationContext:
    """Migrate rich structured UI events without parsing rendered prose.

    Older conversations may predate ``agent_context`` while still containing
    completed structured call arguments/results.  This adapter converts only
    those machine objects into the tool-name-free semantic state.  Assistant
    Markdown is never inspected and failed/text-only turns are ignored.
    """
    if not isinstance(thread_state, Mapping):
        return ConversationContext()
    entries = thread_state.get("messages")
    if not isinstance(entries, list):
        return ConversationContext()

    latest_user_request = ""
    turns: list[TurnReference] = []
    for message_index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        message = entry.get("message")
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "")
        if role == "user":
            text = _message_text_from_structured_state(message)
            if text:
                latest_user_request = text
            continue
        if role != "assistant":
            continue

        content = message.get("content")
        if not isinstance(content, list):
            continue
        tasks: list[TaskReference] = []
        all_entities: list[dict[str, str]] = []
        for part_index, part in enumerate(content):
            if not isinstance(part, Mapping) or part.get("type") != "tool-call":
                continue
            if bool(part.get("isError")):
                continue
            result = part.get("result")
            if result is None:
                continue
            tool_name = str(part.get("toolName") or "").strip()
            sanitized_result = _without_tool_identity(result)
            discovered = _structured_security_values(sanitized_result)
            entities = _security_references(discovered)
            all_entities.extend(item.model_dump() for item in entities)
            raw_args = part.get("args") if isinstance(part.get("args"), Mapping) else {}
            tasks.append(TaskReference(
                task_id=f"legacy_{message_index}_{part_index}",
                kind=_semantic_kind_for_legacy_tool(tool_name),
                objective=latest_user_request or "历史结构化执行",
                parameters=_bounded_json_value(raw_args),
                depends_on=[],
                entities=entities,
                status="completed",
                result_context=_bounded_json_value(sanitized_result),
            ))
        if tasks:
            turns.append(TurnReference(
                request=(latest_user_request or "历史结构化执行")[:12_000],
                tasks=tasks[:12],
                entities=_security_references(all_entities),
            ))
    return ConversationContext(turns=turns[-MAX_REFERENCE_TURNS:])


def _security_references(values: Iterable[dict[str, str]]) -> list[SecurityReference]:
    references: list[SecurityReference] = []
    seen: set[str] = set()
    for value in values:
        symbol = str(value.get("symbol") or "").strip()
        name = str(value.get("name") or symbol).strip()
        if symbol in seen:
            continue
        try:
            reference = SecurityReference(symbol=symbol, name=name)
        except Exception:
            continue
        seen.add(symbol)
        references.append(reference)
    return references


def _structured_security_values(value: Any) -> list[dict[str, str]]:
    """Collect securities only from typed result fields, never prose matching."""
    found: list[dict[str, str]] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            symbol = next((
                str(item.get(key) or "").strip()
                for key in ("symbol", "code", "stock_code", "stockCode")
                if item.get(key) is not None
            ), "")
            name = next((
                str(item.get(key) or "").strip()
                for key in ("name", "stock_name", "stockName")
                if item.get(key) is not None
            ), "")
            if len(symbol) == 6 and symbol.isdigit():
                found.append({"symbol": symbol, "name": name or symbol})
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return found


def build_turn_reference(
    request: str,
    plan: TaskPlan,
    resolved_tasks: list[ResolvedTask],
    execution: PlanExecutionResult,
) -> TurnReference:
    """Build reference state from typed execution objects, never answer prose."""
    resolved_by_id = {task.task_id: task for task in resolved_tasks}
    result_by_id = {result.task.task_id: result for result in execution.tasks}
    all_entities: list[dict[str, str]] = []
    transform_outputs: list[list[dict[str, str]]] = []
    task_references: list[TaskReference] = []

    for candidate in plan.tasks:
        resolved = resolved_by_id.get(candidate.task_id)
        result = result_by_id.get(candidate.task_id)
        result_context = [call.result for call in result.calls] if result else []
        input_entities, _ = resolve_securities_csv(
            ",".join(resolved.symbols if resolved else ()),
        )
        projected = project_task_output_entities(
            candidate.kind.value,
            input_entities,
            result_context,
            candidate.parameters,
        )
        task_entities = _security_references(projected)
        if candidate.kind.value == "collection_financial_filter":
            transform_outputs.append([item.model_dump() for item in task_entities])
        all_entities.extend(item.model_dump() for item in task_entities)
        task_references.append(TaskReference(
            task_id=candidate.task_id,
            kind=candidate.kind.value,
            objective=candidate.objective,
            parameters=_bounded_json_value(candidate.parameters, max_chars=8_000),
            depends_on=list(candidate.depends_on),
            entities=task_entities,
            status=result.status if result else "not_executed",
            blocked_reason=result.blocked_reason if result else None,
            action_fingerprint=action_fingerprint(resolved) if resolved else "",
            result_context=_bounded_json_value(result_context),
        ))

    if transform_outputs:
        # Multiple filters in one turn are independent predicates over the same
        # input collection.  Their user-visible output is the ordered
        # intersection, so that exact set becomes the next turn's scope.
        allowed = set.intersection(*(
            {item["symbol"] for item in output}
            for output in transform_outputs
        )) if transform_outputs else set()
        base_order = [
            item
            for output in transform_outputs
            for item in output
        ]
        turn_entities = _security_references(
            item for item in base_order if item["symbol"] in allowed
        )
    else:
        turn_entities = _security_references(all_entities)

    return TurnReference(
        request=str(request or "")[:12_000],
        tasks=task_references,
        entities=turn_entities,
    )


__all__ = [
    "CONVERSATION_CONTEXT_VERSION",
    "ConversationContext",
    "SecurityReference",
    "TaskReference",
    "TurnReference",
    "build_turn_reference",
    "recover_context_from_thread_state",
]
