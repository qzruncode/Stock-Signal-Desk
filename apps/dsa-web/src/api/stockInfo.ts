import apiClient from './index';

export interface StockInfo {
  symbol: string;
  // From cninfo (primary source)
  name: string;
  short_name: string;
  industry: string;
  market: string;
  listing_date: string;
  establish_date: string;
  register_capital: number | null;
  main_business: string;
  business_scope: string;
  profile: string;
  website: string;
  // From East Money (supplementary, may be null)
  total_shares: number | null;
  circ_shares: number | null;
  pe_dynamic: number | null;
  pe_static: number | null;
  pb_ratio: number | null;
  total_mv: number | null;
  circ_mv: number | null;
  // Metadata
  _fetched_at?: string;
  _cached?: boolean;
  _cninfo_ok?: boolean;
  _em_ok?: boolean;
}

export const stockInfoApi = {
  async getInfo(symbol: string, force: boolean = false): Promise<StockInfo> {
    const response = await apiClient.get<StockInfo>(
      '/api/v1/stocks/info',
      { params: { symbol, force }, timeout: 30000 },
    );
    return response.data;
  },
};
