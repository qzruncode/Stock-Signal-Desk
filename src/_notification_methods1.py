"""NotificationService method group 1."""

from __future__ import annotations

from src.notification import (
    logging,
    datetime,
    List,
    Dict,
    Any,
    Optional,
    Tuple,
    TYPE_CHECKING,
    Enum,
    Config,
    get_config,
    ReportType,
    get_localized_stock_name,
    get_report_labels,
    get_signal_level,
    localize_chip_health,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
    normalize_model_used,
    WechatSender,
    WECHAT_IMAGE_MAX_BYTES,
    logger,
    NotificationChannel,
    ChannelDetector,
 )

class _NotificationServiceMethods1:
    def __init__(self):
        config = get_config()
        self._config = config

        self._markdown_to_image_max_chars = getattr(config, "markdown_to_image_max_chars", 15000)
        self._report_summary_only = getattr(config, "report_summary_only", False)
        self._report_show_llm_model = getattr(config, "report_show_llm_model", True)
        self._history_compare_cache: Dict[Tuple[int, Tuple[Tuple[str, str], ...]], Dict[str, List[Dict[str, Any]]]] = {}

        WechatSender.__init__(self, config)
    def is_available(self) -> bool:
        """检查企业微信通知渠道是否已配置。"""
        return bool(self._config.wechat_webhook_url)
    def _normalize_report_type(self, report_type: Any) -> ReportType:
        if isinstance(report_type, ReportType):
            return report_type
        return ReportType.from_str(report_type)
    def _get_report_language(self, payload: Optional[Any] = None) -> str:
        if isinstance(payload, list):
            for item in payload:
                language = getattr(item, "report_language", None)
                if language:
                    return normalize_report_language(language)
        elif payload is not None:
            language = getattr(payload, "report_language", None)
            if language:
                return normalize_report_language(language)
        return normalize_report_language(getattr(get_config(), "report_language", "zh"))
    def _get_labels(self, payload: Optional[Any] = None) -> Dict[str, str]:
        return get_report_labels(self._get_report_language(payload))
    def _get_display_name(self, result: AnalysisResult, language: Optional[str] = None) -> str:
        report_language = normalize_report_language(language or self._get_report_language(result))
        return self._escape_md(get_localized_stock_name(result.name, result.code, report_language))
    def _get_history_compare_context(self, results: List[AnalysisResult]) -> Dict[str, Any]:
        config = get_config()
        history_compare_n = getattr(config, "report_history_compare_n", 0)
        if history_compare_n <= 0 or not results:
            return {"history_by_code": {}}

        cache_key = (
            history_compare_n,
            tuple(sorted((r.code, getattr(r, "query_id", "") or "") for r in results)),
        )
        if cache_key in self._history_compare_cache:
            return {"history_by_code": self._history_compare_cache[cache_key]}

        history_by_code = {}

        self._history_compare_cache[cache_key] = history_by_code
        return {"history_by_code": history_by_code}
    def generate_aggregate_report(
        self,
        results: List[AnalysisResult],
        report_type: Any,
        report_date: Optional[str] = None,
    ) -> str:
        normalized_type = self._normalize_report_type(report_type)
        if normalized_type == ReportType.BRIEF:
            return self.generate_brief_report(results, report_date=report_date)
        return self.generate_dashboard_report(results, report_date=report_date)
    def _collect_models_used(self, results: List[AnalysisResult]) -> List[str]:
        if not self._should_show_llm_model():
            return []
        models: List[str] = []
        for result in results:
            model = normalize_model_used(getattr(result, "model_used", None))
            if model:
                models.append(model)
        return list(dict.fromkeys(models))
    def _should_show_llm_model(self) -> bool:
        return bool(getattr(self._config, "report_show_llm_model", self._report_show_llm_model))
    @staticmethod
    def _escape_md(name: str) -> str:
        return name.replace("*", r"\*") if name else name
    @staticmethod
    def _clean_sniper_value(value: Any) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, (int, float)):
            return str(value)
        if not isinstance(value, str):
            return str(value)
        if not value or value == "N/A":
            return value
        prefixes = [
            "理想买入点：",
            "次优买入点：",
            "止损位：",
            "目标位：",
            "理想买入点:",
            "次优买入点:",
            "止损位:",
            "目标位:",
            "Ideal Entry:",
            "Secondary Entry:",
            "Stop Loss:",
            "Target:",
        ]
        for prefix in prefixes:
            if value.startswith(prefix):
                return value[len(prefix) :]
        return value
    def _get_signal_level(self, result: AnalysisResult) -> tuple:
        return get_signal_level(
            result.operation_advice,
            result.sentiment_score,
            self._get_report_language(result),
        )
    def _get_source_display_name(self, source: Any, language: Optional[str]) -> str:
        raw_source = str(source or "N/A")
        mapping = self._SOURCE_DISPLAY_NAMES.get(raw_source)
        if not mapping:
            return raw_source
        return mapping[normalize_report_language(language)]
    def _append_market_snapshot(self, lines: List[str], result: AnalysisResult) -> None:
        snapshot = getattr(result, "market_snapshot", None)
        if not snapshot:
            return

        report_language = self._get_report_language(result)
        labels = get_report_labels(report_language)

        lines.extend(
            [
                f"### 📈 {labels['market_snapshot_heading']}",
                "",
                f"| {labels['close_label']} | {labels['prev_close_label']} | {labels['open_label']} | {labels['high_label']} | {labels['low_label']} | {labels['change_pct_label']} | {labels['change_amount_label']} | {labels['amplitude_label']} | {labels['volume_label']} | {labels['amount_label']} |",
                "|------|------|------|------|------|-------|-------|------|--------|--------|",
                f"| {snapshot.get('close', 'N/A')} | {snapshot.get('prev_close', 'N/A')} | "
                f"{snapshot.get('open', 'N/A')} | {snapshot.get('high', 'N/A')} | "
                f"{snapshot.get('low', 'N/A')} | {snapshot.get('pct_chg', 'N/A')} | "
                f"{snapshot.get('change_amount', 'N/A')} | {snapshot.get('amplitude', 'N/A')} | "
                f"{snapshot.get('volume', 'N/A')} | {snapshot.get('amount', 'N/A')} |",
            ]
        )

        if "price" in snapshot:
            display_source = self._get_source_display_name(snapshot.get("source", "N/A"), report_language)
            lines.extend(
                [
                    "",
                    f"| {labels['current_price_label']} | {labels['volume_ratio_label']} | {labels['turnover_rate_label']} | {labels['source_label']} |",
                    "|-------|------|--------|----------|",
                    f"| {snapshot.get('price', 'N/A')} | {snapshot.get('volume_ratio', 'N/A')} | "
                    f"{snapshot.get('turnover_rate', 'N/A')} | {display_source} |",
                ]
            )

        lines.append("")
    def generate_daily_report(self, results: List[AnalysisResult], report_date: Optional[str] = None) -> str:
        """
        生成 Markdown 格式的日报（详细版）

        Args:
            results: 分析结果列表
            report_date: 报告日期（默认今天）

        Returns:
            Markdown 格式的日报内容
        """
        if report_date is None:
            report_date = datetime.now().strftime("%Y-%m-%d")
        report_language = self._get_report_language(results)
        labels = get_report_labels(report_language)

        # 标题
        report_lines = [
            f"# 📅 {report_date} {labels['report_title']}",
            "",
            f"> {labels['analyzed_prefix']} **{len(results)}** {labels['stock_unit']} | "
            f"{labels['generated_at_label']}：{datetime.now().strftime('%H:%M:%S')}",
            "",
            "---",
            "",
        ]

        # 按评分排序（高分在前）
        sorted_results = sorted(results, key=lambda x: x.sentiment_score, reverse=True)

        # 统计信息 - 使用 decision_type 字段准确统计
        buy_count = sum(1 for r in results if getattr(r, "decision_type", "") == "buy")
        sell_count = sum(1 for r in results if getattr(r, "decision_type", "") == "sell")
        hold_count = sum(1 for r in results if getattr(r, "decision_type", "") in ("hold", ""))
        avg_score = sum(r.sentiment_score for r in results) / len(results) if results else 0

        report_lines.extend(
            [
                f"## 📊 {labels['summary_heading']}",
                "",
                "| 指标 | 数值 |",
                "|------|------|",
                f"| 🟢 {labels['buy_label']} | **{buy_count}** {labels['stock_unit_compact']} |",
                f"| 🟡 {labels['watch_label']} | **{hold_count}** {labels['stock_unit_compact']} |",
                f"| 🔴 {labels['sell_label']} | **{sell_count}** {labels['stock_unit_compact']} |",
                f"| 📈 {labels['avg_score_label']} | **{avg_score:.1f}** |",
                "",
                "---",
                "",
            ]
        )

        # Issue #262: summary_only 时仅输出摘要，跳过个股详情
        if self._report_summary_only:
            report_lines.extend([f"## 📊 {labels['summary_heading']}", ""])
            for r in sorted_results:
                _, emoji, _ = self._get_signal_level(r)
                report_lines.append(
                    f"{emoji} **{self._get_display_name(r, report_language)}({r.code})**: "
                    f"{localize_operation_advice(r.operation_advice, report_language)} | "
                    f"{labels['score_label']} {r.sentiment_score} | "
                    f"{localize_trend_prediction(r.trend_prediction, report_language)}"
                )
        else:
            report_lines.extend([f"## 📈 {labels['report_title']}", ""])
            # 逐个股票的详细分析
            for result in sorted_results:
                _, emoji, _ = self._get_signal_level(result)
                confidence_stars = result.get_confidence_stars() if hasattr(result, "get_confidence_stars") else "⭐⭐"

                report_lines.extend(
                    [
                        f"### {emoji} {self._get_display_name(result, report_language)} ({result.code})",
                        "",
                        f"**{labels['action_advice_label']}：{localize_operation_advice(result.operation_advice, report_language)}** | "
                        f"**{labels['score_label']}：{result.sentiment_score}** | "
                        f"**{labels['trend_label']}：{localize_trend_prediction(result.trend_prediction, report_language)}** | "
                        f"**Confidence：{confidence_stars}**",
                        "",
                    ]
                )

                self._append_market_snapshot(report_lines, result)

                # 核心看点
                if hasattr(result, "key_points") and result.key_points:
                    report_lines.extend(
                        [
                            f"**🎯 核心看点**：{result.key_points}",
                            "",
                        ]
                    )

                # 买入/卖出理由
                if hasattr(result, "buy_reason") and result.buy_reason:
                    report_lines.extend(
                        [
                            f"**💡 操作理由**：{result.buy_reason}",
                            "",
                        ]
                    )

                # 走势分析
                if hasattr(result, "trend_analysis") and result.trend_analysis:
                    report_lines.extend(
                        [
                            "#### 📉 走势分析",
                            f"{result.trend_analysis}",
                            "",
                        ]
                    )

                # 短期/中期展望
                outlook_lines = []
                if hasattr(result, "short_term_outlook") and result.short_term_outlook:
                    outlook_lines.append(f"- **短期（1-3日）**：{result.short_term_outlook}")
                if hasattr(result, "medium_term_outlook") and result.medium_term_outlook:
                    outlook_lines.append(f"- **中期（1-2周）**：{result.medium_term_outlook}")
                if outlook_lines:
                    report_lines.extend(
                        [
                            "#### 🔮 市场展望",
                            *outlook_lines,
                            "",
                        ]
                    )

                # 技术面分析
                tech_lines = []
                if result.technical_analysis:
                    tech_lines.append(f"**综合**：{result.technical_analysis}")
                if hasattr(result, "ma_analysis") and result.ma_analysis:
                    tech_lines.append(f"**均线**：{result.ma_analysis}")
                if hasattr(result, "volume_analysis") and result.volume_analysis:
                    tech_lines.append(f"**量能**：{result.volume_analysis}")
                if hasattr(result, "pattern_analysis") and result.pattern_analysis:
                    tech_lines.append(f"**形态**：{result.pattern_analysis}")
                if tech_lines:
                    report_lines.extend(
                        [
                            "#### 📊 技术面分析",
                            *tech_lines,
                            "",
                        ]
                    )

                # 基本面分析
                fund_lines = []
                if hasattr(result, "fundamental_analysis") and result.fundamental_analysis:
                    fund_lines.append(result.fundamental_analysis)
                if hasattr(result, "sector_position") and result.sector_position:
                    fund_lines.append(f"**板块地位**：{result.sector_position}")
                if hasattr(result, "company_highlights") and result.company_highlights:
                    fund_lines.append(f"**公司亮点**：{result.company_highlights}")
                if fund_lines:
                    report_lines.extend(
                        [
                            "#### 🏢 基本面分析",
                            *fund_lines,
                            "",
                        ]
                    )

                # 消息面/情绪面
                news_lines = []
                if result.news_summary:
                    news_lines.append(f"**新闻摘要**：{result.news_summary}")
                if hasattr(result, "market_sentiment") and result.market_sentiment:
                    news_lines.append(f"**市场情绪**：{result.market_sentiment}")
                if hasattr(result, "hot_topics") and result.hot_topics:
                    news_lines.append(f"**相关热点**：{result.hot_topics}")
                if news_lines:
                    report_lines.extend(
                        [
                            "#### 📰 消息面/情绪面",
                            *news_lines,
                            "",
                        ]
                    )

                # 综合分析
                if result.analysis_summary:
                    report_lines.extend(
                        [
                            "#### 📝 综合分析",
                            result.analysis_summary,
                            "",
                        ]
                    )

                # 风险提示
                if hasattr(result, "risk_warning") and result.risk_warning:
                    report_lines.extend(
                        [
                            f"⚠️ **风险提示**：{result.risk_warning}",
                            "",
                        ]
                    )

                # 数据来源说明
                if hasattr(result, "search_performed") and result.search_performed:
                    report_lines.append("*🔍 已执行联网搜索*")
                if hasattr(result, "data_sources") and result.data_sources:
                    report_lines.append(f"*📋 数据来源：{result.data_sources}*")

                # 错误信息（如果有）
                if not result.success and result.error_message:
                    report_lines.extend(
                        [
                            "",
                            f"❌ **分析异常**：{result.error_message[:100]}",
                        ]
                    )

                report_lines.extend(
                    [
                        "",
                        "---",
                        "",
                    ]
                )

        # 底部信息（去除免责声明）
        report_lines.extend(
            [
                "",
                f"*{labels['generated_at_label']}：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
            ]
        )

        return "\n".join(report_lines)
