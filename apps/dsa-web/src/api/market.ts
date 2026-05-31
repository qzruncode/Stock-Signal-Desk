import apiClient from './index';

export interface ShIndex {
  date: string;
  close: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  amount: number | null;
}

export interface MarketStatus {
  is_trading_time: boolean;
  up_count: number;
  down_count: number;
  flat_count: number;
  limit_up_count: number;
  limit_down_count: number;
  total_amount: number;
  north_flow: number;
  sh_index?: ShIndex;
  _fetched_at?: string;
  _cached?: boolean;
}

export const marketApi = {
  async getStatus(useCache: boolean = true): Promise<MarketStatus> {
    const response = await apiClient.get<MarketStatus>(
      '/api/v1/market/status',
      { params: { use_cache: useCache }, timeout: 30000 },
    );
    return response.data;
  },
};
