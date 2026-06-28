# -*- coding: utf-8 -*-
"""新浪财经 fallback financial statements — balance sheet, income statement, cash flow."""

from __future__ import annotations

import logging

from api.v1.endpoints.financials._symbol import _to_em_symbol, _safe_float
from api.v1.endpoints.financials._fetch_statements import _pick_quarters

logger = logging.getLogger(__name__)


SINA_BS_COLUMNS = {
    '报告日': 'report_date',
    '资产总计': 'total_assets',
    '负债合计': 'total_liabilities',
    '所有者权益(或股东权益)合计': 'total_equity',
    '归属于母公司股东权益合计': 'parent_equity',
    '货币资金': 'monetary_funds',
    '应收账款': 'accounts_receivable',
    '存货': 'inventory',
    '合同负债': 'contract_liabilities',
    '固定资产及清理合计': 'fixed_asset',
    '短期借款': 'short_loan',
    '长期借款': 'long_loan',
    '应付账款': 'accounts_payable',
    '一年内到期的非流动负债': 'noncurrent_liab_1year',
    '租赁负债': 'lease_liab',
    '流动资产合计': 'total_current_assets',
    '流动负债合计': 'total_current_liabilities',
}

SINA_IS_COLUMNS = {
    '报告日': 'report_date',
    '一、营业总收入': 'revenue',
    '二、营业总成本': 'total_cost',
    '其中：营业成本': 'operate_cost',
    '五、净利润': 'net_profit',
    '归属于母公司所有者的净利润': 'parent_net_profit',
    '扣除非经常性损益后的净利润': 'deducted_net_profit',
    '销售费用': 'sale_expense',
    '管理费用': 'manage_expense',
    '研发费用': 'research_expense',
    '财务费用': 'finance_expense',
    '营业税金及附加': 'operate_tax_add',
    '资产减值损失': 'asset_impairment_loss',
}

SINA_CF_COLUMNS = {
    '报告日': 'report_date',
    '经营活动产生的现金流量净额': 'operating_cf',
    '投资活动产生的现金流量净额': 'investing_cf',
    '筹资活动产生的现金流量净额': 'financing_cf',
}

SINA_STRING_FIELDS = {'report_date'}


def _fetch_from_sina_full(symbol: str, periods: int) -> dict:
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {
        'balance_sheet': [],
        'income_statement': [],
        'cashflow': [],
    }
    errors: list[str] = []

    em_symbol = _to_em_symbol(symbol)
    sina_stock = em_symbol.lower()

    # Balance Sheet
    try:
        df = ak.stock_financial_report_sina(stock=sina_stock, symbol='资产负债表')
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in SINA_BS_COLUMNS.items():
                    if src not in row.index:
                        continue
                    val = row[src]
                    if dst in SINA_STRING_FIELDS:
                        item[dst] = str(val).strip() if val is not None and str(val) != 'nan' else None
                    else:
                        item[dst] = _safe_float(val)
                if item.get('report_date'):
                    item['report_date'] = str(item['report_date'])[:4] + '-' + \
                                          str(item['report_date'])[4:6] + '-' + \
                                          str(item['report_date'])[6:8]
                ta = item.get('total_assets')
                tl = item.get('total_liabilities')
                te = item.get('total_equity')
                if ta and ta != 0:
                    if tl is not None:
                        item['debt_ratio'] = round(tl / ta * 100, 2)
                    if te is not None and te != 0:
                        item['equity_multiplier'] = round(ta / te, 2)
                result['balance_sheet'].append(item)
    except Exception as e:
        errors.append(f"balance: {e}")
        logger.warning(f"[FinancialStatements] Sina balance failed for {symbol}: {e}")

    # Income Statement
    try:
        df = ak.stock_financial_report_sina(stock=sina_stock, symbol='利润表')
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in SINA_IS_COLUMNS.items():
                    if src not in row.index:
                        continue
                    val = row[src]
                    if dst in SINA_STRING_FIELDS:
                        item[dst] = str(val).strip() if val is not None and str(val) != 'nan' else None
                    else:
                        item[dst] = _safe_float(val)
                if item.get('report_date'):
                    item['report_date'] = str(item['report_date'])[:4] + '-' + \
                                          str(item['report_date'])[4:6] + '-' + \
                                          str(item['report_date'])[6:8]
                rev = item.get('revenue')
                oc = item.get('operate_cost')
                if rev and oc and rev != 0:
                    item['gross_profit'] = rev - oc
                    item['gross_margin'] = round((rev - oc) / rev * 100, 2)
                np_val = item.get('net_profit')
                if rev and np_val and rev != 0:
                    item['net_margin'] = round(np_val / rev * 100, 2)
                result['income_statement'].append(item)
    except Exception as e:
        errors.append(f"income: {e}")
        logger.warning(f"[FinancialStatements] Sina income failed for {symbol}: {e}")

    # Cash Flow
    try:
        df = ak.stock_financial_report_sina(stock=sina_stock, symbol='现金流量表')
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in SINA_CF_COLUMNS.items():
                    if src not in row.index:
                        continue
                    val = row[src]
                    if dst in SINA_STRING_FIELDS:
                        item[dst] = str(val).strip() if val is not None and str(val) != 'nan' else None
                    else:
                        item[dst] = _safe_float(val)
                if item.get('report_date'):
                    item['report_date'] = str(item['report_date'])[:4] + '-' + \
                                          str(item['report_date'])[4:6] + '-' + \
                                          str(item['report_date'])[6:8]
                ocf = item.get('operating_cf')
                if ocf is not None:
                    item['free_cashflow'] = ocf
                result['cashflow'].append(item)
    except Exception as e:
        errors.append(f"cashflow: {e}")
        logger.warning(f"[FinancialStatements] Sina cashflow failed for {symbol}: {e}")

    total = sum(len(result[k]) for k in result)
    if total == 0:
        raise ValueError(f"新浪财经三表均返回空: {'; '.join(errors)}")

    result['source'] = '新浪财经'
    logger.info(f"[FinancialStatements] Sina {_time.time() - t0:.1f}s for {symbol}: "
                f"BS={len(result['balance_sheet'])} IS={len(result['income_statement'])} CF={len(result['cashflow'])}")
    return result