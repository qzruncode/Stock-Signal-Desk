"""Function group 2 extracted from src/services/market_theme/_streaming.py."""

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

__all__ = ['stream_market_mainline_report_via_litellm', 'generate_model_report_stream']

def stream_market_mainline_report_via_litellm(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    on_text: Optional[Callable[[str, str], None]] = None,
    on_reasoning: Optional[Callable[[str], None]] = None,
    payload_validator: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
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
                chunk_usage = normalize_market_mainline_usage(_field(chunk, "usage"))
                if chunk_usage:
                    usage = chunk_usage
                choices = _field(chunk, "choices") or []
                if not choices:
                    continue
                choice = choices[0]
                finish_reason = _field(choice, "finish_reason") or finish_reason
                delta = _field(choice, "delta")
                raw_delta, content_delta = extract_market_mainline_stream_parts(delta)
                if content_delta:
                    content_parts.append(content_delta)
                reasoning_delta = (
                    raw_delta[: -len(content_delta)]
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
            model_used = str(_field(response, "model") or llm_cfg["model"])
            usage = normalize_market_mainline_usage(_field(response, "usage"))
            choices = _field(response, "choices") or []
            choice = choices[0] if choices else None
            finish_reason = _field(choice, "finish_reason")
            message = _field(choice, "message")
            content = _field(message, "content")
            reasoning_content = _field(message, "reasoning_content") or _field(message, "reasoning") or ""
            tool_calls: list[dict[str, Any]] = []
            response_text = ""
            for index, tool_call in enumerate(_field(message, "tool_calls") or []):
                function = _field(tool_call, "function")
                name = _field(function, "name")
                arguments = _field(function, "arguments")
                if isinstance(arguments, dict):
                    arguments_text = json.dumps(
                        arguments,
                        ensure_ascii=False,
                    )
                else:
                    arguments_text = arguments if isinstance(arguments, str) else ""
                tool_calls.append(
                    {
                        "index": index,
                        "name": name,
                        "arguments": arguments_text,
                    }
                )
                if not response_text and name == _MARKET_MAINLINE_REPORT_TOOL_NAME:
                    response_text = arguments_text
            if not response_text and isinstance(content, str):
                response_text = content.strip()
            invalid_payload = {
                "finish_reason": finish_reason,
                "content": content if isinstance(content, str) else "",
                "reasoning_content": (reasoning_content if isinstance(reasoning_content, str) else ""),
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
                    issues=[
                        {
                            "pointer": "/choices/0/message/tool_calls",
                            "code": "forced_tool_call_missing",
                            "expected": (
                                "exactly one submit_market_mainline_report tool " "call matching the supplied schema"
                            ),
                            "allowed": [_MARKET_MAINLINE_REPORT_TOOL_NAME],
                        }
                    ],
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
                    issues=[
                        {
                            "pointer": "/choices/0/message/tool_calls/0/function/arguments",
                            "code": "json_invalid",
                            "expected": "one complete JSON object",
                            "allowed": [],
                        }
                    ],
                ) from exc
            if not isinstance(raw_payload, dict):
                raise MarketMainlineSchemaError(
                    "market mainline payload is not an object",
                    payload={"candidate_arguments": raw_payload},
                    issues=[
                        {
                            "pointer": "/",
                            "code": "object_type_required",
                            "expected": "JSON object",
                            "allowed": [],
                        }
                    ],
                )
            try:
                typed_payload = MarketMainlineReportV3.model_validate(raw_payload).model_dump(mode="json")
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
                        issues=[
                            {
                                "pointer": "/current_mainlines",
                                "code": "evidence_binding_invalid",
                                "expected": (
                                    "at least one current or candidate mainline "
                                    "with valid board names and evidence references"
                                ),
                                "allowed": [],
                            }
                        ],
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
                    "function": {"name": _MARKET_MAINLINE_REPORT_TOOL_NAME},
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
                    "market mainline output remained invalid after one " "targeted repair",
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
                "as_of_date": str(
                    (context.get("source_snapshot") or {}).get("market_status", {}).get("data_time") or ""
                ),
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
