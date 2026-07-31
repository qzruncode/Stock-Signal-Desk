"""Function group 3 extracted from src/services/market_theme/_streaming.py."""

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

__all__ = ['build_llm_model_report_streaming', 'generate_model_report_inline']

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
            as_of_date=str(parsed.get("as_of_date") or evidence_pack["as_of_date"]),
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
    on_progress: Optional[Callable[[int, str, Optional[str]], None]] = None,
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
