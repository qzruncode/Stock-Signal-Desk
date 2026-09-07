# -*- coding: utf-8 -*-
"""Contract tests for source-correct financial Agent tools."""

from __future__ import annotations

from unittest.mock import patch

from market_data_service.providers import financial_data as data
from src.tools.get_balance_sheet import get_balance_sheet
from src.tools.get_cashflow import get_cashflow
from src.tools.get_income_statement import get_income_statement


def test_balance_sheet_keeps_bank_fields_distinct_and_uses_period_end_basis() -> None:
    item = data._normalize_balance(
        {
            "REPORT_DATE": "2026-03-31 00:00:00",
            "TOTAL_ASSETS": 1_000.0,
            "TOTAL_LIABILITIES": 900.0,
            "TOTAL_EQUITY": 100.0,
            "CASH_DEPOSIT_PBC": 120.0,
            "LOAN_ADVANCE": 650.0,
            "ACCEPT_DEPOSIT": 700.0,
        }
    )

    assert item["basis"] == "period_end"
    assert item["cash_and_central_bank_deposits"] == 120.0
    assert item["loans_and_advances"] == 650.0
    assert item["customer_deposits"] == 700.0
    assert item["debt_ratio"] == 90.0
    assert item["current_ratio"] is None


def test_balance_sheet_keeps_zero_numerators_instead_of_treating_them_as_missing() -> (
    None
):
    item = data._normalize_balance(
        {
            "REPORT_DATE": "2026-03-31",
            "TOTAL_ASSETS": 1_000.0,
            "TOTAL_LIABILITIES": 0.0,
            "TOTAL_EQUITY": 1_000.0,
            "TOTAL_CURRENT_ASSETS": 0.0,
            "TOTAL_CURRENT_LIAB": 100.0,
            "INVENTORY": 0.0,
        }
    )

    assert item["debt_ratio"] == 0.0
    assert item["current_ratio"] == 0.0
    assert item["quick_ratio"] == 0.0


def test_income_and_cashflow_are_single_quarter_not_ytd() -> None:
    income = data._normalize_income(
        {
            "REPORT_DATE": "2026-06-30",
            "TOTAL_OPERATE_INCOME": 100.0,
            "OPERATE_COST": 60.0,
            "PARENT_NETPROFIT": 20.0,
        }
    )
    cashflow = data._normalize_cashflow(
        {
            "REPORT_DATE": "2026-06-30",
            "NETCASH_OPERATE": 30.0,
            "CONSTRUCT_LONG_ASSET": 8.0,
        }
    )

    assert income["basis"] == "single_quarter"
    assert income["gross_margin"] == 40.0
    assert income["net_margin"] == 20.0
    assert cashflow["basis"] == "single_quarter"
    assert cashflow["free_cash_flow"] == 22.0


def _section_payload(section: str) -> dict:
    return {
        "symbol": "600519",
        "requested_periods": 4,
        "periods": 1,
        section: [{"report_date": "2026-03-31"}],
        "basis": "period_end" if section == "balance_sheet" else "single_quarter",
        "amount_unit": "元",
        "ratio_unit": "%",
        "currency": "CNY",
        "success": True,
        "errors": [],
    }


def test_statement_specific_tools_request_only_their_own_statement() -> None:
    with patch(
        "src.tools._financial_statements.get_financial_section",
        side_effect=lambda _, section, __, **___: _section_payload(section),
    ) as fetch:
        balance = get_balance_sheet("600519")
        income = get_income_statement("600519")
        cashflow = get_cashflow("600519")

    assert [call.args[1] for call in fetch.call_args_list] == [
        "balance_sheet",
        "income_statement",
        "cashflow",
    ]
    assert balance["basis"] == "period_end"
    assert income["basis"] == "single_quarter"
    assert cashflow["basis"] == "single_quarter"


def test_section_result_has_explicit_units_freshness_and_source(monkeypatch) -> None:
    monkeypatch.setattr(data, "_company_type", lambda _: "4")
    monkeypatch.setattr(
        data,
        "_fetch_section",
        lambda _, __, section, ___: (
            [
                {
                    "REPORT_DATE": "2026-03-31",
                    "NOTICE_DATE": "2026-04-20",
                    "TOTAL_OPERATE_INCOME": 100.0,
                    "OPERATE_COST": 60.0,
                    "PARENT_NETPROFIT": 20.0,
                }
            ]
            if section == "income_statement"
            else []
        ),
    )

    result = data.get_financial_section(
        "600519", "income_statement", 4, use_cache=False
    )

    assert result["success"] is True
    assert result["periods"] == 1
    assert result["basis"] == "single_quarter"
    assert result["amount_unit"] == "元"
    assert result["ratio_unit"] == "%"
    assert result["source_url"].startswith("https://emweb.securities.eastmoney.com/")
    assert result["data_time"] == "2026-03-31"


def test_financial_bundle_treats_one_available_statement_as_partial_success(
    monkeypatch,
) -> None:
    monkeypatch.setattr(data, "_company_type", lambda _: "4")
    monkeypatch.setattr(
        data,
        "_fetch_section",
        lambda _, __, section, ___: (
            [
                {
                    "REPORT_DATE": "2026-03-31",
                    "TOTAL_OPERATE_INCOME": 100.0,
                    "OPERATE_COST": 60.0,
                    "PARENT_NETPROFIT": 20.0,
                }
            ]
            if section == "income_statement"
            else []
        ),
    )

    result = data.get_financial_bundle("600519", 4, use_cache=False)

    assert result["success"] is True
    assert result["partial"] is True
    assert result["income_statement"]
    assert result["balance_sheet"] == []
    assert result["cashflow"] == []
