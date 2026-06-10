# -*- coding: utf-8 -*-
"""Tests for risk event helpers."""

from __future__ import annotations

import unittest

from api.v1.endpoints.financials import _extract_risk_summary, _match_risk_keywords


class RiskEventsHelperTestCase(unittest.TestCase):
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
