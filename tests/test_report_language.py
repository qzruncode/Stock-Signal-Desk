# -*- coding: utf-8 -*-
"""Unit tests for report language helpers."""

import unittest

from src.report_language import (
    get_bias_status_emoji,
    get_localized_stock_name,
    get_sentiment_label,
    get_signal_level,
    infer_decision_type_from_advice,
    localize_trend_prediction,
    localize_bias_status,
)


class ReportLanguageTestCase(unittest.TestCase):
    def test_get_signal_level_handles_exact_structured_sell_advice(self) -> None:
        signal_text, emoji, signal_tag = get_signal_level("卖出", 60, "zh")

        self.assertEqual(signal_text, "卖出")
        self.assertEqual(emoji, "🔴")
        self.assertEqual(signal_tag, "sell")

    def test_get_signal_level_handles_exact_structured_buy_advice_in_english(self) -> None:
        signal_text, emoji, signal_tag = get_signal_level("Buy", 40, "en")

        self.assertEqual(signal_text, "Buy")
        self.assertEqual(emoji, "🟢")
        self.assertEqual(signal_tag, "buy")

    def test_get_localized_stock_name_replaces_placeholder_for_english(self) -> None:
        self.assertEqual(
            get_localized_stock_name("股票AAPL", "AAPL", "en"),
            "Unnamed Stock",
        )

    def test_get_sentiment_label_does_not_infer_semantics_from_score(self) -> None:
        self.assertEqual(get_sentiment_label(80, "en"), "Unclassified")
        self.assertEqual(get_sentiment_label(60, "en"), "Unclassified")
        self.assertEqual(get_sentiment_label(40, "zh"), "未分类")
        self.assertEqual(get_sentiment_label(20, "zh"), "未分类")

    def test_localize_trend_prediction_preserves_fine_grain_zh_states(self) -> None:
        self.assertEqual(localize_trend_prediction("多头排列", "zh"), "多头排列")
        self.assertEqual(localize_trend_prediction("弱势空头", "zh"), "弱势空头")

    def test_localize_trend_prediction_still_translates_english_input_for_zh(self) -> None:
        self.assertEqual(localize_trend_prediction("bullish", "zh"), "看多")
        self.assertEqual(localize_trend_prediction("very bearish", "zh"), "强烈看空")

    def test_bias_status_helpers_support_english_values(self) -> None:
        self.assertEqual(localize_bias_status("Safe", "en"), "Safe")
        self.assertEqual(localize_bias_status("警戒", "en"), "Caution")
        self.assertEqual(get_bias_status_emoji("Safe"), "✅")
        self.assertEqual(get_bias_status_emoji("Caution"), "⚠️")

    def test_infer_decision_type_uses_only_exact_structured_values(self) -> None:
        self.assertEqual(infer_decision_type_from_advice("买入"), "buy")
        self.assertEqual(infer_decision_type_from_advice("持有"), "hold")
        self.assertEqual(infer_decision_type_from_advice("减仓"), "sell")
        self.assertEqual(infer_decision_type_from_advice("洗盘观察"), "hold")
        self.assertEqual(
            infer_decision_type_from_advice("建议买入", default=""),
            "",
        )
        self.assertEqual(
            infer_decision_type_from_advice(
                "当前不跌破支撑位继续持有",
                default="",
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()
