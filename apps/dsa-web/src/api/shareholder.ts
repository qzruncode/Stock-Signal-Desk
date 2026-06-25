import apiClient from './index';

export interface TopHolderItem {
  name: string;
  holding_pct: number | null;
  holding_amount: number | null;
  holder_type: string | null;
  change: string | null;
}

export interface MajorHolderChangeItem {
  date: string | null;
  holder: string;
  direction: string | null;
  shares: number | null;
  pct: number | null;
  price: number | null;
}

export interface ShareholderStructureResponse {
  symbol: string;
  holder_count: number | null;
  holder_count_previous: number | null;
  holder_count_change: number | null;
  holder_count_change_pct: number | null;
  holder_report_date: string | null;
  top10_holders: TopHolderItem[];
  institution_holding_pct: number | null;
  major_holder_changes: MajorHolderChangeItem[];
  actual_controller: string | null;
  source_chain?: string[];
  errors?: string[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const shareholderApi = {
  async getShareholderStructure(
    symbol: string,
    force: boolean = false,
  ): Promise<ShareholderStructureResponse> {
    const response = await apiClient.get<ShareholderStructureResponse>(
      '/api/v1/stocks/shareholder-structure',
      { params: { symbol, force }, timeout: 45000 },
    );
    return response.data;
  },
};
