# -*- coding: utf-8 -*-
"""Stocks filter helper tests — TTM computation.

Covers api.v1.endpoints.stocks.filter._compute_ttm_value for normal /
boundary / failure paths.
"""

from __future__ import annotations

from api.v1.endpoints.stocks.filter import _compute_ttm_value


def _stmt(section, items):
    return {section: items}


def test_compute_ttm_returns_annual_value_when_dec31():
    statements = _stmt(
        "income_statement",
        [
            {"report_date": "2025-12-31", "revenue": 1000.0},
        ],
    )
    assert _compute_ttm_value(statements, "revenue") == 1000.0


def test_compute_ttm_sums_last_four_quarters_when_not_annual():
    statements = _stmt(
        "cashflow",
        [
            {"report_date": "2026-03-31", "amount": 100.0},
            {"report_date": "2025-12-31", "amount": 200.0},
            {"report_date": "2025-09-30", "amount": 300.0},
            {"report_date": "2025-06-30", "amount": 400.0},
        ],
    )
    assert _compute_ttm_value(statements, "amount") == 1000.0


def test_compute_ttm_returns_none_when_fewer_than_four_quarters():
    statements = _stmt(
        "balance_sheet",
        [
            {"report_date": "2026-03-31", "amount": 100.0},
        ],
    )
    assert _compute_ttm_value(statements, "amount") is None


def test_compute_ttm_skips_none_values():
    statements = _stmt(
        "income_statement",
        [
            {"report_date": "2026-03-31", "amount": None},
            {"report_date": "2025-12-31", "amount": 200.0},
            {"report_date": "2025-09-30", "amount": 300.0},
            {"report_date": "2025-06-30", "amount": 400.0},
        ],
    )
    # only 3 non-None values -> cannot sum 4 -> None
    assert _compute_ttm_value(statements, "amount") is None


def test_compute_ttm_returns_none_when_no_sections():
    assert _compute_ttm_value({}, "revenue") is None


def test_compute_ttm_returns_none_when_section_empty():
    assert _compute_ttm_value(_stmt("income_statement", []), "revenue") is None


def test_compute_ttm_returns_none_when_first_section_has_no_field():
    # income_statement has items but no 'amount' field -> returns None,
    # does NOT fall through to other sections (current implementation).
    statements = {
        "income_statement": [{"report_date": "2025-12-31", "other": 1}],
        "cashflow": [{"report_date": "2025-12-31", "amount": 500.0}],
    }
    assert _compute_ttm_value(statements, "amount") is None


def test_compute_ttm_uses_cashflow_when_income_statement_empty():
    statements = {
        "income_statement": [],
        "cashflow": [{"report_date": "2025-12-31", "amount": 500.0}],
    }
    assert _compute_ttm_value(statements, "amount") == 500.0
