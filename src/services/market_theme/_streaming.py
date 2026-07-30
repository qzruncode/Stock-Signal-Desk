# -*- coding: utf-8 -*-
"""liteLLM streaming, stream-part parsing and model-report streaming orchestration."""

from __future__ import annotations

import asyncio
from enum import Enum
import json
import logging
from typing import Any, Callable, Literal, Mapping, Optional

import litellm
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from src.llm.anthropic_gateway import (
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
)
from src.llm.generation_params import apply_litellm_generation_params
from src.storage import DatabaseManager, persist_llm_usage
from src.services.buy_criteria.mainline_policy import (
    MainlineLifecycle,
    MainlineTriggerProgress,
)

from ._llm import (
    _validate_model_report,
    build_model_report_prompts,
    build_streaming_report_draft,
)
from ._context import build_report_evidence_pack, collect_context

logger = logging.getLogger(__name__)

_MARKET_MAINLINE_REPORT_TOOL_NAME = "submit_market_mainline_report"
_MARKET_MAINLINE_MAX_TOKENS = 16_000


class MarketMainlineStageV3(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str
    description: str


class CurrentMarketMainlineV3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    name: str
    lifecycle: MainlineLifecycle
    stage: str
    reason: str
    branches: list[str]
    focus: str
    risks: list[str]
    evidence_refs: list[str]

    @model_validator(mode="after")
    def _confirmed_lifecycle(self) -> "CurrentMarketMainlineV3":
        if self.lifecycle not in {
            MainlineLifecycle.CONFIRMED,
            MainlineLifecycle.EXPANDING,
            MainlineLifecycle.FADING,
        }:
            raise ValueError(
                "current mainline lifecycle must be confirmed, expanding or fading"
            )
        return self


class CandidateMainlineTriggerV3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    description: str
    status: MainlineTriggerProgress
    evidence_refs: list[str] = Field(default_factory=list)


MainlineEvidenceAxis = Literal[
    "institution_consensus",
    "policy",
    "supply_demand",
    "technology",
    "capital_expenditure",
    "continuous_prosperity",
]


class CandidateMainlineEvidenceAxisV3(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    axis: MainlineEvidenceAxis
    evidence_refs: list[str] = Field(min_length=1)


class CandidateMarketMainlineV3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    name: str
    lifecycle: MainlineLifecycle
    stage_hint: str
    reason: str
    branches: list[str]
    expected_horizon: Literal[
        "one_to_six_months",
        "six_to_twelve_months",
        "over_twelve_months",
    ]
    evidence_axes: list[CandidateMainlineEvidenceAxisV3]
    trigger_assessments: list[CandidateMainlineTriggerV3]
    evidence_refs: list[str]

    @model_validator(mode="after")
    def _candidate_lifecycle(self) -> "CandidateMarketMainlineV3":
        if self.lifecycle not in {
            MainlineLifecycle.EMERGING,
            MainlineLifecycle.VALIDATING,
        }:
            raise ValueError(
                "candidate mainline lifecycle must be emerging or validating"
            )
        return self


class MarketMainlineEvidenceDigestV3(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    policy: list[str]
    industry: list[str]
    market: list[str]


class MarketMainlineReportV3(BaseModel):
    """Single source for provider Schema and local runtime validation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    generated_at: str
    as_of_date: str
    overview: str
    full_report: str
    market_stage: MarketMainlineStageV3
    current_mainlines: list[CurrentMarketMainlineV3]
    candidate_mainlines: list[CandidateMarketMainlineV3]
    action_summary: list[str]
    evidence_digest: MarketMainlineEvidenceDigestV3


def _inline_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Expand Pydantic references for gateways without ``$defs`` support."""
    definitions = schema.get("$defs")
    definitions = definitions if isinstance(definitions, dict) else {}

    def expand(value: Any) -> Any:
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = definitions.get(ref.rsplit("/", 1)[-1])
            if not isinstance(target, dict):
                raise ValueError(f"unresolved local JSON schema reference: {ref}")
            return {
                **expand(target),
                **{
                    key: expand(item)
                    for key, item in value.items()
                    if key != "$ref"
                },
            }
        return {
            key: expand(item)
            for key, item in value.items()
            if key != "$defs"
        }

    expanded = expand(schema)
    if not isinstance(expanded, dict):
        raise ValueError("expanded JSON schema is not an object")
    return expanded


_MARKET_MAINLINE_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": _MARKET_MAINLINE_REPORT_TOOL_NAME,
        "description": (
            "提交仅基于给定证据包形成的A股市场主线结构化报告"
        ),
        "parameters": _inline_json_schema(
            MarketMainlineReportV3.model_json_schema()
        ),
    },
}


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _market_mainline_payload_from_response(response: Any) -> dict[str, Any]:
    choices = _field(response, "choices") or []
    if not choices:
        raise ValueError("market mainline response has no choices")
    choice = choices[0]
    message = _field(choice, "message")
    for tool_call in _field(message, "tool_calls") or []:
        function = _field(tool_call, "function")
        if _field(function, "name") != _MARKET_MAINLINE_REPORT_TOOL_NAME:
            continue
        arguments = _field(function, "arguments")
        payload = json.loads(arguments) if isinstance(arguments, str) else arguments
        if isinstance(payload, dict):
            return payload
        raise ValueError("market mainline tool arguments are not an object")

    content = _field(message, "content")
    if isinstance(content, str) and content.strip():
        payload = json.loads(content)
        if isinstance(payload, dict):
            return payload
    raise ValueError(
        "market mainline response did not call the forced schema"
    )


def extract_json_object_from_text(raw_text: str) -> Optional[str]:
    text = (raw_text or "").strip()
    if not text:
        return None

    for start, ch in enumerate(text):
        if ch != "{":
            continue
        candidate = text[start:].strip()
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return candidate
    return None


def normalize_market_mainline_usage(raw_usage: Any) -> dict[str, Any]:
    if raw_usage is None:
        return {}

    def _read(name: str) -> int:
        if isinstance(raw_usage, dict):
            value = raw_usage.get(name)
        else:
            value = getattr(raw_usage, name, None)
        try:
            return int(value or 0)
        except Exception:
            return 0

    usage = {
        "prompt_tokens": _read("prompt_tokens"),
        "completion_tokens": _read("completion_tokens"),
        "total_tokens": _read("total_tokens"),
    }
    return usage if any(usage.values()) else {}


def extract_market_mainline_stream_parts(delta: Any) -> tuple[str, str]:
    if not delta:
        return "", ""

    content = getattr(delta, "content", None)
    if isinstance(delta, dict):
        content = delta.get("content")

    content_text = ""
    if isinstance(content, str):
        content_text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        content_text = "".join(parts)

    reasoning_values: list[str] = []
    for field_name in ("reasoning_content", "reasoning"):
        reasoning = _field(delta, field_name)
        if isinstance(reasoning, str) and reasoning:
            reasoning_values.append(reasoning)
    model_extra = _field(delta, "model_extra")
    if isinstance(model_extra, dict):
        for field_name in ("reasoning_content", "reasoning"):
            reasoning = model_extra.get(field_name)
            if (
                isinstance(reasoning, str)
                and reasoning
                and reasoning not in reasoning_values
            ):
                reasoning_values.append(reasoning)
    reasoning_text = "".join(reasoning_values)

    raw_text = "".join(part for part in (reasoning_text, content_text) if part)
    return raw_text, content_text


class MarketMainlineSchemaError(ValueError):
    """Retain the exact invalid output for one targeted repair."""

    def __init__(
        self,
        message: str,
        *,
        payload: dict[str, Any],
        issues: list[dict[str, Any]],
    ) -> None:
        super().__init__(message)
        self.payload = payload
        self.issues = issues


def _json_safe_contract_value(value: Any) -> Any:
    """Convert validation context into a stable JSON-compatible value."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_safe_contract_value(value.value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe_contract_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe_contract_value(item) for item in value]
    if isinstance(value, BaseException):
        return str(value)
    return str(value)


def _validation_issues(exc: ValidationError) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for error in exc.errors(include_url=False):
        location = error.get("loc") or ()
        pointer = "/" + "/".join(
            str(value).replace("~", "~0").replace("/", "~1")
            for value in location
        )
        issues.append({
            "pointer": pointer or "/",
            "code": str(error.get("type") or "schema_invalid"),
            "expected": str(error.get("msg") or "value matching schema"),
            "allowed": (
                _json_safe_contract_value(error.get("ctx"))
                if isinstance(error.get("ctx"), dict)
                else []
            ),
        })
    return issues


def _sum_usage(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: int(first.get(key) or 0) + int(second.get(key) or 0)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def stream_market_mainline_report_via_litellm(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    on_text: Optional[Callable[[str, str], None]] = None,
    on_reasoning: Optional[Callable[[str], None]] = None,
    payload_validator: Optional[
        Callable[[dict[str, Any]], dict[str, Any]]
    ] = None,
) -> tuple[str, str, str, dict[str, Any]]:
    llm_cfg = resolve_anthropic_gateway_config()

    async def _run() -> tuple[str, str, str, dict[str, Any]]:
        async def consume(
            call_kwargs: dict[str, Any],
        ) -> tuple[
            str,
            str,
            dict[str, Any],
            dict[str, Any],
        ]:
            response_stream = await litellm.acompletion(**call_kwargs)
            tool_arguments: dict[int, list[str]] = {}
            tool_names: dict[int, str] = {}
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            model_used = str(llm_cfg["model"])
            usage: dict[str, Any] = {}
            finish_reason: Any = None
            async for chunk in response_stream:
                model_used = str(_field(chunk, "model") or model_used)
                chunk_usage = normalize_market_mainline_usage(
                    _field(chunk, "usage")
                )
                if chunk_usage:
                    usage = chunk_usage
                choices = _field(chunk, "choices") or []
                if not choices:
                    continue
                choice = choices[0]
                finish_reason = _field(choice, "finish_reason") or finish_reason
                delta = _field(choice, "delta")
                raw_delta, content_delta = (
                    extract_market_mainline_stream_parts(delta)
                )
                if content_delta:
                    content_parts.append(content_delta)
                reasoning_delta = (
                    raw_delta[:-len(content_delta)]
                    if content_delta and raw_delta.endswith(content_delta)
                    else raw_delta
                )
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
                        tool_names[index] = name
                    arguments = _field(function, "arguments")
                    if not isinstance(arguments, str) or not arguments:
                        continue
                    tool_arguments.setdefault(index, []).append(arguments)
                    full_arguments = "".join(tool_arguments[index])
                    if on_text:
                        on_text(arguments, full_arguments)

            response_text = ""
            for index in sorted(tool_arguments):
                if tool_names.get(index) != _MARKET_MAINLINE_REPORT_TOOL_NAME:
                    continue
                response_text = "".join(tool_arguments[index])
                break
            if not response_text:
                response_text = "".join(content_parts).strip()
            invalid_payload = {
                "finish_reason": finish_reason,
                "content": "".join(content_parts),
                "reasoning_content": "".join(reasoning_parts),
                "tool_calls": [
                    {
                        "index": index,
                        "name": tool_names.get(index),
                        "arguments": "".join(parts),
                    }
                    for index, parts in sorted(tool_arguments.items())
                ],
                "model": model_used,
                "usage": usage,
            }
            return response_text, model_used, usage, invalid_payload

        async def consume_projection(
            call_kwargs: dict[str, Any],
        ) -> tuple[
            str,
            str,
            dict[str, Any],
            dict[str, Any],
        ]:
            """Consume the one-shot typed projection used for targeted repair."""
            response = await litellm.acompletion(**call_kwargs)
            model_used = str(
                _field(response, "model") or llm_cfg["model"]
            )
            usage = normalize_market_mainline_usage(
                _field(response, "usage")
            )
            choices = _field(response, "choices") or []
            choice = choices[0] if choices else None
            finish_reason = _field(choice, "finish_reason")
            message = _field(choice, "message")
            content = _field(message, "content")
            reasoning_content = (
                _field(message, "reasoning_content")
                or _field(message, "reasoning")
                or ""
            )
            tool_calls: list[dict[str, Any]] = []
            response_text = ""
            for index, tool_call in enumerate(
                _field(message, "tool_calls") or []
            ):
                function = _field(tool_call, "function")
                name = _field(function, "name")
                arguments = _field(function, "arguments")
                if isinstance(arguments, dict):
                    arguments_text = json.dumps(
                        arguments,
                        ensure_ascii=False,
                    )
                else:
                    arguments_text = (
                        arguments if isinstance(arguments, str) else ""
                    )
                tool_calls.append({
                    "index": index,
                    "name": name,
                    "arguments": arguments_text,
                })
                if (
                    not response_text
                    and name == _MARKET_MAINLINE_REPORT_TOOL_NAME
                ):
                    response_text = arguments_text
            if not response_text and isinstance(content, str):
                response_text = content.strip()
            invalid_payload = {
                "finish_reason": finish_reason,
                "content": content if isinstance(content, str) else "",
                "reasoning_content": (
                    reasoning_content
                    if isinstance(reasoning_content, str)
                    else ""
                ),
                "tool_calls": tool_calls,
                "model": model_used,
                "usage": usage,
            }
            return response_text, model_used, usage, invalid_payload

        def validate(
            response_text: str,
            invalid_payload: dict[str, Any],
        ) -> dict[str, Any]:
            if not response_text:
                raise MarketMainlineSchemaError(
                    "market mainline response did not call the forced schema",
                    payload=invalid_payload,
                    issues=[{
                        "pointer": "/choices/0/message/tool_calls",
                        "code": "forced_tool_call_missing",
                        "expected": (
                            "exactly one submit_market_mainline_report tool "
                            "call matching the supplied schema"
                        ),
                        "allowed": [_MARKET_MAINLINE_REPORT_TOOL_NAME],
                    }],
                )
            try:
                raw_payload = json.loads(response_text)
            except json.JSONDecodeError as exc:
                raise MarketMainlineSchemaError(
                    "market mainline response is not valid JSON",
                    payload={
                        **invalid_payload,
                        "candidate_arguments": response_text,
                    },
                    issues=[{
                        "pointer": "/choices/0/message/tool_calls/0/function/arguments",
                        "code": "json_invalid",
                        "expected": "one complete JSON object",
                        "allowed": [],
                    }],
                ) from exc
            if not isinstance(raw_payload, dict):
                raise MarketMainlineSchemaError(
                    "market mainline payload is not an object",
                    payload={"candidate_arguments": raw_payload},
                    issues=[{
                        "pointer": "/",
                        "code": "object_type_required",
                        "expected": "JSON object",
                        "allowed": [],
                    }],
                )
            try:
                typed_payload = MarketMainlineReportV3.model_validate(
                    raw_payload
                ).model_dump(mode="json")
            except ValidationError as exc:
                raise MarketMainlineSchemaError(
                    "market mainline payload failed exact schema validation",
                    payload=raw_payload,
                    issues=_validation_issues(exc),
                ) from exc
            if payload_validator:
                try:
                    typed_payload = payload_validator(typed_payload)
                except Exception as exc:
                    raise MarketMainlineSchemaError(
                        "market mainline payload failed evidence binding",
                        payload=typed_payload,
                        issues=[{
                            "pointer": "/current_mainlines",
                            "code": "evidence_binding_invalid",
                            "expected": (
                                "at least one current or candidate mainline "
                                "with valid board names and evidence references"
                            ),
                            "allowed": [],
                        }],
                    ) from exc
            return typed_payload

        base_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        call_kwargs = build_litellm_kwargs(
            llm_cfg,
            stream=True,
            messages=base_messages,
            tools=[_MARKET_MAINLINE_REPORT_TOOL],
            tool_choice={
                "type": "function",
                "function": {"name": _MARKET_MAINLINE_REPORT_TOOL_NAME},
            },
            max_tokens=max_tokens,
        )
        call_kwargs = apply_litellm_generation_params(
            call_kwargs,
            llm_cfg["model"],
            temperature,
        )
        (
            response_text,
            model_used,
            usage,
            invalid_payload,
        ) = await consume(call_kwargs)
        repair_record: dict[str, Any] | None = None
        try:
            payload = validate(response_text, invalid_payload)
        except MarketMainlineSchemaError as first_error:
            repair_record = {
                "attempted": True,
                "issues": first_error.issues,
            }
            repair_request = {
                "targeted_repair": {
                    "invalid_payload": first_error.payload,
                    "issues": first_error.issues,
                    "instruction": (
                        "只修复上述结构化输出错误；使用同一份精确 Schema "
                        "提交一次 submit_market_mainline_report。不得重做"
                        "市场分析、改变证据结论或生成新的任务。"
                    ),
                },
            }
            repair_kwargs = build_litellm_kwargs(
                llm_cfg,
                stream=False,
                messages=[
                    *base_messages,
                    {
                        "role": "user",
                        "content": json.dumps(
                            repair_request,
                            ensure_ascii=False,
                        ),
                    },
                ],
                tools=[_MARKET_MAINLINE_REPORT_TOOL],
                tool_choice={
                    "type": "function",
                    "function": {
                        "name": _MARKET_MAINLINE_REPORT_TOOL_NAME
                    },
                },
                max_tokens=max(8192, max_tokens),
                extra_body={
                    "thinking": {"type": "disabled"},
                    "reasoning_effort": "none",
                },
            )
            repair_kwargs = apply_litellm_generation_params(
                repair_kwargs,
                llm_cfg["model"],
                0,
            )
            (
                repaired_text,
                repair_model,
                repair_usage,
                repaired_invalid_payload,
            ) = await consume_projection(repair_kwargs)
            usage = _sum_usage(usage, repair_usage)
            model_used = repair_model or model_used
            try:
                payload = validate(
                    repaired_text,
                    repaired_invalid_payload,
                )
            except MarketMainlineSchemaError as second_error:
                repair_record["succeeded"] = False
                repair_record["final_issues"] = second_error.issues
                raise MarketMainlineSchemaError(
                    "market mainline output remained invalid after one "
                    "targeted repair",
                    payload=second_error.payload,
                    issues=second_error.issues,
                ) from second_error
            repair_record["succeeded"] = True

        response_text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if on_text and not response_text:
            on_text(response_text, response_text)
        if repair_record is not None:
            usage["repair_record"] = repair_record
        return (
            response_text,
            response_text,
            model_used,
            usage,
        )

    return asyncio.run(_run())


def generate_model_report_stream(
    *,
    force: bool,
    task_queue: Any,
    task_id: str,
) -> dict[str, Any]:
    task_queue.update_task_progress(task_id, 5, "正在准备市场主线证据包")
    try:
        context = collect_context(force=force, include_rss=True)
    except Exception as exc:
        raise RuntimeError(f"市场数据采集失败: {exc}") from exc

    evidence_pack = build_report_evidence_pack(context)
    system_prompt, user_prompt = build_model_report_prompts(evidence_pack)
    task_queue.update_task_result(
        task_id,
        {
            "phase": "collecting",
            "stream_text": "",
            "report_draft": {
                "as_of_date": str((context.get("source_snapshot") or {}).get("market_status", {}).get("data_time") or ""),
            },
            "debug_input": {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "evidence_pack": evidence_pack,
            },
        },
        progress=18,
        message="证据包已准备完成，等待模型连接",
    )

    task_queue.update_task_result(
        task_id,
        {
            "phase": "waiting_model",
            "debug_input": {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "evidence_pack": evidence_pack,
            },
        },
        progress=24,
        message="正在连接模型服务",
    )

    try:
        result = build_llm_model_report_streaming(
            context,
            evidence_pack=evidence_pack,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            on_text=lambda accumulated_text, draft: task_queue.update_task_result(
                task_id,
                {
                    "phase": "generating",
                    "stream_text": accumulated_text,
                    "report_draft": draft,
                    "debug_input": {
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "evidence_pack": evidence_pack,
                    },
                },
                progress=min(92, 24 + max(1, len(accumulated_text) // 120)),
                message="模型已连接，正在生成研判内容",
            ),
        )
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    if not result:
        raise RuntimeError("模型研判生成失败")

    return {
        "phase": "completed",
        "stream_text": result.get("raw_stream_output") or result.get("raw_response") or result.get("full_report") or "",
        "report_draft": {
            "overview": result.get("overview"),
            "full_report": result.get("full_report"),
            "as_of_date": result.get("as_of_date"),
            "market_stage": result.get("market_stage"),
        },
        "debug_input": {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "evidence_pack": evidence_pack,
        },
        "report": result,
        "llm_used": result.get("llm_used", True),
        "model_used": result.get("model_used"),
    }


def build_llm_model_report_streaming(
    context: dict[str, Any],
    *,
    evidence_pack: Optional[dict[str, Any]] = None,
    system_prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
    on_text: Optional[Any] = None,
    on_reasoning: Optional[Callable[[str], None]] = None,
) -> Optional[dict[str, Any]]:
    evidence_pack = evidence_pack or build_report_evidence_pack(context)
    if system_prompt is None or user_prompt is None:
        system_prompt, user_prompt = build_model_report_prompts(evidence_pack)

    def _on_stream_text(_delta_text: str, full_text: str) -> None:
        if on_text:
            on_text(full_text, build_streaming_report_draft(full_text))

    try:
        (
            raw_response_text,
            response_text,
            model_used,
            usage,
        ) = stream_market_mainline_report_via_litellm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=_MARKET_MAINLINE_MAX_TOKENS,
            on_text=_on_stream_text,
            on_reasoning=on_reasoning,
            payload_validator=lambda payload: _validate_model_report(
                payload,
                evidence_pack,
            ),
        )
        repair_record = usage.pop("repair_record", None)
        persist_llm_usage(
            usage,
            model_used,
            call_type="market_mainline_report",
        )
        parsed = json.loads(response_text)
        if not isinstance(parsed, dict):
            raise ValueError("market mainline payload is not an object")
        parsed = _validate_model_report(parsed, evidence_pack)
        parsed.setdefault("generated_at", context["generated_at"])
        parsed.setdefault("as_of_date", evidence_pack["as_of_date"])
        parsed["llm_used"] = True
        parsed["model_used"] = model_used
        parsed.setdefault("current_mainlines", [])
        parsed.setdefault("candidate_mainlines", [])
        parsed.setdefault("future_mainlines", [])
        parsed.setdefault("action_summary", [])
        parsed.setdefault(
            "evidence_digest",
            {"policy": [], "industry": [], "market": []},
        )
        parsed.setdefault(
            "source_summary",
            evidence_pack.get("source_summary") or {},
        )
        parsed["raw_stream_output"] = raw_response_text
        parsed["raw_response"] = raw_response_text
        if repair_record:
            parsed["repair_records"] = [repair_record]
        parsed["debug_input"] = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "evidence_pack": evidence_pack,
        }
        DatabaseManager.get_instance().save_market_mainline_report(
            report_key="market_mainline",
            as_of_date=str(
                parsed.get("as_of_date")
                or evidence_pack["as_of_date"]
            ),
            mode="llm",
            payload=parsed,
            raw_response=raw_response_text,
            model_used=model_used,
        )
        return parsed
    except Exception as exc:
        logger.warning(
            "market mainline forced-schema generation failed",
            exc_info=True,
        )
        raise RuntimeError(f"市场主线结构化生成失败: {exc}") from exc


def generate_model_report_inline(
    *,
    force: bool,
    on_progress: Optional[
        Callable[[int, str, Optional[str]], None]
    ] = None,
) -> dict[str, Any]:
    """Generate the shared snapshot inside the owning Workflow lifecycle."""

    def emit(
        progress: int,
        message: str,
        reasoning_delta: Optional[str] = None,
    ) -> None:
        if on_progress:
            on_progress(progress, message, reasoning_delta)

    emit(5, "正在准备市场主线证据包")
    try:
        context = collect_context(force=force, include_rss=True)
    except Exception as exc:
        raise RuntimeError(f"市场数据采集失败: {exc}") from exc
    evidence_pack = build_report_evidence_pack(context)
    system_prompt, user_prompt = build_model_report_prompts(evidence_pack)
    emit(18, "市场主线证据包已准备完成")
    emit(24, "模型已连接，正在生成市场主线结构")
    last_text_progress = 24

    def report_text_progress(
        accumulated_text: str,
        _draft: Any,
    ) -> None:
        nonlocal last_text_progress
        progress = min(
            92,
            24 + max(1, len(accumulated_text) // 120),
        )
        if progress <= last_text_progress:
            return
        last_text_progress = progress
        emit(progress, "正在生成市场主线结构")

    result = build_llm_model_report_streaming(
        context,
        evidence_pack=evidence_pack,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        on_text=report_text_progress,
        on_reasoning=lambda delta: emit(
            24,
            "正在分析市场主线证据",
            delta,
        ),
    )
    if not result:
        raise RuntimeError("模型研判生成失败")
    emit(100, "市场主线快照已生成")
    return result
