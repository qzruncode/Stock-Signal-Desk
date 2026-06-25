import apiClient from './index';

export interface ProfitForecast {
  year: string;
  eps: number;
  pe: number | null;
}

export interface ResearchReportItem {
  title: string;
  org: string;
  rating: string;
  industry: string;
  publish_date: string | null;
  url: string;
  profit_forecasts: ProfitForecast[];
  monthly_report_count: number | null;
}

export interface ResearchReportResponse {
  symbol: string;
  days: number;
  items: ResearchReportItem[];
  analysis?: Record<string, unknown>;
  errors?: string[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const researchReportApi = {
  async getResearchReports(
    symbol: string,
    days: number = 1095,
    force: boolean = false,
  ): Promise<ResearchReportResponse> {
    const response = await apiClient.get<ResearchReportResponse>(
      '/api/v1/stocks/research-report',
      { params: { symbol, days, force }, timeout: 45000 },
    );
    return response.data;
  },
};
