"""Function group 1 extracted from src/agent/orchestrator_v2/planner.py."""

from __future__ import annotations

from src.agent.orchestrator_v2.planner import (
    asyncio,
    dataclass,
    date,
    inspect,
    json,
    repair_json,
    logging,
    os,
    re,
    time,
    MappingProxyType,
    Any,
    Awaitable,
    Callable,
    Literal,
    Mapping,
    uuid,
    BaseModel,
    ValidationError,
    AgentErrorCode,
    AgentStage,
    AgentStageEventV2,
    AssumptionRecord,
    GoalContractV2,
    InputReferenceV2,
    IntentOutlineNodeV2,
    IntentOutlineV2,
    Capability,
    OrchestratorV2Error,
    PlanningTraceV2,
    PlannerVerificationV2,
    QuestionType,
    RepairIssueV2,
    RepairRecordV2,
    ResourceType,
    StageObserver,
    StageStatus,
    capability_catalog,
    capability_for,
    normalize_capability_intent,
    current_user_request,
    build_litellm_kwargs,
    V2_SCHEMA_VERSION,
    MODEL_PROGRESS_HEARTBEAT_SECONDS,
    logger,
    RawProviderPayloadError,
    MissingProviderPayloadError,
    ExactContractValidationError,
    _AvailableArtifact,
    PlannedIntentNodeV2,
    PlannedIntentGraphV2,
    _OUTLINE_SYSTEM_PROMPT,
    _INTENT_SYSTEM_PROMPT,
    _VERIFIER_SYSTEM_PROMPT,
    __all__,
 )

__all__ = ['_runtime_float', '_runtime_int', '_json_structure_is_closed', '_json_object', '_payload_from_response', '_pointer', '_deref_schema', '_schema_at_location', '_normalize_json_encoded_contract_fields', '_schema_expectation', '_repair_issues', '_function_tool', '_exact_contract_messages', '_emit']

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


def _json_structure_is_closed(text: str) -> bool:
    """Allow local repair only when no source content has been truncated."""
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"}": "{", "]": "["}
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            stack.append(character)
        elif character in "}]":
            if not stack or stack[-1] != pairs[character]:
                return False
            stack.pop()
    return not in_string and not stack


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
            if not _json_structure_is_closed(candidate):
                continue
            try:
                repaired = repair_json(candidate, return_objects=True)
            except Exception:
                continue
            if isinstance(repaired, dict):
                return repaired
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
    raise MissingProviderPayloadError(f"model returned no {function_name} payload")

def _pointer(location: tuple[Any, ...]) -> str:
    if not location:
        return "/"
    escaped = [str(item).replace("~", "~0").replace("/", "~1") for item in location]
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
            resolved = [_deref_schema(branch, root) for branch in branches if isinstance(branch, Mapping)]
            titled = next((branch for branch in resolved if branch.get("title") == str(part)), None)
            if titled is not None:
                current = titled
                continue
            property_branch = next(
                (
                    branch
                    for branch in resolved
                    if isinstance(branch.get("properties"), Mapping) and str(part) in branch["properties"]
                ),
                None,
            )
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

def _normalize_json_encoded_contract_fields(
    payload: dict[str, Any],
    model: type[BaseModel],
) -> dict[str, Any]:
    """Unwrap provider-stringified nested objects using the exact Schema.

    Some OpenAI-compatible providers preserve the outer function arguments as
    JSON but serialize nested object/array fields a second time. The transport
    boundary may safely undo that encoding only where the contract requires a
    structured value; the strict Pydantic model remains authoritative.
    """

    root = model.model_json_schema()

    def branches(schema: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        resolved = _deref_schema(schema, root)
        alternatives = resolved.get("anyOf") or resolved.get("oneOf")
        if not isinstance(alternatives, list):
            return (resolved,)
        return tuple(_deref_schema(item, root) for item in alternatives if isinstance(item, Mapping))

    def structured_branch(
        schema: Mapping[str, Any],
        kind: str,
    ) -> Mapping[str, Any] | None:
        return next(
            (
                item
                for item in branches(schema)
                if item.get("type") == kind or (kind == "object" and isinstance(item.get("properties"), Mapping))
            ),
            None,
        )

    def normalize(
        value: Any,
        schema: Mapping[str, Any],
        *,
        depth: int,
    ) -> Any:
        if depth > 24:
            return value
        candidates = branches(schema)
        string_allowed = any(item.get("type") == "string" for item in candidates)
        object_schema = structured_branch(schema, "object")
        array_schema = structured_branch(schema, "array")
        if isinstance(value, str) and not string_allowed:
            stripped = value.strip()
            expected_container = (
                object_schema
                if stripped.startswith("{") and stripped.endswith("}")
                else (array_schema if stripped.startswith("[") and stripped.endswith("]") else None)
            )
            if expected_container is not None and len(stripped) <= 250_000:
                try:
                    decoded = json.loads(stripped)
                except (TypeError, ValueError):
                    decoded = value
                if (expected_container is object_schema and isinstance(decoded, dict)) or (
                    expected_container is array_schema and isinstance(decoded, list)
                ):
                    value = decoded
        if isinstance(value, Mapping) and object_schema is not None:
            properties = object_schema.get("properties")
            additional = object_schema.get("additionalProperties")
            return {
                key: (
                    normalize(
                        item,
                        (
                            properties[key]
                            if isinstance(properties, Mapping)
                            and key in properties
                            and isinstance(properties[key], Mapping)
                            else additional
                        ),
                        depth=depth + 1,
                    )
                    if (
                        (isinstance(properties, Mapping) and key in properties and isinstance(properties[key], Mapping))
                        or isinstance(additional, Mapping)
                    )
                    else item
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)) and array_schema is not None:
            items = array_schema.get("items")
            if isinstance(items, Mapping):
                return [normalize(item, items, depth=depth + 1) for item in value]
        return value

    normalized = normalize(payload, root, depth=0)
    return dict(normalized) if isinstance(normalized, Mapping) else payload

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
            schema_expected, schema_allowed = _schema_expectation(_schema_at_location(model, location))
            expected = str(context.get("expected") or context.get("class_name") or schema_expected or error.get("type"))
            issues.append(
                RepairIssueV2(
                    pointer=_pointer(location),
                    code=str(error.get("type") or "validation_error"),
                    expected=expected,
                    allowed=schema_allowed,
                    message=str(error.get("msg") or "invalid value"),
                )
            )
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
                "最终回答只能是一个完整 JSON 对象，不得包含解释、Markdown、" "代码围栏或 JSON 之外的文字。"
            ),
        }
        prompt = (
            system_prompt
            + (
                "\n\n当前严格函数参数通道未返回可解析 JSON，现改用 JSON 内容" "通道完成同一次定点修复。"
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
    result = observer(
        AgentStageEventV2(
            run_id=run_id,
            stage=stage,
            status=status,
            task_id=task_id,
            error_code=error_code,
            summary=summary,
        )
    )
    if inspect.isawaitable(result):
        await result
