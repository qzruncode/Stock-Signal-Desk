import apiClient from './index';

export interface StockMetaItem {
  code: string;
  name: string;
  market: string;
  sector: string | null;
  status: string;
  ipo_date: string | null;
  revenue_latest: number | null;
  net_profit_latest: number | null;
  operating_cf_latest: number | null;
  debt_ratio: number | null;
  financial_fetched_at: string | null;
  report_date: string | null;
  last_sync_at: string | null;
}

export interface StocksListResponse {
  items: StockMetaItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export const stocksApi = {
  async list(params?: {
    page?: number;
    page_size?: number;
    search?: string;
    market?: string;
    count?: boolean;
    signal?: AbortSignal;
  }): Promise<StocksListResponse & { has_more?: boolean }> {
    const { signal, ...query } = params || {};
    const response = await apiClient.get<StocksListResponse & { has_more?: boolean }>('/api/v1/stocks', {
      params: query,
      signal,
    });
    return response.data;
  },

};
