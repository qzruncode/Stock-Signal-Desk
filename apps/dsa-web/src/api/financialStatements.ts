import apiClient from './index';

export interface BalanceSheetItem {
  report_date: string | null;
  report_date_name: string | null;
  total_assets: number | null;
  total_liabilities: number | null;
  total_equity: number | null;
  parent_equity: number | null;
  monetary_funds: number | null;
  accounts_receivable: number | null;
  inventory: number | null;
  contract_liabilities: number | null;
  fixed_asset: number | null;
  short_loan: number | null;
  long_loan: number | null;
  accounts_payable: number | null;
  noncurrent_liab_1year: number | null;
  lease_liab: number | null;
  total_current_assets: number | null;
  total_current_liabilities: number | null;
  debt_ratio: number | null;
  equity_multiplier: number | null;
}

export interface IncomeStatementItem {
  report_date: string | null;
  report_date_name: string | null;
  revenue: number | null;
  revenue_yoy?: number | null;
  total_cost: number | null;
  operate_cost: number | null;
  operate_profit: number | null;
  total_profit: number | null;
  net_profit: number | null;
  net_profit_yoy?: number | null;
  parent_net_profit: number | null;
  parent_net_profit_yoy?: number | null;
  deducted_net_profit: number | null;
  deducted_net_profit_yoy?: number | null;
  basic_eps: number | null;
  diluted_eps: number | null;
  sale_expense: number | null;
  manage_expense: number | null;
  research_expense: number | null;
  finance_expense: number | null;
  invest_income: number | null;
  operate_tax_add: number | null;
  income_tax: number | null;
  asset_impairment_loss?: number | null;
  gross_profit: number | null;
  gross_margin: number | null;
  net_margin: number | null;
}

export interface CashflowItem {
  report_date: string | null;
  report_date_name: string | null;
  operating_cf: number | null;
  investing_cf: number | null;
  financing_cf: number | null;
  capex: number | null;
  net_profit: number | null;
  free_cashflow: number | null;
  cf_quality: number | null;
}

export interface FinancialStatementsResponse {
  symbol: string;
  periods: number;
  balance_sheet: BalanceSheetItem[];
  income_statement: IncomeStatementItem[];
  cashflow: CashflowItem[];
  source?: string;
  _fetched_at?: string;
  _cached?: boolean;
  _errors?: string[];
}

export const financialStatementsApi = {
  async getStatements(
    symbol: string,
    periods: number = 12,
    force: boolean = false,
  ): Promise<FinancialStatementsResponse> {
    const response = await apiClient.get<FinancialStatementsResponse>(
      '/api/v1/stocks/financials/statements',
      { params: { symbol, periods, force }, timeout: 60000 },
    );
    return response.data;
  },
};
