import apiClient from './index';

export interface SectorItem {
  name: string;
  code: string;
  change_pct: number | null;
  net_flow: number | null;
  up_count: number | null;
  down_count: number | null;
  lead_stock: string;
  lead_stock_price: number | null;
  lead_stock_change_pct: number | null;
  total_amount: number | null;
  [key: string]: unknown;
}

export interface SectorListResponse {
  type: string;
  items: SectorItem[];
  _fetched_at?: string;
  _cached?: boolean;
}

export type SectorType = 'industry' | 'concept' | 'region';

export const sectorsApi = {
  async getSectors(type: SectorType = 'industry', force: boolean = false): Promise<SectorListResponse> {
    const response = await apiClient.get<SectorListResponse>(
      '/api/v1/market/sectors',
      { params: { type, force }, timeout: 30000 },
    );
    return response.data;
  },
};
