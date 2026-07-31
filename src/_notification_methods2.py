"""NotificationService method group 2."""

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

class _NotificationServiceMethods2:
    def generate_dashboard_report(self, results: List[AnalysisResult], report_date: Optional[str] = None) -> str:
        """
        生成决策仪表盘格式的日报（详细版）

        格式：市场概览 + 重要信息 + 核心结论 + 数据透视 + 作战计划

        Args:
            results: 分析结果列表
            report_date: 报告日期（默认今天）

        Returns:
            Markdown 格式的决策仪表盘日报
        """
        config = get_config()
        report_language = self._get_report_language(results)
        labels = get_report_labels(report_language)
        reason_label = "Rationale" if report_language == "en" else "操作理由"
        risk_warning_label = "Risk Warning" if report_language == "en" else "风险提示"
        technical_heading = "Technicals" if report_language == "en" else "技术面"
        ma_label = "Moving Averages" if report_language == "en" else "均线"
        volume_analysis_label = "Volume" if report_language == "en" else "量能"
        news_heading = "News Flow" if report_language == "en" else "消息面"
        if report_date is None:
            report_date = datetime.now().strftime("%Y-%m-%d")

        # 按评分排序（高分在前）
        sorted_results = sorted(results, key=lambda x: x.sentiment_score, reverse=True)

        # 统计信息 - 使用 decision_type 字段准确统计
        buy_count = sum(1 for r in results if getattr(r, "decision_type", "") == "buy")
        sell_count = sum(1 for r in results if getattr(r, "decision_type", "") == "sell")
        hold_count = sum(1 for r in results if getattr(r, "decision_type", "") in ("hold", ""))

        report_lines = [
            f"# 🎯 {report_date} {labels['dashboard_title']}",
            "",
            f"> {labels['analyzed_prefix']} **{len(results)}** {labels['stock_unit']} | "
            f"🟢{labels['buy_label']}:{buy_count} 🟡{labels['watch_label']}:{hold_count} 🔴{labels['sell_label']}:{sell_count}",
            "",
        ]

        # === 新增：分析结果摘要 (Issue #112) ===
        if results:
            report_lines.extend(
                [
                    f"## 📊 {labels['summary_heading']}",
                    "",
                ]
            )
            for r in sorted_results:
                _, signal_emoji, _ = self._get_signal_level(r)
                display_name = self._get_display_name(r, report_language)
                report_lines.append(
                    f"{signal_emoji} **{display_name}({r.code})**: "
                    f"{localize_operation_advice(r.operation_advice, report_language)} | "
                    f"{labels['score_label']} {r.sentiment_score} | "
                    f"{localize_trend_prediction(r.trend_prediction, report_language)}"
                )
            report_lines.extend(
                [
                    "",
                    "---",
                    "",
                ]
            )

        # 逐个股票的决策仪表盘（Issue #262: summary_only 时跳过详情）
        if not self._report_summary_only:
            for result in sorted_results:
                signal_text, signal_emoji, signal_tag = self._get_signal_level(result)
                dashboard = result.dashboard if hasattr(result, "dashboard") and result.dashboard else {}

                # 股票名称（优先使用 dashboard 或 result 中的名称，转义 *ST 等特殊字符）
                stock_name = self._get_display_name(result, report_language)

                report_lines.extend(
                    [
                        f"## {signal_emoji} {stock_name} ({result.code})",
                        "",
                    ]
                )

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

                self._append_market_snapshot(report_lines, result)

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
                            f"✅ {labels['yes_label']}"
                            if trend_data.get("is_bullish", False)
                            else f"❌ {labels['no_label']}"
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
                        bias_status = price_data.get("bias_status", "N/A")
                        report_lines.extend(
                            [
                                f"| {labels['price_metrics_label']} | {labels['current_price_label']} |",
                                "|---------|------|",
                                f"| {labels['current_price_label']} | {price_data.get('current_price', 'N/A')} |",
                                f"| {labels['ma5_label']} | {price_data.get('ma5', 'N/A')} |",
                                f"| {labels['ma10_label']} | {price_data.get('ma10', 'N/A')} |",
                                f"| {labels['ma20_label']} | {price_data.get('ma20', 'N/A')} |",
                                f"| {labels['bias_ma5_label']} | {price_data.get('bias_ma5', 'N/A')}% {bias_status} |",
                                f"| {labels['support_level_label']} | {price_data.get('support_level', 'N/A')} |",
                                f"| {labels['resistance_level_label']} | {price_data.get('resistance_level', 'N/A')} |",
                                "",
                            ]
                        )
                    # 量能分析
                    if vol_data:
                        report_lines.extend(
                            [
                                f"**{labels['volume_label']}**: {labels['volume_ratio_label']} {vol_data.get('volume_ratio', 'N/A')} ({vol_data.get('volume_status', '')}) | "
                                f"{labels['turnover_rate_label']} {vol_data.get('turnover_rate', 'N/A')}%",
                                f"💡 *{vol_data.get('volume_meaning', '')}*",
                                "",
                            ]
                        )
                    # 筹码结构
                    if chip_data:
                        chip_health = localize_chip_health(chip_data.get("chip_health", "N/A"), report_language)
                        report_lines.extend(
                            [
                                f"**{labels['chip_label']}**: {chip_data.get('profit_ratio', 'N/A')} | {chip_data.get('avg_cost', 'N/A')} | "
                                f"{chip_data.get('concentration', 'N/A')} {chip_health}",
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

                # 如果没有 dashboard，显示传统格式
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

                report_lines.extend(
                    [
                        "---",
                        "",
                    ]
                )

        # 底部（去除免责声明）
        report_lines.extend(
            [
                "",
                f"*{labels['generated_at_label']}：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
            ]
        )
        models = self._collect_models_used(results)
        if models:
            report_lines.append(f"*{labels['analysis_model_label']}：{', '.join(models)}*")

        return "\n".join(report_lines)
