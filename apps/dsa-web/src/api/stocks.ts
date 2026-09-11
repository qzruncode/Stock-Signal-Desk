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

export interface StocksListParams {
  page?: number;
  page_size?: number;
  search?: string;
  market?: string;
  signal?: AbortSignal;
}

export const stocksApi = {
  async list(params?: StocksListParams): Promise<StocksListResponse & { has_more?: boolean }> {
    const { signal, ...query } = params || {};
    const response = await apiClient.get<StocksListResponse & { has_more?: boolean }>('/api/v1/stocks', {
      params: query,
      signal,
    });
    return response.data;
  },

  async listAll(params?: Omit<StocksListParams, 'page' | 'page_size'>): Promise<StockMetaItem[]> {
    const pageSize = 500;
    const firstPage = await stocksApi.list({
      ...params,
      page: 1,
      page_size: pageSize,
    });
    const items = [...firstPage.items];

    for (let page = 2; page <= firstPage.total_pages; page += 1) {
      const nextPage = await stocksApi.list({
        ...params,
        page,
        page_size: pageSize,
      });
      items.push(...nextPage.items);
    }

    return items;
  },

};
