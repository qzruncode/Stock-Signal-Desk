"""NotificationService method group 3."""

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

class _NotificationServiceMethods3:
    def generate_wechat_dashboard(self, results: List[AnalysisResult]) -> str:
        """
        生成企业微信决策仪表盘精简版（控制在4000字符内）

        只保留核心结论和狙击点位

        Args:
            results: 分析结果列表

        Returns:
            精简版决策仪表盘
        """
        config = get_config()
        report_language = self._get_report_language(results)
        labels = get_report_labels(report_language)
        report_date = datetime.now().strftime("%Y-%m-%d")

        # 按评分排序
        sorted_results = sorted(results, key=lambda x: x.sentiment_score, reverse=True)

        # 统计 - 使用 decision_type 字段准确统计
        buy_count = sum(1 for r in results if getattr(r, "decision_type", "") == "buy")
        sell_count = sum(1 for r in results if getattr(r, "decision_type", "") == "sell")
        hold_count = sum(1 for r in results if getattr(r, "decision_type", "") in ("hold", ""))

        lines = [
            f"## 🎯 {report_date} {labels['dashboard_title']}",
            "",
            f"> {len(results)} {labels['stock_unit']} | "
            f"🟢{labels['buy_label']}:{buy_count} 🟡{labels['watch_label']}:{hold_count} 🔴{labels['sell_label']}:{sell_count}",
            "",
        ]

        # Issue #262: summary_only 时仅输出摘要列表
        if self._report_summary_only:
            lines.append(f"**📊 {labels['summary_heading']}**")
            lines.append("")
            for r in sorted_results:
                _, signal_emoji, _ = self._get_signal_level(r)
                stock_name = self._get_display_name(r, report_language)
                lines.append(
                    f"{signal_emoji} **{stock_name}({r.code})**: "
                    f"{localize_operation_advice(r.operation_advice, report_language)} | "
                    f"{labels['score_label']} {r.sentiment_score} | "
                    f"{localize_trend_prediction(r.trend_prediction, report_language)}"
                )
        else:
            for result in sorted_results:
                signal_text, signal_emoji, _ = self._get_signal_level(result)
                dashboard = result.dashboard if hasattr(result, "dashboard") and result.dashboard else {}
                core = dashboard.get("core_conclusion", {}) if dashboard else {}
                battle = dashboard.get("battle_plan", {}) if dashboard else {}
                intel = dashboard.get("intelligence", {}) if dashboard else {}

                # 股票名称
                stock_name = self._get_display_name(result, report_language)

                # 标题行：信号等级 + 股票名称
                lines.append(f"### {signal_emoji} **{signal_text}** | {stock_name}({result.code})")
                lines.append("")

                # 核心决策（一句话）
                one_sentence = core.get("one_sentence", result.analysis_summary) if core else result.analysis_summary
                if one_sentence:
                    lines.append(f"📌 **{one_sentence[:80]}**")
                    lines.append("")

                # 重要信息区（舆情+基本面）
                info_lines = []

                # 业绩预期
                if intel.get("earnings_outlook"):
                    outlook = str(intel["earnings_outlook"])[:60]
                    info_lines.append(f"📊 {labels['earnings_outlook_label']}: {outlook}")
                if intel.get("sentiment_summary"):
                    sentiment = str(intel["sentiment_summary"])[:50]
                    info_lines.append(f"💭 {labels['sentiment_summary_label']}: {sentiment}")
                if info_lines:
                    lines.extend(info_lines)
                    lines.append("")

                # 风险警报（最重要，醒目显示）
                risks = intel.get("risk_alerts", []) if intel else []
                if risks:
                    lines.append(f"🚨 **{labels['risk_alerts_label']}**:")
                    for risk in risks[:2]:  # 最多显示2条
                        risk_str = str(risk)
                        risk_text = risk_str[:50] + "..." if len(risk_str) > 50 else risk_str
                        lines.append(f"   • {risk_text}")
                    lines.append("")

                # 利好催化
                catalysts = intel.get("positive_catalysts", []) if intel else []
                if catalysts:
                    lines.append(f"✨ **{labels['positive_catalysts_label']}**:")
                    for cat in catalysts[:2]:  # 最多显示2条
                        cat_str = str(cat)
                        cat_text = cat_str[:50] + "..." if len(cat_str) > 50 else cat_str
                        lines.append(f"   • {cat_text}")
                    lines.append("")

                # 狙击点位
                sniper = battle.get("sniper_points", {}) if battle else {}
                if sniper:
                    ideal_buy = str(sniper.get("ideal_buy", ""))
                    stop_loss = str(sniper.get("stop_loss", ""))
                    take_profit = str(sniper.get("take_profit", ""))
                    points = []
                    if ideal_buy:
                        points.append(f"🎯{labels['ideal_buy_label']}:{ideal_buy[:15]}")
                    if stop_loss:
                        points.append(f"🛑{labels['stop_loss_label']}:{stop_loss[:15]}")
                    if take_profit:
                        points.append(f"🎊{labels['take_profit_label']}:{take_profit[:15]}")
                    if points:
                        lines.append(" | ".join(points))
                        lines.append("")

                # 持仓建议
                pos_advice = core.get("position_advice", {}) if core else {}
                if pos_advice:
                    no_pos = str(pos_advice.get("no_position", ""))
                    has_pos = str(pos_advice.get("has_position", ""))
                    if no_pos:
                        lines.append(f"🆕 {labels['no_position_label']}: {no_pos[:50]}")
                    if has_pos:
                        lines.append(f"💼 {labels['has_position_label']}: {has_pos[:50]}")
                    lines.append("")

                # 检查清单简化版
                checklist = battle.get("action_checklist", []) if battle else []
                if checklist:
                    # 只显示不通过的项目
                    failed_checks = [str(c) for c in checklist if str(c).startswith("❌") or str(c).startswith("⚠️")]
                    if failed_checks:
                        lines.append(f"**{labels['failed_checks_heading']}**:")
                        for check in failed_checks[:3]:
                            lines.append(f"   {check[:40]}")
                        lines.append("")

                lines.append("---")
                lines.append("")

        # 底部
        lines.append(f"*{labels['report_time_label']}: {datetime.now().strftime('%H:%M')}*")
        models = self._collect_models_used(results)
        if models:
            lines.append(f"*{labels['analysis_model_label']}: {', '.join(models)}*")

        content = "\n".join(lines)

        return content
    def generate_wechat_summary(self, results: List[AnalysisResult]) -> str:
        """
        生成企业微信精简版日报（控制在4000字符内）

        Args:
            results: 分析结果列表

        Returns:
            精简版 Markdown 内容
        """
        report_date = datetime.now().strftime("%Y-%m-%d")
        report_language = self._get_report_language(results)
        labels = get_report_labels(report_language)

        # 按评分排序
        sorted_results = sorted(results, key=lambda x: x.sentiment_score, reverse=True)

        # 统计 - 使用 decision_type 字段准确统计
        buy_count = sum(1 for r in results if getattr(r, "decision_type", "") == "buy")
        sell_count = sum(1 for r in results if getattr(r, "decision_type", "") == "sell")
        hold_count = sum(1 for r in results if getattr(r, "decision_type", "") in ("hold", ""))
        avg_score = sum(r.sentiment_score for r in results) / len(results) if results else 0

        lines = [
            f"## 📅 {report_date} {labels['report_title']}",
            "",
            f"> {labels['analyzed_prefix']} **{len(results)}** {labels['stock_unit_compact']} | "
            f"🟢{labels['buy_label']}:{buy_count} 🟡{labels['watch_label']}:{hold_count} 🔴{labels['sell_label']}:{sell_count} | "
            f"{labels['avg_score_label']}:{avg_score:.0f}",
            "",
        ]

        # 每只股票精简信息（控制长度）
        for result in sorted_results:
            _, emoji, _ = self._get_signal_level(result)

            # 核心信息行
            lines.append(f"### {emoji} {self._get_display_name(result, report_language)}({result.code})")
            lines.append(
                f"**{localize_operation_advice(result.operation_advice, report_language)}** | "
                f"{labels['score_label']}:{result.sentiment_score} | "
                f"{localize_trend_prediction(result.trend_prediction, report_language)}"
            )

            # 操作理由（截断）
            if hasattr(result, "buy_reason") and result.buy_reason:
                reason = result.buy_reason[:80] + "..." if len(result.buy_reason) > 80 else result.buy_reason
                lines.append(f"💡 {reason}")

            # 核心看点
            if hasattr(result, "key_points") and result.key_points:
                points = result.key_points[:60] + "..." if len(result.key_points) > 60 else result.key_points
                lines.append(f"🎯 {points}")

            # 风险提示（截断）
            if hasattr(result, "risk_warning") and result.risk_warning:
                risk = result.risk_warning[:50] + "..." if len(result.risk_warning) > 50 else result.risk_warning
                lines.append(f"⚠️ {risk}")

            lines.append("")

        # 底部（模型行在 --- 之前，Issue #528）
        models = self._collect_models_used(results)
        if models:
            lines.append(f"*{labels['analysis_model_label']}: {', '.join(models)}*")
        lines.extend(
            [
                "---",
                f"*{labels['not_investment_advice']}*",
                f"*{labels['details_report_hint']} reports/report_{report_date.replace('-', '')}.md*",
            ]
        )

        content = "\n".join(lines)

        return content
    def generate_brief_report(
        self,
        results: List[AnalysisResult],
        report_date: Optional[str] = None,
    ) -> str:
        """
        Generate brief report (3-5 sentences per stock) for mobile/push.

        Args:
            results: Analysis results list (use [result] for single stock).
            report_date: Report date (default: today).

        Returns:
            Brief markdown content.
        """
        if report_date is None:
            report_date = datetime.now().strftime("%Y-%m-%d")
        report_language = self._get_report_language(results)
        labels = get_report_labels(report_language)
        config = get_config()
        # Fallback: brief summary from dashboard report
        if not results:
            return f"# {report_date} {labels['brief_title']}\n\n{labels['no_results']}"
        sorted_results = sorted(results, key=lambda x: x.sentiment_score, reverse=True)
        buy_count = sum(1 for r in results if getattr(r, "decision_type", "") == "buy")
        sell_count = sum(1 for r in results if getattr(r, "decision_type", "") == "sell")
        hold_count = sum(1 for r in results if getattr(r, "decision_type", "") in ("hold", ""))
        lines = [
            f"# {report_date} {labels['brief_title']}",
            "",
            f"> {len(results)} {labels['stock_unit_compact']} | 🟢{buy_count} 🟡{hold_count} 🔴{sell_count}",
            "",
        ]
        for r in sorted_results:
            _, emoji, _ = self._get_signal_level(r)
            name = self._get_display_name(r, report_language)
            dash = r.dashboard or {}
            core = dash.get("core_conclusion", {}) or {}
            one = (core.get("one_sentence") or r.analysis_summary or "")[:60]
            lines.append(
                f"**{name}({r.code})** {emoji} "
                f"{localize_operation_advice(r.operation_advice, report_language)} | "
                f"{labels['score_label']} {r.sentiment_score} | {one}"
            )
        lines.append("")
        lines.append(f"*{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        models = self._collect_models_used(results)
        if models:
            lines.append(f"*{labels['analysis_model_label']}: {', '.join(models)}*")
        return "\n".join(lines)
    def generate_single_stock_report(self, result: AnalysisResult) -> str:
        """
        生成单只股票的分析报告（用于单股推送模式 #55）

        格式精简但信息完整，适合每分析完一只股票立即推送

        Args:
            result: 单只股票的分析结果

        Returns:
            Markdown 格式的单股报告
        """
        report_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        report_language = self._get_report_language(result)
        labels = get_report_labels(report_language)
        signal_text, signal_emoji, _ = self._get_signal_level(result)
        dashboard = result.dashboard if hasattr(result, "dashboard") and result.dashboard else {}
        core = dashboard.get("core_conclusion", {}) if dashboard else {}
        battle = dashboard.get("battle_plan", {}) if dashboard else {}
        intel = dashboard.get("intelligence", {}) if dashboard else {}

        # 股票名称（转义 *ST 等特殊字符）
        stock_name = self._get_display_name(result, report_language)

        lines = [
            f"## {signal_emoji} {stock_name} ({result.code})",
            "",
            f"> {report_date} | {labels['score_label']}: **{result.sentiment_score}** | {localize_trend_prediction(result.trend_prediction, report_language)}",
            "",
        ]

        self._append_market_snapshot(lines, result)

        # 核心决策（一句话）
        one_sentence = core.get("one_sentence", result.analysis_summary) if core else result.analysis_summary
        if one_sentence:
            lines.extend(
                [
                    f"### 📌 {labels['core_conclusion_heading']}",
                    "",
                    f"**{signal_text}**: {one_sentence}",
                    "",
                ]
            )

        # 重要信息（舆情+基本面）
        info_added = False
        if intel:
            if intel.get("earnings_outlook"):
                if not info_added:
                    lines.append(f"### 📰 {labels['info_heading']}")
                    lines.append("")
                    info_added = True
                lines.append(f"📊 **{labels['earnings_outlook_label']}**: {str(intel['earnings_outlook'])[:100]}")

            if intel.get("sentiment_summary"):
                if not info_added:
                    lines.append(f"### 📰 {labels['info_heading']}")
                    lines.append("")
                    info_added = True
                lines.append(f"💭 **{labels['sentiment_summary_label']}**: {str(intel['sentiment_summary'])[:80]}")

            # 风险警报
            risks = intel.get("risk_alerts", [])
            if risks:
                if not info_added:
                    lines.append(f"### 📰 {labels['info_heading']}")
                    lines.append("")
                    info_added = True
                lines.append("")
                lines.append(f"🚨 **{labels['risk_alerts_label']}**:")
                for risk in risks[:3]:
                    lines.append(f"- {str(risk)[:60]}")

            # 利好催化
            catalysts = intel.get("positive_catalysts", [])
            if catalysts:
                lines.append("")
                lines.append(f"✨ **{labels['positive_catalysts_label']}**:")
                for cat in catalysts[:3]:
                    lines.append(f"- {str(cat)[:60]}")

        if info_added:
            lines.append("")

        # 狙击点位
        sniper = battle.get("sniper_points", {}) if battle else {}
        if sniper:
            lines.extend(
                [
                    f"### 🎯 {labels['action_points_heading']}",
                    "",
                    f"| {labels['ideal_buy_label']} | {labels['stop_loss_label']} | {labels['take_profit_label']} |",
                    "|------|------|------|",
                ]
            )
            ideal_buy = sniper.get("ideal_buy", "-")
            stop_loss = sniper.get("stop_loss", "-")
            take_profit = sniper.get("take_profit", "-")
            lines.append(f"| {ideal_buy} | {stop_loss} | {take_profit} |")
            lines.append("")

        # 持仓建议
        pos_advice = core.get("position_advice", {}) if core else {}
        if pos_advice:
            lines.extend(
                [
                    f"### 💼 {labels['position_advice_heading']}",
                    "",
                    f"- 🆕 **{labels['no_position_label']}**: {pos_advice.get('no_position', localize_operation_advice(result.operation_advice, report_language))}",
                    f"- 💼 **{labels['has_position_label']}**: {pos_advice.get('has_position', labels['continue_holding'])}",
                    "",
                ]
            )

        lines.append("---")
        if self._should_show_llm_model():
            model_used = normalize_model_used(getattr(result, "model_used", None))
            if model_used:
                lines.append(f"*{labels['analysis_model_label']}: {model_used}*")
        lines.append(f"*{labels['not_investment_advice']}*")

        return "\n".join(lines)
    def send(
        self,
        content: str,
    ) -> bool:
        """
        发送消息到企业微信。

        Args:
            content: 消息内容（Markdown 格式）

        Returns:
            是否发送成功
        """
        try:
            result = self.send_to_wechat(content)
            if result:
                logger.info("企业微信通知发送成功")
            else:
                logger.error("企业微信通知发送失败")
            return result
        except Exception as e:
            logger.error(f"企业微信发送异常: {e}")
            return False
