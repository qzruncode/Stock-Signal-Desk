# -*- coding: utf-8 -*-
"""Tests for risk event helpers."""

from __future__ import annotations

import unittest

from api.v1.endpoints.financials import (
    _classify_risk_event,
    _extract_risk_summary,
    _match_risk_keywords,
)


class RiskEventsHelperTestCase(unittest.TestCase):
    def test_classify_inquiry_as_medium_regulatory(self) -> None:
        risk = _classify_risk_event("收到交易所问询函并回复相关问题", source_kind="announcement")

        self.assertIsNotNone(risk)
        assert risk is not None
        self.assertEqual(risk["risk_label"], "监管处罚")
        self.assertEqual(risk["severity"], "medium")

    def test_classify_internal_control_as_low_governance(self) -> None:
        risk = _classify_risk_event("关于2025年度内部控制审计报告的公告", source_kind="announcement")

        self.assertIsNotNone(risk)
        assert risk is not None
        self.assertEqual(risk["risk_label"], "治理异动")
        self.assertEqual(risk["severity"], "low")

    def test_match_risk_keywords(self) -> None:
        text = "控股股东质押股份被冻结，并收到监管函"
        matched = _match_risk_keywords(text)

        self.assertIn("质押", matched)
        self.assertIn("冻结", matched)
        self.assertIn("监管函", matched)

    def test_extract_risk_summary(self) -> None:
        content = (
            "公司公告称控股股东部分股份被冻结。"
            "本次冻结涉及债务逾期事项，存在被动减持风险。"
            "公司正在与相关方协商解决。"
        )
        summary = _extract_risk_summary(content, ["冻结", "债务逾期", "减持"])

        self.assertIn("被冻结", summary)
        self.assertIn("债务逾期", summary)


if __name__ == "__main__":
    unittest.main()
