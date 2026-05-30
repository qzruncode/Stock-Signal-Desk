import apiClient from './index';

export interface KlineBar {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  amount: number;
  pct_chg: number | null;
  turnover_rate: number | null;
  _source?: string;
}

export interface KlineResponse {
  symbol: string;
  source: string;
  count: number;
  data: KlineBar[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const klineApi = {
  async getKline(symbol: string, count: number = 500): Promise<KlineResponse> {
    const response = await apiClient.get<KlineResponse>(
      '/api/v1/kline',
      { params: { symbol, count }, timeout: 45000 },
    );
    return response.data;
  },

  async getHistoryData(symbol: string, startDate: string, endDate: string): Promise<KlineResponse> {
    const response = await apiClient.get<KlineResponse>(
      '/api/v1/kline/history',
      { params: { symbol, start_date: startDate, end_date: endDate }, timeout: 45000 },
    );
    return response.data;
  },
};
