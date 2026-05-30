import apiClient from './index';

export interface RealtimeQuote {
  code: string;
  name: string;
  source: string;
  price: number | null;
  change_pct: number | null;
  change_amount: number | null;
  volume: number | null;
  amount: number | null;
  volume_ratio: number | null;
  turnover_rate: number | null;
  amplitude: number | null;
  open_price: number | null;
  high: number | null;
  low: number | null;
  pre_close: number | null;
  pe_ratio: number | null;
  pb_ratio: number | null;
  total_mv: number | null;
  circ_mv: number | null;
  change_60d: number | null;
  high_52w: number | null;
  low_52w: number | null;
}

export interface RealtimeQuotesResponse {
  items: RealtimeQuote[];
  total: number;
}

export const quotesApi = {
  async getRealtime(symbol: string): Promise<RealtimeQuotesResponse> {
    const response = await apiClient.get<RealtimeQuotesResponse>(
      '/api/v1/quotes/realtime',
      { params: { symbol }, timeout: 45000 },
    );
    return response.data;
  },
};
