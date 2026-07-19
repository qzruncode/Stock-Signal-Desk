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
    revenue_latest: number | null;
    net_profit_latest: number | null;
    operating_cf_latest: number | null;
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
