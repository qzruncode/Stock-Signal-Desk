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
  status: 'idle' | 'running' | 'success' | 'failed';
  progress: number;
  total: number;
  started_at: string | null;
  finished_at: string | null;
  message: string;
  error: string | null;
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
};
