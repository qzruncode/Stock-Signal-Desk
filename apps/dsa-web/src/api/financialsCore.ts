import apiClient from './index';

export interface FinancialItem {
  report_date: string | null;
  net_profit: number | null;
  net_profit_yoy: number | null;
  parent_net_profit: number | null;
  parent_net_profit_yoy: number | null;
  deducted_profit: number | null;
  deducted_profit_yoy: number | null;
  deducted_net_profit: number | null;
  deducted_net_profit_yoy: number | null;
  revenue: number | null;
  revenue_yoy: number | null;
  revenue_qoq: number | null;
  eps: number | null;
  bps: number | null;
  capital_reserve_per_share: number | null;
  undistributed_profit_per_share: number | null;
  operating_cf_per_share: number | null;
  net_margin: number | null;
  gross_margin: number | null;
  roe: number | null;
  roe_diluted: number | null;
  operating_cycle: number | null;
  inventory_turnover: number | null;
  inventory_turnover_days: number | null;
  receivables_turnover_days: number | null;
  current_ratio: number | null;
  quick_ratio: number | null;
  conservative_quick_ratio: number | null;
  equity_ratio: number | null;
  debt_ratio: number | null;
  operating_cash_flow: number | null;
  accounts_receivable: number | null;
  inventory: number | null;
  contract_liabilities: number | null;
  asset_impairment_loss: number | null;
}

export interface FinancialsResponse {
  symbol: string;
  periods: number;
  items: FinancialItem[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const financialsApi = {
  async getFinancials(
    symbol: string,
    periods: number = 12,
    force: boolean = false,
  ): Promise<FinancialsResponse> {
    const response = await apiClient.get<FinancialsResponse>(
      '/api/v1/stocks/financials',
      { params: { symbol, periods, force }, timeout: 30000 },
    );
    return response.data;
  },
};
