# -*- coding: utf-8 -*-
"""Tests for analyzer news prompt hard constraints (Issue #697)."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    from tests.litellm_stub import ensure_litellm_stub

    ensure_litellm_stub()

from src.analyzer import (
    GeminiAnalyzer,
    _infer_trend_direction,
    _sanitize_trend_analysis_for_prompt,
)


class AnalyzerNewsPromptTestCase(unittest.TestCase):
    def test_infer_trend_direction_reads_only_typed_fields(self) -> None:
        self.assertEqual(_infer_trend_direction({"trend_direction": "bullish"}), "bullish")
        self.assertEqual(_infer_trend_direction({"direction": "bearish"}), "bearish")
        self.assertEqual(_infer_trend_direction({"is_bullish": True}), "bullish")
        self.assertEqual(_infer_trend_direction({"is_bullish": False}), "bearish")

    def test_infer_trend_direction_does_not_classify_prose(self) -> None:
        self.assertEqual(
            _infer_trend_direction({
                "trend_status": "不是空头而是多头排列",
                "ma_alignment": "MA5 > MA10 > MA20",
            }),
            "neutral",
        )

    def test_analysis_prompt_contains_actionability_guardrails(self) -> None:
        analyzer = GeminiAnalyzer()

        prompt = analyzer._get_analysis_system_prompt("zh", stock_code="002812")

        self.assertIn("可操作性与稳定性约束", prompt)
        self.assertIn("不得仅因为单日涨跌", prompt)
        self.assertIn("支撑、压力", prompt)
        self.assertIn("独立解释证据", prompt)

    def test_prompt_contains_time_constraints(self) -> None:
        analyzer = GeminiAnalyzer()

        context = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "date": "2026-03-16",
            "today": {},
            "fundamental_context": {
                "earnings": {
                    "data": {
                        "financial_report": {"report_date": "2025-12-31", "revenue": 1000},
                        "dividend": {"ttm_cash_dividend_per_share": 1.2, "ttm_dividend_yield_pct": 2.4},
                    }
                }
            },
        }
        fake_cfg = SimpleNamespace(
            news_max_age_days=30,
            news_strategy_profile="medium",  # 7 days
        )
        with patch("src.analyzer.get_config", return_value=fake_cfg):
            prompt = analyzer._format_prompt(context, "贵州茅台", news_context="news")

        self.assertIn("近7日的新闻搜索结果", prompt)
        self.assertIn("每一条都必须带具体日期（YYYY-MM-DD）", prompt)
        self.assertIn("超出近7日窗口的新闻一律忽略", prompt)
        self.assertIn("时间未知、无法确定发布日期的新闻一律忽略", prompt)
        self.assertIn("财报与分红（价值投资口径）", prompt)
        self.assertIn("禁止编造", prompt)

    def test_prompt_includes_capital_flow_as_operation_filter(self) -> None:
        analyzer = GeminiAnalyzer()

        context = {
            "code": "002812",
            "stock_name": "恩捷股份",
            "date": "2026-04-01",
            "today": {"close": 32.8, "ma5": 31.2, "ma10": 30.5, "ma20": 29.8},
            "fundamental_context": {
                "capital_flow": {
                    "status": "ok",
                    "data": {
                        "stock_flow": {
                            "main_net_inflow": -1200000,
                            "inflow_5d": -3600000,
                            "inflow_10d": -5200000,
                        },
                        "sector_rankings": {
                            "top": [{"name": "电池"}],
                            "bottom": [{"name": "化工"}],
                        },
                    },
                }
            },
        }

        prompt = analyzer._format_prompt(context, "恩捷股份", news_context=None)

        self.assertIn("主力资金流向（原始交易证据）", prompt)
        self.assertIn("主力净流入", prompt)
        self.assertIn("-1200000", prompt)
        self.assertIn("不得由正负号直接生成操作结论", prompt)

    def test_prompt_prefers_context_news_window_days(self) -> None:
        analyzer = GeminiAnalyzer()

        context = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "date": "2026-03-16",
            "today": {},
            "news_window_days": 1,
        }
        fake_cfg = SimpleNamespace(
            news_max_age_days=30,
            news_strategy_profile="long",  # 30 days if fallback is used
        )
        with patch("src.analyzer.get_config", return_value=fake_cfg):
            prompt = analyzer._format_prompt(context, "贵州茅台", news_context="news")

        self.assertIn("近1日的新闻搜索结果", prompt)
        self.assertIn("超出近1日窗口的新闻一律忽略", prompt)

    def test_sanitize_trend_analysis_for_prompt_returns_derived_copy_only(self) -> None:
        original = {
            "trend_status": "空头排列",
            "ma_alignment": "空头排列 MA5<MA10<MA20",
            "signal_reasons": ["多头排列，持续上涨", "事件催化存在但技术待确认"],
            "risk_factors": ["跌破MA20，趋势承压"],
        }

        sanitized = _sanitize_trend_analysis_for_prompt(original, volume_change_ratio=12.4)

        self.assertEqual(
            original["signal_reasons"],
            ["多头排列，持续上涨", "事件催化存在但技术待确认"],
        )
        self.assertNotIn("prompt_consistency_notes", original)
        self.assertNotIn("prompt_trend_direction", original)
        self.assertIn("多头排列，持续上涨", sanitized["signal_reasons"])
        self.assertEqual(sanitized["prompt_trend_direction"], "neutral")
        self.assertEqual(sanitized["prompt_volume_change_ratio"], 12.4)


if __name__ == "__main__":
    unittest.main()
