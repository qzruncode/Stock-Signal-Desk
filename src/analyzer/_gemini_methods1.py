"""Method group 1 for GeminiAnalyzer."""

from src.analyzer.gemini_analyzer import (
    contextlib,
    json,
    logging,
    math,
    re,
    time,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    litellm,
    repair_json,
    Config,
    resolve_news_window_days,
    STOCK_NAME_MAP,
    AnthropicGatewayConfigError,
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
    call_litellm_with_param_recovery,
    apply_litellm_generation_params,
    get_market_guidelines,
    get_market_role,
    get_no_data_text,
    get_unknown_text,
    infer_decision_type_from_advice,
    localize_confidence_level,
    normalize_report_language,
    AnalysisReportSchema,
    persist_llm_usage,
    _safe_float,
    apply_placeholder_fill,
    check_content_integrity,
    AnalysisResult,
    _sanitize_trend_analysis_for_prompt,
    logger,
    _LiteLLMStreamError,
    _AllModelsFailedError,
 )

class _GeminiAnalyzerMethods1:
    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        config: Optional[Config] = None,
    ):
        """Initialize LLM Analyzer.

        模型/鉴权统一由 Anthropic 网关配置（ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL）决定，
        不再在初始化期构建 Router；配置是否齐全在 :meth:`is_available` / 调用时按需检测。

        Args:
            api_key: Ignored (kept for backward compatibility). Keys come from the gateway config.
        """
        self._config_override = config
    def _get_runtime_config(self) -> Config:
        """Return the runtime config, honoring injected overrides for tests/pipeline."""
        # 延迟从 src.analyzer 命名空间取 get_config，保留测试对 `src.analyzer.get_config` 的 patch 能力
        import src.analyzer as _analyzer_pkg

        return getattr(self, "_config_override", None) or _analyzer_pkg.get_config()
    def _get_analysis_system_prompt(self, report_language: str, stock_code: str = "") -> str:
        """Build the analyzer system prompt with output-language guidance."""
        lang = normalize_report_language(report_language)
        market_role = get_market_role(stock_code, lang)
        market_guidelines = get_market_guidelines(stock_code, lang)
        base_prompt = self.SYSTEM_PROMPT.replace("{market_placeholder}", market_role).replace(
            "{guidelines_placeholder}", market_guidelines
        )
        if lang == "en":
            return (
                base_prompt
                + """

## Output Language (highest priority)

- Keep all JSON keys unchanged.
- `decision_type` must remain `buy|hold|sell`.
- All human-readable JSON values must be written in English.
- Use the common English company name when you are confident; otherwise keep the original listed company name instead of inventing one.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, nested dashboard text, checklist items, and all narrative summaries.
"""
            )
        return (
            base_prompt
            + """

## 输出语言（最高优先级）

- 所有 JSON 键名保持不变。
- `decision_type` 必须保持为 `buy|hold|sell`。
- 所有面向用户的人类可读文本值必须使用中文。
"""
        )
    def is_available(self) -> bool:
        """Check if the Anthropic gateway is fully configured (base_url/token/model all set)."""
        import os

        return bool(
            (os.getenv("ANTHROPIC_BASE_URL") or "").strip()
            and (os.getenv("ANTHROPIC_AUTH_TOKEN") or "").strip()
            and (os.getenv("ANTHROPIC_MODEL") or "").strip()
        )
    def _dispatch_litellm_completion(
        self,
        call_kwargs: Dict[str, Any],
    ) -> Any:
        """Dispatch a sync LiteLLM completion.

        网关单源后无 Router/多供应商分支：kwargs 已由 ``build_litellm_kwargs`` 组装好
        鉴权字段（api_key/api_base/custom_llm_provider/extra_headers），直接调用即可。
        """
        return litellm.completion(**call_kwargs)
    def _normalize_usage(self, usage_obj: Any) -> Dict[str, Any]:
        """Normalize usage objects from LiteLLM responses/chunks."""
        if not usage_obj:
            return {}

        def _get_value(key: str) -> int:
            if isinstance(usage_obj, dict):
                return int(usage_obj.get(key) or 0)
            return int(getattr(usage_obj, key, 0) or 0)

        return {
            "prompt_tokens": _get_value("prompt_tokens"),
            "completion_tokens": _get_value("completion_tokens"),
            "total_tokens": _get_value("total_tokens"),
        }
    @staticmethod
    def _get_response_field(obj: Any, key: str) -> Any:
        """Read a field from dict-like or object-like LiteLLM payloads."""
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)
    def _extract_text_blocks(self, blocks: Any) -> str:
        """Extract text from OpenAI-compatible content block lists."""
        if not blocks:
            return ""

        parts: List[str] = []
        for block in blocks:
            if isinstance(block, str):
                parts.append(block)
                continue

            text = None
            if isinstance(block, dict):
                text = block.get("text")
                if text is None:
                    text = block.get("content")
            else:
                text = getattr(block, "text", None)
                if text is None:
                    text = getattr(block, "content", None)

            if isinstance(text, str) and text:
                parts.append(text)

        return "".join(parts).strip()
    def _extract_completion_text(self, response: Any) -> str:
        """Extract text from non-stream LiteLLM completion responses."""
        choices = self._get_response_field(response, "choices")
        if not choices:
            return ""

        choice = choices[0]
        message = self._get_response_field(choice, "message")

        content_blocks = self._get_response_field(choice, "content_blocks")
        if content_blocks is None and message is not None:
            content_blocks = self._get_response_field(message, "content_blocks")
        block_text = self._extract_text_blocks(content_blocks)
        if block_text:
            return block_text

        content = None
        if message is not None:
            content = self._get_response_field(message, "content")
        if content is None:
            content = self._get_response_field(choice, "content")

        if isinstance(content, list):
            return self._extract_text_blocks(content)
        if isinstance(content, str):
            return content.strip()
        return str(content).strip() if content is not None else ""
    def _extract_stream_text(self, chunk: Any) -> str:
        """Extract provider-agnostic text delta from a LiteLLM streaming chunk."""
        choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
        if not choices:
            return ""

        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice, dict) else getattr(choice, "delta", None)
        message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)

        content: Any = None
        if isinstance(delta, dict):
            content = delta.get("content")
        elif isinstance(delta, str):
            content = delta
        elif delta is not None:
            content = getattr(delta, "content", None)

        if content is None:
            if isinstance(message, dict):
                content = message.get("content")
            elif message is not None:
                content = getattr(message, "content", None)

        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)

        return content if isinstance(content, str) else ""
    def _consume_litellm_stream(
        self,
        stream_response: Any,
        *,
        model: str,
        progress_callback: Optional[Callable[[int], None]] = None,
        text_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Consume a LiteLLM stream into a single text payload."""
        chunks: List[str] = []
        usage: Dict[str, Any] = {}
        chars_received = 0
        next_emit_at = 1

        try:
            for chunk in stream_response:
                chunk_usage = chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)
                normalized_usage = self._normalize_usage(chunk_usage)
                if normalized_usage:
                    usage = normalized_usage

                delta_text = self._extract_stream_text(chunk)
                if not delta_text:
                    continue

                chunks.append(delta_text)
                chars_received += len(delta_text)
                if text_callback:
                    text_callback(delta_text, "".join(chunks))
                if progress_callback and chars_received >= next_emit_at:
                    progress_callback(chars_received)
                    next_emit_at = chars_received + 160
        except Exception as exc:
            raise _LiteLLMStreamError(
                f"{model} stream interrupted: {exc}",
                partial_received=chars_received > 0,
            ) from exc

        response_text = "".join(chunks).strip()
        if not response_text:
            raise _LiteLLMStreamError(
                f"{model} stream returned empty response",
                partial_received=False,
            )

        if progress_callback and chars_received > 0:
            progress_callback(chars_received)

        return response_text, usage
    def _call_litellm(
        self,
        prompt: str,
        generation_config: dict,
        *,
        system_prompt: Optional[str] = None,
        stream: bool = False,
        stream_progress_callback: Optional[Callable[[int], None]] = None,
        stream_text_callback: Optional[Callable[[str, str], None]] = None,
        response_validator: Optional[Callable[[str], None]] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Call LLM via litellm through the Anthropic gateway (single source).

        模型/鉴权统一由 ``ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL`` 决定，无多模型 fallback
        与 Router 路由。thinking/reasoning_effort/temperature 等正交生成参数仍然生效。
        流式失败时自动回退到非流式。所有失败（含响应校验失败）聚合为
        :class:`_AllModelsFailedError`，保留 ``last_response_text/last_model/last_usage``
        以便上游 :meth:`analyze` 用末次文本兜底。

        Args:
            prompt: User prompt text.
            generation_config: Dict with optional keys: temperature, max_output_tokens, max_tokens.
            response_validator: Optional callable that accepts the raw response text and raises
                an exception if the response is unacceptable (e.g. not valid JSON).

        Returns:
            Tuple of (response text, model_used, usage). On success model_used is the full model
            name and usage is a dict with prompt_tokens, completion_tokens, total_tokens.
        """
        config = self._get_runtime_config()
        max_tokens = generation_config.get("max_output_tokens") or generation_config.get("max_tokens") or 8192
        requested_temperature = generation_config.get("temperature", 0.7)

        # 网关单源：缺失即抛错，不回落（与 AI 助手一致）
        llm_cfg = resolve_anthropic_gateway_config()
        model = llm_cfg["model"]

        last_error: Optional[BaseException] = None
        last_response_text: Optional[str] = None
        last_model: Optional[str] = None
        last_usage: Dict[str, Any] = {}
        effective_system_prompt = system_prompt or self.TEXT_SYSTEM_PROMPT

        try:
            call_kwargs = build_litellm_kwargs(
                llm_cfg,
                stream=False,
                messages=[
                    {"role": "system", "content": effective_system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
            )
            call_kwargs = apply_litellm_generation_params(
                call_kwargs,
                model,
                requested_temperature,
            )

            # Inject thinking mode and reasoning effort if configured
            thinking_enabled = getattr(config, "llm_thinking_enabled", False)
            reasoning_effort = getattr(config, "llm_reasoning_effort", "auto")
            if thinking_enabled and reasoning_effort != "auto":
                extra_body = call_kwargs.get("extra_body", {})
                extra_body["reasoning_effort"] = reasoning_effort
                call_kwargs["extra_body"] = extra_body
                logger.debug(
                    "[LiteLLM] Injecting reasoning_effort=%s for model %s (thinking enabled)",
                    reasoning_effort,
                    model,
                )

            _stream_text: Optional[str] = None
            _stream_usage: Dict[str, Any] = {}

            if stream:
                try:
                    stream_response = call_litellm_with_param_recovery(
                        lambda kwargs: self._dispatch_litellm_completion(kwargs),
                        model=model,
                        call_kwargs={**call_kwargs, "stream": True},
                        model_list=None,
                        cache_recovery=False,
                        logger=logger,
                    )
                    with contextlib.closing(stream_response):
                        _stream_text, _stream_usage = self._consume_litellm_stream(
                            stream_response,
                            model=model,
                            progress_callback=stream_progress_callback,
                            text_callback=stream_text_callback,
                        )
                except _LiteLLMStreamError as exc:
                    if exc.partial_received:
                        logger.warning(
                            "[LiteLLM] %s stream failed after partial output, retrying non-stream: %s",
                            model,
                            exc,
                        )
                    else:
                        logger.warning(
                            "[LiteLLM] %s stream unavailable before first chunk, falling back to non-stream: %s",
                            model,
                            exc,
                        )
                    last_error = exc
                except Exception as exc:
                    logger.warning(
                        "[LiteLLM] %s stream request failed before first chunk, falling back to non-stream: %s",
                        model,
                        exc,
                    )
                    last_error = exc

            if _stream_text is not None:
                last_response_text = _stream_text
                last_model = model
                last_usage = _stream_usage
                if response_validator is not None:
                    response_validator(_stream_text)
                return _stream_text, model, _stream_usage

            response = call_litellm_with_param_recovery(
                lambda kwargs: self._dispatch_litellm_completion(kwargs),
                model=model,
                call_kwargs=call_kwargs,
                model_list=None,
                logger=logger,
            )

            content = self._extract_completion_text(response)
            if content:
                usage = self._normalize_usage(self._get_response_field(response, "usage"))
                last_response_text = content
                last_model = model
                last_usage = usage
                if response_validator is not None:
                    response_validator(content)
                return (content, model, usage)
            raise ValueError("LLM returned empty response")

        except Exception as e:
            logger.warning(f"[LiteLLM] {model} failed: {e}")
            last_error = e

        raise _AllModelsFailedError(
            f"LLM call failed for model {model}. Last error: {last_error}",
            last_response_text=last_response_text,
            last_model=last_model,
            last_usage=last_usage,
        )
    def generate_text(
        self,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> Optional[str]:
        """Public entry point for free-form text generation.

        External callers (e.g. MarketAnalyzer) must use this method instead of
        calling _call_litellm() directly.

        Args:
            prompt:      Text prompt to send to the LLM.
            max_tokens:  Maximum tokens in the response (default 2048).
            temperature: Sampling temperature (default 0.7).

        Returns:
            Response text, or None if the LLM call fails (error is logged).
        """
        try:
            result = self._call_litellm(
                prompt,
                generation_config={"max_tokens": max_tokens, "temperature": temperature},
            )
            if isinstance(result, tuple):
                text, model_used, usage = result
                persist_llm_usage(usage, model_used, call_type="analysis")
                return text
            return result
        except Exception as exc:
            logger.error("[generate_text] LLM call failed: %s", exc)
            return None
