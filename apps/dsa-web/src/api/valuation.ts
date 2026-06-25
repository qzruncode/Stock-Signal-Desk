import apiClient from './index';

export interface ValuationRatiosResponse {
  symbol: string;
  trade_date: string | null;
  pe_static: number | null;
  pe_dynamic: number | null;
  pe_ttm: number | null;
  pb: number | null;
  ps: number | null;
  pcf: number | null;
  peg: number | null;
  dividend_yield: number | null;
  dividend_date?: string | null;
  pe_percentiles: Record<string, number>;
  industry?: string | null;
  industry_average: {
    industry: string | null;
    pe: number | null;
    pb: number | null;
    sample_size: number;
  };
  price_overdraft_signal?: {
    status: 'low' | 'watch' | 'medium' | 'high' | 'uncertain';
    score: number;
    confidence: number;
    valuation_expensive_score: number;
    expectation_support_score: number;
    signals: string[];
    metrics: {
      pe_ttm: number | null;
      pe_dynamic: number | null;
      pb: number | null;
      peg: number | null;
      dividend_yield: number | null;
      pe_percentile_1y: number | null;
      pe_percentile_3y: number | null;
      pe_percentile_5y: number | null;
      industry_pe: number | null;
      industry_pb: number | null;
      pe_premium_vs_industry: number | null;
      pb_premium_vs_industry: number | null;
      dynamic_pe_discount_vs_ttm: number | null;
    };
    reasoning: string[];
    limitations: string[];
  };
  source_chain?: string[];
  errors?: string[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const valuationApi = {
  async getValuationRatios(
    symbol: string,
    withHistory: boolean = true,
    force: boolean = false,
  ): Promise<ValuationRatiosResponse> {
    const response = await apiClient.get<ValuationRatiosResponse>(
      '/api/v1/stocks/valuation-ratios',
      { params: { symbol, with_history: withHistory, force }, timeout: 45000 },
    );
    return response.data;
  },
};
