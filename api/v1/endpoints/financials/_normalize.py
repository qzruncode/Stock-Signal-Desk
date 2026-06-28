# -*- coding: utf-8 -*-
"""Field name normalization for financial statements."""

from __future__ import annotations


# EM normalized column names — used by _em_statements.py
BS_COLUMNS = {
    'REPORT_DATE': 'report_date',
    'REPORT_DATE_NAME': 'report_date_name',
    'TOTAL_ASSETS': 'total_assets',
    'TOTAL_LIABILITIES': 'total_liabilities',
    'TOTAL_EQUITY': 'total_equity',
    'TOTAL_PARENT_EQUITY': 'parent_equity',
    'MONETARYFUNDS': 'monetary_funds',
    'ACCOUNTS_RECE': 'accounts_receivable',
    'INVENTORY': 'inventory',
    'CONTRACT_LIAB': 'contract_liabilities',
    'FIXED_ASSET': 'fixed_asset',
    'SHORT_LOAN': 'short_loan',
    'LONG_LOAN': 'long_loan',
    'ACCOUNTS_PAYABLE': 'accounts_payable',
    'NONCURRENT_LIAB_1YEAR': 'noncurrent_liab_1year',
    'LEASE_LIAB': 'lease_liab',
    'TOTAL_CURRENT_ASSETS': 'total_current_assets',
    'TOTAL_CURRENT_LIAB': 'total_current_liabilities',
}

BS_STRING_FIELDS = {'report_date', 'report_date_name'}

IS_COLUMNS = {
    'REPORT_DATE': 'report_date',
    'REPORT_DATE_NAME': 'report_date_name',
    'TOTAL_OPERATE_INCOME': 'revenue',
    'TOTAL_OPERATE_INCOME_YOY': 'revenue_yoy',
    'TOTAL_OPERATE_COST': 'total_cost',
    'OPERATE_COST': 'operate_cost',
    'OPERATE_PROFIT': 'operate_profit',
    'TOTAL_PROFIT': 'total_profit',
    'NETPROFIT': 'net_profit',
    'NETPROFIT_YOY': 'net_profit_yoy',
    'PARENT_NETPROFIT': 'parent_net_profit',
    'PARENT_NETPROFIT_YOY': 'parent_net_profit_yoy',
    'DEDUCT_PARENT_NETPROFIT': 'deducted_net_profit',
    'DEDUCT_PARENT_NETPROFIT_YOY': 'deducted_net_profit_yoy',
    'BASIC_EPS': 'basic_eps',
    'DILUTED_EPS': 'diluted_eps',
    'SALE_EXPENSE': 'sale_expense',
    'MANAGE_EXPENSE': 'manage_expense',
    'RESEARCH_EXPENSE': 'research_expense',
    'FINANCE_EXPENSE': 'finance_expense',
    'INVEST_INCOME': 'invest_income',
    'OPERATE_TAX_ADD': 'operate_tax_add',
    'INCOME_TAX': 'income_tax',
    'ASSET_IMPAIRMENT_LOSS': 'asset_impairment_loss',
}

IS_STRING_FIELDS = {'report_date', 'report_date_name'}

CF_COLUMNS = {
    'REPORT_DATE': 'report_date',
    'REPORT_DATE_NAME': 'report_date_name',
    'NETCASH_OPERATE': 'operating_cf',
    'NETCASH_INVEST': 'investing_cf',
    'NETCASH_FINANCE': 'financing_cf',
    'CONSTRUCT_LONG_ASSET': 'capex',
    'NETPROFIT': 'net_profit',
}

CF_STRING_FIELDS = {'report_date', 'report_date_name'}


def _normalize_balance_debt_fields(item: dict) -> None:
    """Treat absent borrowing line items as zero in balance-sheet displays."""
    if item.get('total_liabilities') is None:
        return
    for field in ('short_loan', 'long_loan'):
        if item.get(field) is None:
            item[field] = 0.0