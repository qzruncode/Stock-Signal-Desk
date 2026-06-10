# -*- coding: utf-8 -*-
"""Tests for valuation overdraft signal helper."""

from __future__ import annotations

import unittest

from api.v1.endpoints.financials import _build_price_overdraft_signal


class PriceOverdraftSignalTestCase(unittest.TestCase):
    def test_elevated_overdraft_when_expensive_and_expectation_weak(self) -> None:
        result = _build_price_overdraft_signal(
            {
                "pe_ttm": 48,
                "pe_dynamic": 46,
                "pb": 8.2,
                "peg": 2.3,
                "dividend_yield": 0.6,
                "pe_percentiles": {"1y": 92, "3y": 95, "5y": 97},
                "industry_average": {"industry": "白酒", "pe": 24, "pb": 4.1, "sample_size": 20},
            }
        )

        self.assertIn(result["status"], {"medium", "high"})
        self.assertGreaterEqual(result["score"], 55)
        self.assertIn("pe_percentile_extremely_high", result["signals"])
        self.assertIn("peg_above_2", result["signals"])

    def test_low_overdraft_when_expectation_can_digest_valuation(self) -> None:
        result = _build_price_overdraft_signal(
            {
                "pe_ttm": 32,
                "pe_dynamic": 20,
                "pb": 4.0,
                "peg": 0.9,
                "dividend_yield": 2.8,
                "pe_percentiles": {"1y": 70, "3y": 76, "5y": 72},
                "industry_average": {"industry": "软件", "pe": 28, "pb": 3.5, "sample_size": 35},
            }
        )

        self.assertIn(result["status"], {"low", "watch"})
        self.assertGreater(result["expectation_support_score"], result["valuation_expensive_score"])
        self.assertIn("forward_pe_improving", result["signals"])
        self.assertIn("peg_supportive", result["signals"])

    def test_uncertain_when_evidence_is_sparse(self) -> None:
        result = _build_price_overdraft_signal(
            {
                "pe_ttm": None,
                "pe_dynamic": None,
                "pb": None,
                "peg": None,
                "dividend_yield": None,
                "pe_percentiles": {},
                "industry_average": {"industry": None, "pe": None, "pb": None, "sample_size": 0},
            }
        )

        self.assertEqual(result["status"], "uncertain")
        self.assertGreater(len(result["limitations"]), 0)


if __name__ == "__main__":
    unittest.main()
