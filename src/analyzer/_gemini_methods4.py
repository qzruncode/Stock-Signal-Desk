"""Method group 4 for GeminiAnalyzer."""

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

class _GeminiAnalyzerMethods4:
    def _build_market_snapshot(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """构建当日行情快照（展示用）"""
        today = context.get("today", {}) or {}
        realtime = context.get("realtime", {}) or {}
        yesterday = context.get("yesterday", {}) or {}

        prev_close = yesterday.get("close")
        close = today.get("close")
        high = today.get("high")
        low = today.get("low")

        amplitude = None
        change_amount = None
        if prev_close not in (None, 0) and high is not None and low is not None:
            try:
                amplitude = (float(high) - float(low)) / float(prev_close) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                amplitude = None
        if prev_close is not None and close is not None:
            try:
                change_amount = float(close) - float(prev_close)
            except (TypeError, ValueError):
                change_amount = None

        snapshot = {
            "date": context.get("date", "未知"),
            "close": self._format_price(close),
            "open": self._format_price(today.get("open")),
            "high": self._format_price(high),
            "low": self._format_price(low),
            "prev_close": self._format_price(prev_close),
            "pct_chg": self._format_percent(today.get("pct_chg")),
            "change_amount": self._format_price(change_amount),
            "amplitude": self._format_percent(amplitude),
            "volume": self._format_volume(today.get("volume")),
            "amount": self._format_amount(today.get("amount")),
        }

        if realtime:
            snapshot.update(
                {
                    "price": self._format_price(realtime.get("price")),
                    "volume_ratio": realtime.get("volume_ratio", "N/A"),
                    "turnover_rate": self._format_percent(realtime.get("turnover_rate")),
                    "source": getattr(realtime.get("source"), "value", realtime.get("source", "N/A")),
                }
            )

        return snapshot
    def _check_content_integrity(self, result: AnalysisResult) -> Tuple[bool, List[str]]:
        """Delegate to module-level check_content_integrity."""
        return check_content_integrity(result)
    def _build_integrity_complement_prompt(self, missing_fields: List[str], report_language: str = "zh") -> str:
        """Build complement instruction for missing mandatory fields."""
        report_language = normalize_report_language(report_language)
        if report_language == "en":
            lines = [
                "### Completion requirements: fill the missing mandatory fields below and output the full JSON again:"
            ]
            for f in missing_fields:
                if f == "sentiment_score":
                    lines.append("- sentiment_score: integer score from 0 to 100")
                elif f == "operation_advice":
                    lines.append("- operation_advice: localized action advice")
                elif f == "analysis_summary":
                    lines.append("- analysis_summary: concise analysis summary")
                elif f == "dashboard.core_conclusion.one_sentence":
                    lines.append("- dashboard.core_conclusion.one_sentence: one-line decision")
                elif f == "dashboard.intelligence.risk_alerts":
                    lines.append("- dashboard.intelligence.risk_alerts: risk alert list (can be empty)")
                elif f == "dashboard.battle_plan.sniper_points.stop_loss":
                    lines.append("- dashboard.battle_plan.sniper_points.stop_loss: stop-loss level")
            return "\n".join(lines)

        lines = ["### 补全要求：请在上方分析基础上补充以下必填内容，并输出完整 JSON："]
        for f in missing_fields:
            if f == "sentiment_score":
                lines.append("- sentiment_score: 0-100 综合评分")
            elif f == "operation_advice":
                lines.append("- operation_advice: 买入/加仓/持有/减仓/卖出/观望")
            elif f == "analysis_summary":
                lines.append("- analysis_summary: 综合分析摘要")
            elif f == "dashboard.core_conclusion.one_sentence":
                lines.append("- dashboard.core_conclusion.one_sentence: 一句话决策")
            elif f == "dashboard.intelligence.risk_alerts":
                lines.append("- dashboard.intelligence.risk_alerts: 风险警报列表（可为空数组）")
            elif f == "dashboard.battle_plan.sniper_points.stop_loss":
                lines.append("- dashboard.battle_plan.sniper_points.stop_loss: 止损价")
        return "\n".join(lines)
    def _build_integrity_retry_prompt(
        self,
        base_prompt: str,
        previous_response: str,
        missing_fields: List[str],
        report_language: str = "zh",
    ) -> str:
        """Build retry prompt using the previous response as the complement baseline."""
        complement = self._build_integrity_complement_prompt(missing_fields, report_language=report_language)
        previous_output = previous_response.strip()
        if normalize_report_language(report_language) == "en":
            prefix = "### The previous output is below. Complete the missing fields based on that output and return the full JSON again. Do not omit existing fields:"
        else:
            prefix = "### 上一次输出如下，请在该输出基础上补齐缺失字段，并重新输出完整 JSON。不要省略已有字段："
        return "\n\n".join(
            [
                base_prompt,
                prefix,
                previous_output,
                complement,
            ]
        )
    def _apply_placeholder_fill(self, result: AnalysisResult, missing_fields: List[str]) -> None:
        """Delegate to module-level apply_placeholder_fill."""
        apply_placeholder_fill(result, missing_fields)
    def _parse_response(self, response_text: str, code: str, name: str) -> AnalysisResult:
        """
        解析 Gemini 响应（决策仪表盘版）

        尝试从响应中提取 JSON 格式的分析结果，包含 dashboard 字段
        如果解析失败，尝试智能提取或返回默认结果
        """
        try:
            report_language = normalize_report_language(getattr(self._get_runtime_config(), "report_language", "zh"))
            # 清理响应文本：移除 markdown 代码块标记
            cleaned_text = response_text
            if "```json" in cleaned_text:
                cleaned_text = cleaned_text.replace("```json", "").replace("```", "")
            elif "```" in cleaned_text:
                cleaned_text = cleaned_text.replace("```", "")

            # 尝试找到 JSON 内容
            json_start = cleaned_text.find("{")
            json_end = cleaned_text.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = cleaned_text[json_start:json_end]

                # 尝试修复常见的 JSON 问题
                json_str = self._fix_json_string(json_str)

                data = json.loads(json_str)

                # Schema validation (lenient: on failure, continue with raw dict)
                try:
                    AnalysisReportSchema.model_validate(data)
                except Exception as e:
                    logger.warning(
                        "LLM report schema validation failed, continuing with raw dict: %s",
                        str(e)[:100],
                    )

                # 提取 dashboard 数据
                dashboard = data.get("dashboard", None)

                # 优先使用 AI 返回的股票名称（如果原名称无效或包含代码）
                ai_stock_name = data.get("stock_name")
                if ai_stock_name and (name.startswith("股票") or name == code or "Unknown" in name):
                    name = ai_stock_name

                # 解析所有字段，使用默认值防止缺失
                # 解析 decision_type，如果没有则根据 operation_advice 推断
                decision_type = data.get("decision_type", "")
                if not decision_type:
                    op = data.get("operation_advice", "Hold" if report_language == "en" else "持有")
                    decision_type = infer_decision_type_from_advice(op, default="hold")

                return AnalysisResult(
                    code=code,
                    name=name,
                    # 核心指标
                    sentiment_score=int(data.get("sentiment_score", 50)),
                    trend_prediction=data.get("trend_prediction", "Sideways" if report_language == "en" else "震荡"),
                    operation_advice=data.get("operation_advice", "Hold" if report_language == "en" else "持有"),
                    decision_type=decision_type,
                    confidence_level=localize_confidence_level(
                        data.get("confidence_level", "Medium" if report_language == "en" else "中"),
                        report_language,
                    ),
                    report_language=report_language,
                    # 决策仪表盘
                    dashboard=dashboard,
                    # 走势分析
                    trend_analysis=data.get("trend_analysis", ""),
                    short_term_outlook=data.get("short_term_outlook", ""),
                    medium_term_outlook=data.get("medium_term_outlook", ""),
                    # 技术面
                    technical_analysis=data.get("technical_analysis", ""),
                    ma_analysis=data.get("ma_analysis", ""),
                    volume_analysis=data.get("volume_analysis", ""),
                    pattern_analysis=data.get("pattern_analysis", ""),
                    # 基本面
                    fundamental_analysis=data.get("fundamental_analysis", ""),
                    sector_position=data.get("sector_position", ""),
                    company_highlights=data.get("company_highlights", ""),
                    # 情绪面/消息面
                    news_summary=data.get("news_summary", ""),
                    market_sentiment=data.get("market_sentiment", ""),
                    hot_topics=data.get("hot_topics", ""),
                    # 综合
                    analysis_summary=data.get(
                        "analysis_summary", "Analysis completed" if report_language == "en" else "分析完成"
                    ),
                    key_points=data.get("key_points", ""),
                    risk_warning=data.get("risk_warning", ""),
                    buy_reason=data.get("buy_reason", ""),
                    # 元数据
                    search_performed=data.get("search_performed", False),
                    data_sources=data.get(
                        "data_sources", "Technical data" if report_language == "en" else "技术面数据"
                    ),
                    success=True,
                )
            else:
                # 没有找到 JSON，标记为失败
                logger.warning(f"无法从响应中提取 JSON，标记为解析失败")
                return self._parse_text_response(response_text, code, name)

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}，标记为解析失败")
            return self._parse_text_response(response_text, code, name)
    def _fix_json_string(self, json_str: str) -> str:
        """修复常见的 JSON 格式问题"""
        import re

        # 移除注释
        json_str = re.sub(r"//.*?\n", "\n", json_str)
        json_str = re.sub(r"/\*.*?\*/", "", json_str, flags=re.DOTALL)

        # 修复尾随逗号
        json_str = re.sub(r",\s*}", "}", json_str)
        json_str = re.sub(r",\s*]", "]", json_str)

        # 确保布尔值是小写
        json_str = json_str.replace("True", "true").replace("False", "false")

        # fix by json-repair
        json_str = repair_json(json_str)

        return json_str
    def _validate_json_response(self, text: str) -> None:
        """Validate that *text* contains a parseable JSON object.

        Used as the ``response_validator`` argument to :meth:`_call_litellm` so
        that a JSON-less or unparseable reply from the primary model is treated
        as a model failure and triggers fallback to the next configured model.

        Raises:
            ValueError: if no JSON object is found in *text*.
            json.JSONDecodeError: if the extracted JSON cannot be parsed (after
                :meth:`_fix_json_string` attempts repair).
        """
        cleaned = text
        if "```json" in cleaned:
            cleaned = cleaned.replace("```json", "").replace("```", "")
        elif "```" in cleaned:
            cleaned = cleaned.replace("```", "")

        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}") + 1

        if json_start < 0 or json_end <= json_start:
            raise ValueError("No JSON object found in LLM response")

        json_str = cleaned[json_start:json_end]
        json_str = self._fix_json_string(json_str)
        json.loads(json_str)
    def _parse_text_response(self, response_text: str, code: str, name: str) -> AnalysisResult:
        """Preserve an unstructured fallback without guessing its semantics."""
        report_language = normalize_report_language(getattr(self._get_runtime_config(), "report_language", "zh"))
        sentiment_score = 50
        trend = "Sideways" if report_language == "en" else "震荡"
        advice = "Hold" if report_language == "en" else "持有"
        decision_type = "hold"

        # 截取前500字符作为摘要
        summary = (
            response_text[:500]
            if response_text
            else ("No analysis result" if report_language == "en" else "无分析结果")
        )

        return AnalysisResult(
            code=code,
            name=name,
            sentiment_score=sentiment_score,
            trend_prediction=trend,
            operation_advice=advice,
            decision_type=decision_type,
            confidence_level="Low" if report_language == "en" else "低",
            analysis_summary=summary,
            key_points=(
                "JSON parsing failed; treat this as best-effort output."
                if report_language == "en"
                else "JSON解析失败，仅供参考"
            ),
            risk_warning=(
                "The result may be inaccurate. Cross-check with other information."
                if report_language == "en"
                else "分析结果可能不准确，建议结合其他信息判断"
            ),
            raw_response=response_text,
            success=False,
            error_message="LLM response is not valid JSON; analysis result will not be persisted",
            report_language=report_language,
        )
    def batch_analyze(self, contexts: List[Dict[str, Any]], delay_between: float = 2.0) -> List[AnalysisResult]:
        """
        批量分析多只股票

        注意：为避免 API 速率限制，每次分析之间会有延迟

        Args:
            contexts: 上下文数据列表
            delay_between: 每次分析之间的延迟（秒）

        Returns:
            AnalysisResult 列表
        """
        results = []

        for i, context in enumerate(contexts):
            if i > 0:
                logger.debug(f"等待 {delay_between} 秒后继续...")
                time.sleep(delay_between)

            result = self.analyze(context)
            results.append(result)

        return results
