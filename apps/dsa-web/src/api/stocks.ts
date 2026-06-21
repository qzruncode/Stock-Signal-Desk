import apiClient from './index';

export interface StockMetaItem {
  code: string;
  name: string;
  market: string;
  sector: string | null;
  area: string | null;
  status: string;
  ipo_date: string | null;
  total_market_cap: number | null;
  circulating_market_cap: number | null;
  pe_ttm: number | null;
  pb: number | null;
  last_sync_at: string | null;
}

export interface StocksListResponse {
  items: StockMetaItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface SyncStatusResponse {
  status: 'idle' | 'running' | 'syncing_kline' | 'success' | 'failed';
  progress: number;
  total: number;
  kline_progress: number;
  kline_total: number;
  started_at: string | null;
  finished_at: string | null;
  message: string;
  error: string | null;
}

export interface KlineStatusResponse {
  total_stocks: number;
  stocks_with_kline: number;
  missing: number;
  latest_trading_day: string | null;
}

export interface KlineBatchResponse {
  results: Record<string, Array<[string, number, number, number, number]>>;
}

export interface FundamentalFilterResponse {
  data: Record<string, {
    revenue_ttm: number | null;
    deducted_profit_ttm: number | null;
    debt_ratio: number | null;
    report_date: string | null;
  }>;
}

export const stocksApi = {
  async list(params?: {
    page?: number;
    page_size?: number;
    search?: string;
    market?: string;
  }): Promise<StocksListResponse> {
    const response = await apiClient.get<StocksListResponse>('/api/v1/stocks', { params });
    return response.data;
  },

  async sync(): Promise<{ success: boolean; message: string; status: string }> {
    const response = await apiClient.post('/api/v1/stocks/sync');
    return response.data;
  },

  async syncStatus(): Promise<SyncStatusResponse> {
    const response = await apiClient.get<SyncStatusResponse>('/api/v1/stocks/sync/status');
    return response.data;
  },

  async count(): Promise<{ total: number }> {
    const response = await apiClient.get<{ total: number }>('/api/v1/stocks/count');
    return response.data;
  },

  async getKlineStatus(): Promise<KlineStatusResponse> {
    const response = await apiClient.get<KlineStatusResponse>('/api/v1/stocks/kline-status');
    return response.data;
  },

  async getKlineBatch(codes: string[], count: number = 250): Promise<KlineBatchResponse> {
    const response = await apiClient.post<KlineBatchResponse>(
      '/api/v1/stocks/kline/batch',
      { codes, count },
      { timeout: 30000 },
    );
    return response.data;
  },

  async fundamentalFilter(codes: string[]): Promise<FundamentalFilterResponse> {
    const response = await apiClient.post<FundamentalFilterResponse>(
      '/api/v1/stocks/fundamental-filter',
      { codes },
      { timeout: 240000 }, // 4min > backend 3min timeout, leave margin for slow fetches
    );
    return response.data;
  },
};
