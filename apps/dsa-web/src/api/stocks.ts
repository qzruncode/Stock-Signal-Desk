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
  amount_today: number | null;
  revenue_ttm: number | null;
  deducted_profit_ttm: number | null;
  operating_cf_ttm: number | null;
  net_profit_ttm: number | null;
  debt_ratio: number | null;
  interest_bearing_debt_ratio: number | null;
  cash_debt_ratio: number | null;
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
  missing_codes: string[];
  latest_trading_day: string | null;
}

export interface KlineBatchResponse {
  results: Record<string, KlineBatchBar[]>;
}

export interface KlineBatchBar {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  amount: number | null;
  pct_chg: number | null;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  volume_ratio: number | null;
  data_source: string | null;
}

export interface FundamentalFilterResponse {
  data: Record<string, {
    revenue_ttm: number | null;
    deducted_profit_ttm: number | null;
    operating_cf_ttm: number | null;
    net_profit_ttm: number | null;
    debt_ratio: number | null;
    interest_bearing_debt_ratio: number | null;
    cash_debt_ratio: number | null;
    report_date: string | null;
  }>;
}

export interface StockEnrichResponse {
  item: StockMetaItem | null;
  updated_sections: string[];
  errors: Record<string, string>;
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

  async syncList(): Promise<{ success: boolean; message: string; status: string }> {
    const response = await apiClient.post('/api/v1/stocks/sync/list');
    return response.data;
  },

  async syncListStatus(): Promise<SyncStatusResponse> {
    const response = await apiClient.get<SyncStatusResponse>('/api/v1/stocks/sync/list/status');
    return response.data;
  },

  async syncKline(): Promise<{ success: boolean; message: string; status: string }> {
    const response = await apiClient.post('/api/v1/stocks/sync/kline');
    return response.data;
  },

  async syncKlineStatus(): Promise<SyncStatusResponse> {
    const response = await apiClient.get<SyncStatusResponse>('/api/v1/stocks/sync/kline/status');
    return response.data;
  },

  async syncMissingKline(codes: string[]): Promise<SyncStatusResponse> {
    const response = await apiClient.post<SyncStatusResponse>('/api/v1/stocks/kline/sync-missing', { codes });
    return response.data;
  },

  async syncMissingKlineStatus(): Promise<SyncStatusResponse> {
    const response = await apiClient.get<SyncStatusResponse>('/api/v1/stocks/kline/sync-missing/status');
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

  async enrichStock(code: string, sections: Array<'valuation' | 'financial'>): Promise<StockEnrichResponse> {
    const response = await apiClient.post<StockEnrichResponse>(
      '/api/v1/stocks/enrich',
      { code, sections },
      { timeout: 240000 },
    );
    return response.data;
  },
};
