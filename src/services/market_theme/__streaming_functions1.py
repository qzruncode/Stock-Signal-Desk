"""Function group 1 extracted from src/services/market_theme/_streaming.py."""

from __future__ import annotations

from src.services.market_theme._streaming import (
    asyncio,
    Enum,
    json,
    logging,
    Any,
    Callable,
    Literal,
    Mapping,
    Optional,
    litellm,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
    apply_litellm_generation_params,
    DatabaseManager,
    persist_llm_usage,
    MainlineLifecycle,
    MainlineTriggerProgress,
    _validate_model_report,
    build_model_report_prompts,
    build_streaming_report_draft,
    build_report_evidence_pack,
    collect_context,
    logger,
    _MARKET_MAINLINE_REPORT_TOOL_NAME,
    _MARKET_MAINLINE_MAX_TOKENS,
    MarketMainlineStageV3,
    CurrentMarketMainlineV3,
    CandidateMainlineTriggerV3,
    MainlineEvidenceAxis,
    CandidateMainlineEvidenceAxisV3,
    CandidateMarketMainlineV3,
    MarketMainlineEvidenceDigestV3,
    MarketMainlineReportV3,
    MarketMainlineSchemaError,
 )

__all__ = ['_inline_json_schema', '_field', '_market_mainline_payload_from_response', 'extract_json_object_from_text', 'normalize_market_mainline_usage', 'extract_market_mainline_stream_parts', '_json_safe_contract_value', '_validation_issues', '_sum_usage']

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
                **{key: expand(item) for key, item in value.items() if key != "$ref"},
            }
        return {key: expand(item) for key, item in value.items() if key != "$defs"}

    expanded = expand(schema)
    if not isinstance(expanded, dict):
        raise ValueError("expanded JSON schema is not an object")
    return expanded

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
    raise ValueError("market mainline response did not call the forced schema")

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
            if isinstance(reasoning, str) and reasoning and reasoning not in reasoning_values:
                reasoning_values.append(reasoning)
    reasoning_text = "".join(reasoning_values)

    raw_text = "".join(part for part in (reasoning_text, content_text) if part)
    return raw_text, content_text

def _json_safe_contract_value(value: Any) -> Any:
    """Convert validation context into a stable JSON-compatible value."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_safe_contract_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe_contract_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe_contract_value(item) for item in value]
    if isinstance(value, BaseException):
        return str(value)
    return str(value)

def _validation_issues(exc: ValidationError) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for error in exc.errors(include_url=False):
        location = error.get("loc") or ()
        pointer = "/" + "/".join(str(value).replace("~", "~0").replace("/", "~1") for value in location)
        issues.append(
            {
                "pointer": pointer or "/",
                "code": str(error.get("type") or "schema_invalid"),
                "expected": str(error.get("msg") or "value matching schema"),
                "allowed": (_json_safe_contract_value(error.get("ctx")) if isinstance(error.get("ctx"), dict) else []),
            }
        )
    return issues

def _sum_usage(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: int(first.get(key) or 0) + int(second.get(key) or 0)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
