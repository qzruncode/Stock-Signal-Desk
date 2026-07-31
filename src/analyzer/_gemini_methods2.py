"""Method group 2 for GeminiAnalyzer."""

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

class _GeminiAnalyzerMethods2:
    def analyze(
        self,
        context: Dict[str, Any],
        news_context: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        stream_progress_callback: Optional[Callable[[int], None]] = None,
    ) -> AnalysisResult:
        """
        分析单只股票

        流程：
        1. 格式化输入数据（技术面 + 新闻）
        2. 调用 Gemini API（带重试和模型切换）
        3. 解析 JSON 响应
        4. 返回结构化结果

        Args:
            context: 从 storage.get_analysis_context() 获取的上下文数据
            news_context: 预先搜索的新闻内容（可选）

        Returns:
            AnalysisResult 对象
        """

        def _emit_progress(progress: int, message: str) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(progress, message)
            except Exception as exc:
                logger.debug("[analyzer] progress callback skipped: %s", exc)

        code = context.get("code", "Unknown")
        config = self._get_runtime_config()
        report_language = normalize_report_language(getattr(config, "report_language", "zh"))
        system_prompt = self._get_analysis_system_prompt(report_language, stock_code=code)

        # 请求前增加延时（防止连续请求触发限流）
        request_delay = config.gemini_request_delay
        if request_delay > 0:
            logger.debug(f"[LLM] 请求前等待 {request_delay:.1f} 秒...")
            _emit_progress(65, f"{code}：LLM 请求前等待 {request_delay:.1f} 秒")
            time.sleep(request_delay)

        # 优先从上下文获取股票名称（由 main.py 传入）
        name = context.get("stock_name")
        if not name or name.startswith("股票"):
            # 备选：从 realtime 中获取
            if "realtime" in context and context["realtime"].get("name"):
                name = context["realtime"]["name"]
            else:
                # 最后从映射表获取
                name = STOCK_NAME_MAP.get(code, f"股票{code}")

        # 如果模型不可用，返回默认结果
        if not self.is_available():
            return AnalysisResult(
                code=code,
                name=name,
                sentiment_score=50,
                trend_prediction="Sideways" if report_language == "en" else "震荡",
                operation_advice="Hold" if report_language == "en" else "持有",
                confidence_level="Low" if report_language == "en" else "低",
                analysis_summary=(
                    "AI analysis is unavailable because no API key is configured."
                    if report_language == "en"
                    else "AI 分析功能未启用（未配置 API Key）"
                ),
                risk_warning=(
                    "Configure the Anthropic gateway (ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL) and retry."
                    if report_language == "en"
                    else "请配置 Anthropic 网关（ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL）后重试"
                ),
                success=False,
                error_message="LLM API key is not configured" if report_language == "en" else "LLM API Key 未配置",
                model_used=None,
                report_language=report_language,
            )

        try:
            # 格式化输入（包含技术面数据和新闻）
            prompt = self._format_prompt(context, name, news_context, report_language=report_language)

            config = self._get_runtime_config()
            import os as _os

            model_name = (_os.getenv("ANTHROPIC_MODEL") or "").strip() or "unknown"
            logger.info(f"========== AI 分析 {name}({code}) ==========")
            logger.info(f"[LLM配置] 模型: {model_name}")
            logger.info(f"[LLM配置] Prompt 长度: {len(prompt)} 字符")
            logger.info(f"[LLM配置] 是否包含新闻: {'是' if news_context else '否'}")

            # 记录完整 prompt 到日志（INFO级别记录摘要，DEBUG记录完整）
            prompt_preview = prompt[:500] + "..." if len(prompt) > 500 else prompt
            logger.info(f"[LLM Prompt 预览]\n{prompt_preview}")
            logger.debug(f"=== 完整 Prompt ({len(prompt)}字符) ===\n{prompt}\n=== End Prompt ===")

            # 设置生成配置
            generation_config = {
                "temperature": config.llm_temperature,
                "max_output_tokens": 8192,
            }

            logger.info(f"[LLM调用] 开始调用 {model_name}...")
            _emit_progress(68, f"{name}：LLM 已接收请求，等待响应")

            # 使用 litellm 调用（支持完整性校验重试）
            current_prompt = prompt
            retry_count = 0
            max_retries = config.report_integrity_retry if config.report_integrity_enabled else 0

            while True:
                start_time = time.time()
                try:
                    response_text, model_used, llm_usage = self._call_litellm(
                        current_prompt,
                        generation_config,
                        system_prompt=system_prompt,
                        stream=True,
                        stream_progress_callback=stream_progress_callback,
                        response_validator=self._validate_json_response,
                    )
                except _AllModelsFailedError as exc:
                    if exc.last_response_text is not None:
                        logger.warning(
                            "[LLM JSON] %s(%s): all models returned invalid JSON, using text fallback",
                            name,
                            code,
                        )
                        response_text = exc.last_response_text
                        model_used = exc.last_model
                        llm_usage = exc.last_usage
                    else:
                        raise
                elapsed = time.time() - start_time

                # 记录响应信息
                logger.info(f"[LLM返回] {model_name} 响应成功, 耗时 {elapsed:.2f}s, 响应长度 {len(response_text)} 字符")
                response_preview = response_text[:300] + "..." if len(response_text) > 300 else response_text
                logger.info(f"[LLM返回 预览]\n{response_preview}")
                logger.debug(
                    f"=== {model_name} 完整响应 ({len(response_text)}字符) ===\n{response_text}\n=== End Response ==="
                )
                # Keep parser/retry progress monotonic so task progress/message never "goes backward".
                parse_progress = min(99, 93 + retry_count * 2)
                _emit_progress(parse_progress, f"{name}：LLM 返回完成，正在解析 JSON")

                # 解析响应
                result = self._parse_response(response_text, code, name)
                result.raw_response = response_text
                result.search_performed = bool(news_context)
                result.market_snapshot = self._build_market_snapshot(context)
                result.model_used = model_used
                result.report_language = report_language

                # 内容完整性校验（可选）
                if not config.report_integrity_enabled:
                    break
                pass_integrity, missing_fields = self._check_content_integrity(result)
                if pass_integrity:
                    break
                if retry_count < max_retries:
                    current_prompt = self._build_integrity_retry_prompt(
                        prompt,
                        response_text,
                        missing_fields,
                        report_language=report_language,
                    )
                    retry_count += 1
                    logger.info(
                        "[LLM完整性] 必填字段缺失 %s，第 %d 次补全重试",
                        missing_fields,
                        retry_count,
                    )
                    retry_progress = min(99, 92 + retry_count * 2)
                    _emit_progress(
                        retry_progress,
                        f"{name}：报告字段不完整，正在补全重试（{retry_count}/{max_retries}）",
                    )
                else:
                    self._apply_placeholder_fill(result, missing_fields)
                    logger.warning(
                        "[LLM完整性] 必填字段缺失 %s，已占位补全，不阻塞流程",
                        missing_fields,
                    )
                    break

            persist_llm_usage(llm_usage, model_used, call_type="analysis", stock_code=code)

            logger.info(f"[LLM解析] {name}({code}) 分析完成: {result.trend_prediction}, 评分 {result.sentiment_score}")

            return result

        except Exception as e:
            logger.error(f"AI 分析 {name}({code}) 失败: {e}")
            return AnalysisResult(
                code=code,
                name=name,
                sentiment_score=50,
                trend_prediction="Sideways" if report_language == "en" else "震荡",
                operation_advice="Hold" if report_language == "en" else "持有",
                confidence_level="Low" if report_language == "en" else "低",
                analysis_summary=(
                    f"Analysis failed: {str(e)[:100]}" if report_language == "en" else f"分析过程出错: {str(e)[:100]}"
                ),
                risk_warning=(
                    "Analysis failed. Please retry later or review manually."
                    if report_language == "en"
                    else "分析失败，请稍后重试或手动分析"
                ),
                success=False,
                error_message=str(e),
                model_used=None,
                report_language=report_language,
            )
