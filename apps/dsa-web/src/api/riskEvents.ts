import apiClient from './index';

export interface RiskEventItem {
  title: string;
  summary: string;
  risk_summary: string;
  date: string | null;
  source: string;
  source_type: 'news' | 'announcement';
  url: string;
  severity: 'high' | 'medium' | 'low';
  risk_category: string;
  risk_label: string;
  event_type: string;
  tags: string[];
}

export interface RiskEventsResponse {
  symbol: string;
  days: number;
  items: RiskEventItem[];
  analysis: {
    total_events: number;
    severity_distribution: {
      high: number;
      medium: number;
      low: number;
    };
    source_distribution?: {
      news: number;
      announcement: number;
    };
    top_risk_labels: string[];
    high_severity_titles: string[];
    ai_summary_hints: string[];
  };
  source_chain?: string[];
  errors?: string[];
  _fetched_at?: string;
  _cached?: boolean;
}

export const riskEventsApi = {
  async getRiskEvents(
    symbol: string,
    days: number = 90,
    force: boolean = false,
  ): Promise<RiskEventsResponse> {
    const response = await apiClient.get<RiskEventsResponse>(
      '/api/v1/stocks/risk-events',
      { params: { symbol, days, force }, timeout: 45000 },
    );
    return response.data;
  },
};
