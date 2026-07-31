# -*- coding: utf-8 -*-
"""同花顺 financial statements — balance sheet, income statement, cash flow."""

from __future__ import annotations

import logging

from api.v1.endpoints.financials._symbol import _safe_amount, _safe_float
from api.v1.endpoints.financials._fetch_statements import _pick_quarters

logger = logging.getLogger(__name__)


THS_DEBT_COLUMNS = {
    "报告期": "report_date",
    "*资产合计": "total_assets",
    "*负债合计": "total_liabilities",
    "*所有者权益（或股东权益）合计": "total_equity",
    "*归属于母公司所有者权益合计": "parent_equity",
    "货币资金": "monetary_funds",
    "应收账款": "accounts_receivable",
    "存货": "inventory",
    "合同负债": "contract_liabilities",
    "固定资产合计": "fixed_asset",
    "短期借款": "short_loan",
    "长期借款": "long_loan",
    "应付账款": "accounts_payable",
    "一年内到期的非流动负债": "noncurrent_liab_1year",
    "流动资产合计": "total_current_assets",
    "流动负债合计": "total_current_liabilities",
}

THS_BENEFIT_COLUMNS = {
    "报告期": "report_date",
    "*营业总收入": "revenue",
    "*营业总成本": "total_cost",
    "其中：营业成本": "operate_cost",
    "*净利润": "net_profit",
    "*归属于母公司所有者的净利润": "parent_net_profit",
    "*扣除非经常性损益后的净利润": "deducted_net_profit",
    "销售费用": "sale_expense",
    "管理费用": "manage_expense",
    "研发费用": "research_expense",
    "财务费用": "finance_expense",
    "营业税金及附加": "operate_tax_add",
    "资产减值损失": "asset_impairment_loss",
}

THS_CASH_COLUMNS = {
    "报告期": "report_date",
    "*经营活动产生的现金流量净额": "operating_cf",
    "*投资活动产生的现金流量净额": "investing_cf",
    "*筹资活动产生的现金流量净额": "financing_cf",
}

THS_STRING_FIELDS = {"report_date"}

THS_AMOUNT_FIELDS = {
    "total_assets",
    "total_liabilities",
    "total_equity",
    "parent_equity",
    "monetary_funds",
    "accounts_receivable",
    "inventory",
    "fixed_asset",
    "short_loan",
    "long_loan",
    "accounts_payable",
    "noncurrent_liab_1year",
    "lease_liab",
    "total_current_assets",
    "total_current_liabilities",
    "revenue",
    "total_cost",
    "operate_cost",
    "net_profit",
    "parent_net_profit",
    "deducted_net_profit",
    "deducted_profit",
    "sale_expense",
    "manage_expense",
    "research_expense",
    "finance_expense",
    "operate_tax_add",
    "operating_cf",
    "investing_cf",
    "financing_cf",
}


def _fetch_from_ths_triple(symbol: str, periods: int) -> dict:
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {
        "balance_sheet": [],
        "income_statement": [],
        "cashflow": [],
    }
    errors: list[str] = []

    def _parse_val(dst: str, val):
        if dst in THS_STRING_FIELDS:
            return str(val).strip() if val is not None and str(val) != "nan" else None
        if dst in THS_AMOUNT_FIELDS:
            if val is False or (isinstance(val, str) and val.strip() in ("False", "")):
                return None
            return _safe_amount(val)
        return _safe_float(val)

    # Balance Sheet
    try:
        df = ak.stock_financial_debt_ths(symbol=symbol, indicator="按报告期")
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in THS_DEBT_COLUMNS.items():
                    if src not in row.index:
                        continue
                    item[dst] = _parse_val(dst, row[src])
                if item.get("report_date"):
                    item["report_date"] = str(item["report_date"])[:10]
                ta = item.get("total_assets")
                tl = item.get("total_liabilities")
                te = item.get("total_equity")
                if ta and ta != 0:
                    if tl is not None:
                        item["debt_ratio"] = round(tl / ta * 100, 2)
                    if te is not None and te != 0:
                        item["equity_multiplier"] = round(ta / te, 2)
                result["balance_sheet"].append(item)
    except Exception as e:
        errors.append(f"balance: {e}")
        logger.warning(f"[FinancialStatements] THS triple balance failed for {symbol}: {e}")

    # Income Statement
    try:
        df = ak.stock_financial_benefit_ths(symbol=symbol, indicator="按报告期")
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in THS_BENEFIT_COLUMNS.items():
                    if src not in row.index:
                        continue
                    item[dst] = _parse_val(dst, row[src])
                if item.get("report_date"):
                    item["report_date"] = str(item["report_date"])[:10]
                rev = item.get("revenue")
                oc = item.get("operate_cost")
                if rev and oc and rev != 0:
                    item["gross_profit"] = rev - oc
                    item["gross_margin"] = round((rev - oc) / rev * 100, 2)
                np_val = item.get("net_profit")
                if rev and np_val and rev != 0:
                    item["net_margin"] = round(np_val / rev * 100, 2)
                result["income_statement"].append(item)
    except Exception as e:
        errors.append(f"income: {e}")
        logger.warning(f"[FinancialStatements] THS triple income failed for {symbol}: {e}")

    # Cash Flow
    try:
        df = ak.stock_financial_cash_ths(symbol=symbol, indicator="按报告期")
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in THS_CASH_COLUMNS.items():
                    if src not in row.index:
                        continue
                    item[dst] = _parse_val(dst, row[src])
                if item.get("report_date"):
                    item["report_date"] = str(item["report_date"])[:10]
                ocf = item.get("operating_cf")
                if ocf is not None:
                    item["free_cashflow"] = ocf
                result["cashflow"].append(item)
    except Exception as e:
        errors.append(f"cashflow: {e}")
        logger.warning(f"[FinancialStatements] THS triple cashflow failed for {symbol}: {e}")

    total = sum(len(result[k]) for k in result)
    if total == 0:
        raise ValueError(f"同花顺三表均返回空: {'; '.join(errors)}")

    result["source"] = "同花顺"
    logger.info(
        f"[FinancialStatements] THS triple {_time.time() - t0:.1f}s for {symbol}: "
        f"BS={len(result['balance_sheet'])} IS={len(result['income_statement'])} CF={len(result['cashflow'])}"
    )
    return result
