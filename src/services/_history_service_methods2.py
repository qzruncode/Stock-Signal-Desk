"""HistoryService method group 2."""

from __future__ import annotations

from src.services.history_service import (
    json,
    logging,
    date,
    datetime,
    timedelta,
    Optional,
    Dict,
    Any,
    List,
    Tuple,
    TYPE_CHECKING,
    get_config,
    resolve_news_window_days,
    get_bias_status_emoji,
    get_localized_stock_name,
    get_report_labels,
    get_signal_level,
    localize_bias_status,
    localize_chip_health,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
    DatabaseManager,
    normalize_model_used,
    parse_json_field,
    logger,
    MarkdownReportGenerationError,
 )

class _HistoryServiceMethods2:
    def get_markdown_report(self, record_id: str) -> Optional[str]:
        """
        Generate a Markdown report for a single analysis history record.

        This method reconstructs an AnalysisResult from the stored raw_result
        and generates a detailed Markdown report similar to the push notifications.

        Args:
            record_id: integer PK (as string) or query_id string

        Returns:
            Markdown formatted report string, or None if record not found

        Raises:
            MarkdownReportGenerationError: If report generation fails due to internal errors
        """
        record = self._resolve_record(record_id)
        if not record:
            logger.warning(f"get_markdown_report: record not found for {record_id}")
            return None

        # Rebuild AnalysisResult from raw_result
        raw_result = parse_json_field(record.raw_result)
        if not raw_result:
            logger.error(f"get_markdown_report: raw_result is empty for {record_id}")
            raise MarkdownReportGenerationError(
                f"raw_result is empty or invalid for record {record_id}", record_id=record_id
            )

        if getattr(record, "report_type", None) == "market_review":
            markdown_report = self._extract_market_review_content(record, raw_result)
            if markdown_report:
                return markdown_report
            logger.error(f"get_markdown_report: market review report is empty for {record_id}")
            raise MarkdownReportGenerationError(
                f"market review report is empty for record {record_id}",
                record_id=record_id,
            )

        try:
            result = self._rebuild_analysis_result(raw_result, record)
        except Exception as e:
            logger.error(f"get_markdown_report: failed to rebuild AnalysisResult for {record_id}: {e}", exc_info=True)
            raise MarkdownReportGenerationError(
                f"Failed to rebuild AnalysisResult: {str(e)}", record_id=record_id
            ) from e

        if not result:
            logger.error(f"get_markdown_report: _rebuild_analysis_result returned None for {record_id}")
            raise MarkdownReportGenerationError(
                f"Failed to rebuild AnalysisResult from raw_result", record_id=record_id
            )

        # Generate Markdown report
        try:
            return self._generate_single_stock_markdown(result, record)
        except Exception as e:
            logger.error(f"get_markdown_report: failed to generate markdown for {record_id}: {e}", exc_info=True)
            raise MarkdownReportGenerationError(
                f"Failed to generate markdown report: {str(e)}", record_id=record_id
            ) from e
    def _rebuild_analysis_result(self, raw_result: Dict[str, Any], record) -> Optional[AnalysisResult]:
        """
        Rebuild an AnalysisResult object from stored raw_result dict.

        Args:
            raw_result: The parsed raw_result JSON dict
            record: The AnalysisHistory ORM record

        Returns:
            AnalysisResult object or None
        """
        try:
            from src.analyzer import AnalysisResult

            # Extract dashboard data if available
            dashboard = raw_result.get("dashboard", {})

            # Build AnalysisResult with available data
            return AnalysisResult(
                code=raw_result.get("code", record.code),
                name=raw_result.get("name", record.name),
                sentiment_score=raw_result.get("sentiment_score", record.sentiment_score or 50),
                trend_prediction=raw_result.get("trend_prediction", record.trend_prediction or ""),
                operation_advice=raw_result.get("operation_advice", record.operation_advice or ""),
                decision_type=raw_result.get("decision_type", "hold"),
                confidence_level=raw_result.get("confidence_level", "中"),
                report_language=normalize_report_language(raw_result.get("report_language")),
                dashboard=dashboard,
                trend_analysis=raw_result.get("trend_analysis", ""),
                short_term_outlook=raw_result.get("short_term_outlook", ""),
                medium_term_outlook=raw_result.get("medium_term_outlook", ""),
                technical_analysis=raw_result.get("technical_analysis", ""),
                ma_analysis=raw_result.get("ma_analysis", ""),
                volume_analysis=raw_result.get("volume_analysis", ""),
                pattern_analysis=raw_result.get("pattern_analysis", ""),
                fundamental_analysis=raw_result.get("fundamental_analysis", ""),
                sector_position=raw_result.get("sector_position", ""),
                company_highlights=raw_result.get("company_highlights", ""),
                news_summary=raw_result.get("news_summary", record.news_content or ""),
                market_sentiment=raw_result.get("market_sentiment", ""),
                hot_topics=raw_result.get("hot_topics", ""),
                analysis_summary=raw_result.get("analysis_summary", record.analysis_summary or ""),
                key_points=raw_result.get("key_points", ""),
                risk_warning=raw_result.get("risk_warning", ""),
                buy_reason=raw_result.get("buy_reason", ""),
                market_snapshot=raw_result.get("market_snapshot"),
                search_performed=raw_result.get("search_performed", False),
                data_sources=raw_result.get("data_sources", ""),
                success=raw_result.get("success", True),
                error_message=raw_result.get("error_message"),
                current_price=raw_result.get("current_price"),
                change_pct=raw_result.get("change_pct"),
                model_used=raw_result.get("model_used"),
            )
        except Exception as e:
            logger.error(f"Failed to rebuild AnalysisResult: {e}", exc_info=True)
            return None
    def _generate_single_stock_markdown(self, result: AnalysisResult, record) -> str:
        """
        Generate a Markdown report for a single stock analysis.

        This follows the same Markdown conventions as the current notification output.
        using dashboard structured data for detailed report.

        Args:
            result: The AnalysisResult object
            record: The AnalysisHistory ORM record

        Returns:
            Markdown formatted report string
        """
        report_date = (
            record.created_at.strftime("%Y-%m-%d") if record.created_at else datetime.now().strftime("%Y-%m-%d")
        )
        report_time = (
            record.created_at.strftime("%H:%M:%S") if record.created_at else datetime.now().strftime("%H:%M:%S")
        )
        report_language = normalize_report_language(getattr(result, "report_language", "zh"))
        labels = get_report_labels(report_language)
        analysis_date_label = "Analysis Date" if report_language == "en" else "分析日期"
        report_time_label = "Report Time" if report_language == "en" else "报告生成时间"
        reason_label = "Rationale" if report_language == "en" else "操作理由"
        risk_warning_label = "Risk Warning" if report_language == "en" else "风险提示"
        technical_heading = "Technicals" if report_language == "en" else "技术面"
        ma_label = "Moving Averages" if report_language == "en" else "均线"
        volume_analysis_label = "Volume" if report_language == "en" else "量能"
        news_heading = "News Flow" if report_language == "en" else "消息面"

        # Escape markdown special characters in stock name
        name_escaped = (
            self._escape_md(get_localized_stock_name(result.name, result.code, report_language)) or result.code
        )

        # Get signal level
        signal_text, signal_emoji, signal_tag = self._get_signal_level(result)
        dashboard = result.dashboard if hasattr(result, "dashboard") and result.dashboard else {}

        report_lines = [
            f"# 📊 {name_escaped} ({result.code}) {labels['report_title']}",
            "",
            f"> {analysis_date_label}: **{report_date}** | {report_time_label}: {report_time}",
            "",
            "---",
            "",
        ]

        # ========== 舆情与基本面概览（放在最前面）==========
        intel = dashboard.get("intelligence", {}) if dashboard else {}
        if intel:
            report_lines.extend(
                [
                    f"### 📰 {labels['info_heading']}",
                    "",
                ]
            )
            # 舆情情绪总结
            if intel.get("sentiment_summary"):
                report_lines.append(f"**💭 {labels['sentiment_summary_label']}**: {intel['sentiment_summary']}")
            # 业绩预期
            if intel.get("earnings_outlook"):
                report_lines.append(f"**📊 {labels['earnings_outlook_label']}**: {intel['earnings_outlook']}")
            # 风险警报（醒目显示）
            risk_alerts = intel.get("risk_alerts", [])
            if risk_alerts:
                report_lines.append("")
                report_lines.append(f"**🚨 {labels['risk_alerts_label']}**:")
                for alert in risk_alerts:
                    report_lines.append(f"- {alert}")
            # 利好催化
            catalysts = intel.get("positive_catalysts", [])
            if catalysts:
                report_lines.append("")
                report_lines.append(f"**✨ {labels['positive_catalysts_label']}**:")
                for cat in catalysts:
                    report_lines.append(f"- {cat}")
            # 最新消息
            if intel.get("latest_news"):
                report_lines.append("")
                report_lines.append(f"**📢 {labels['latest_news_label']}**: {intel['latest_news']}")
            report_lines.append("")

        # ========== 核心结论 ==========
        core = dashboard.get("core_conclusion", {}) if dashboard else {}
        one_sentence = core.get("one_sentence", result.analysis_summary)
        time_sense = core.get("time_sensitivity", labels["default_time_sensitivity"])
        pos_advice = core.get("position_advice", {})

        report_lines.extend(
            [
                f"### 📌 {labels['core_conclusion_heading']}",
                "",
                f"**{signal_emoji} {signal_text}** | {localize_trend_prediction(result.trend_prediction, report_language)}",
                "",
                f"> **{labels['one_sentence_label']}**: {one_sentence}",
                "",
                f"⏰ **{labels['time_sensitivity_label']}**: {time_sense}",
                "",
            ]
        )
        # 持仓分类建议
        if pos_advice:
            report_lines.extend(
                [
                    f"| {labels['position_status_label']} | {labels['action_advice_label']} |",
                    "|---------|---------|",
                    f"| 🆕 **{labels['no_position_label']}** | {pos_advice.get('no_position', localize_operation_advice(result.operation_advice, report_language))} |",
                    f"| 💼 **{labels['has_position_label']}** | {pos_advice.get('has_position', labels['continue_holding'])} |",
                    "",
                ]
            )

        # ========== 行情快照 ==========
        self._append_market_snapshot_to_report(report_lines, result, labels)

        # ========== 数据透视 ==========
        data_persp = dashboard.get("data_perspective", {}) if dashboard else {}
        if data_persp:
            trend_data = data_persp.get("trend_status", {})
            price_data = data_persp.get("price_position", {})
            vol_data = data_persp.get("volume_analysis", {})
            chip_data = data_persp.get("chip_structure", {})

            report_lines.extend(
                [
                    f"### 📊 {labels['data_perspective_heading']}",
                    "",
                ]
            )
            # 趋势状态
            if trend_data:
                is_bullish = (
                    f"✅ {labels['yes_label']}" if trend_data.get("is_bullish", False) else f"❌ {labels['no_label']}"
                )
                report_lines.extend(
                    [
                        f"**{labels['ma_alignment_label']}**: {trend_data.get('ma_alignment', 'N/A')} | "
                        f"{labels['bullish_alignment_label']}: {is_bullish} | "
                        f"{labels['trend_strength_label']}: {trend_data.get('trend_score', 'N/A')}/100",
                        "",
                    ]
                )
            # 价格位置
            if price_data:
                raw_bias_status = price_data.get("bias_status", "N/A")
                bias_status = localize_bias_status(raw_bias_status, report_language)
                bias_emoji = get_bias_status_emoji(raw_bias_status)
                report_lines.extend(
                    [
                        f"| {labels['price_metrics_label']} | {labels['current_price_label']} |",
                        "|---------|------|",
                        f"| {labels['current_price_label']} | {price_data.get('current_price', 'N/A')} |",
                        f"| {labels['ma5_label']} | {price_data.get('ma5', 'N/A')} |",
                        f"| {labels['ma10_label']} | {price_data.get('ma10', 'N/A')} |",
                        f"| {labels['ma20_label']} | {price_data.get('ma20', 'N/A')} |",
                        f"| {labels['bias_ma5_label']} | {price_data.get('bias_ma5', 'N/A')}% {bias_emoji}{bias_status} |",
                        f"| {labels['support_level_label']} | {price_data.get('support_level', 'N/A')} |",
                        f"| {labels['resistance_level_label']} | {price_data.get('resistance_level', 'N/A')} |",
                        "",
                    ]
                )
            # 量能分析
            if vol_data:
                report_lines.extend(
                    [
                        f"**{labels['volume_label']}**: {labels['volume_ratio_label']} {vol_data.get('volume_ratio', 'N/A')} "
                        f"({vol_data.get('volume_status', '')}) | {labels['turnover_rate_label']} {vol_data.get('turnover_rate', 'N/A')}%",
                        f"💡 *{vol_data.get('volume_meaning', '')}*",
                        "",
                    ]
                )
            # 筹码结构
            if chip_data:
                raw_chip_health = chip_data.get("chip_health", "N/A")
                chip_health = localize_chip_health(raw_chip_health, report_language)
                normalized_chip_health = str(raw_chip_health or "").strip().lower()
                if normalized_chip_health in {"健康", "healthy"}:
                    chip_emoji = "✅"
                elif normalized_chip_health in {"一般", "average"}:
                    chip_emoji = "⚠️"
                else:
                    chip_emoji = "🚨"
                report_lines.extend(
                    [
                        f"**{labels['chip_label']}**: {chip_data.get('profit_ratio', 'N/A')} | {chip_data.get('avg_cost', 'N/A')} | "
                        f"{chip_data.get('concentration', 'N/A')} {chip_emoji}{chip_health}",
                        "",
                    ]
                )

        # ========== 作战计划 ==========
        battle = dashboard.get("battle_plan", {}) if dashboard else {}
        if battle:
            report_lines.extend(
                [
                    f"### 🎯 {labels['battle_plan_heading']}",
                    "",
                ]
            )
            # 狙击点位
            sniper = battle.get("sniper_points", {})
            if sniper:
                report_lines.extend(
                    [
                        f"**📍 {labels['action_points_heading']}**",
                        "",
                        f"| {labels['action_points_heading']} | {labels['current_price_label']} |",
                        "|---------|------|",
                        f"| 🎯 {labels['ideal_buy_label']} | {self._clean_sniper_value(sniper.get('ideal_buy', 'N/A'))} |",
                        f"| 🔵 {labels['secondary_buy_label']} | {self._clean_sniper_value(sniper.get('secondary_buy', 'N/A'))} |",
                        f"| 🛑 {labels['stop_loss_label']} | {self._clean_sniper_value(sniper.get('stop_loss', 'N/A'))} |",
                        f"| 🎊 {labels['take_profit_label']} | {self._clean_sniper_value(sniper.get('take_profit', 'N/A'))} |",
                        "",
                    ]
                )
            # 仓位策略
            position = battle.get("position_strategy", {})
            if position:
                report_lines.extend(
                    [
                        f"**💰 {labels['suggested_position_label']}**: {position.get('suggested_position', 'N/A')}",
                        f"- {labels['entry_plan_label']}: {position.get('entry_plan', 'N/A')}",
                        f"- {labels['risk_control_label']}: {position.get('risk_control', 'N/A')}",
                        "",
                    ]
                )
            # 检查清单
            checklist = battle.get("action_checklist", []) if battle else []
            if checklist:
                report_lines.extend(
                    [
                        f"**✅ {labels['checklist_heading']}**",
                        "",
                    ]
                )
                for item in checklist:
                    report_lines.append(f"- {item}")
                report_lines.append("")

        # ========== 如果没有 dashboard，显示传统格式 ==========
        if not dashboard:
            # 操作理由
            if result.buy_reason:
                report_lines.extend(
                    [
                        f"**💡 {reason_label}**: {result.buy_reason}",
                        "",
                    ]
                )
            # 风险提示
            if result.risk_warning:
                report_lines.extend(
                    [
                        f"**⚠️ {risk_warning_label}**: {result.risk_warning}",
                        "",
                    ]
                )
            # 技术面分析
            if result.ma_analysis or result.volume_analysis:
                report_lines.extend(
                    [
                        f"### 📊 {technical_heading}",
                        "",
                    ]
                )
                if result.ma_analysis:
                    report_lines.append(f"**{ma_label}**: {result.ma_analysis}")
                if result.volume_analysis:
                    report_lines.append(f"**{volume_analysis_label}**: {result.volume_analysis}")
                report_lines.append("")
            # 消息面
            if result.news_summary:
                report_lines.extend(
                    [
                        f"### 📰 {news_heading}",
                        f"{result.news_summary}",
                        "",
                    ]
                )

        # ========== 底部 ==========
        report_lines.extend(
            [
                "---",
                "",
                f"*{labels['generated_at_label']}: {report_time}*",
            ]
        )

        return "\n".join(report_lines)
    @staticmethod
    def _escape_md(text: Optional[str]) -> str:
        """Escape markdown special characters."""
        if not text:
            return ""
        return text.replace("*", r"\*")
    @staticmethod
    def _clean_sniper_value(value: Any) -> str:
        """Clean sniper point value for display."""
        if value is None:
            return "N/A"
        text = str(value).strip()
        if not text or text in ("-", "—", "N/A", "None"):
            return "N/A"
        return text
    def _get_signal_level(self, result: AnalysisResult) -> Tuple[str, str, str]:
        """Get signal level based on sentiment score and decision type."""
        return get_signal_level(
            result.operation_advice,
            result.sentiment_score,
            getattr(result, "report_language", "zh"),
        )
