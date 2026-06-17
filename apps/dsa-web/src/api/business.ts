import apiClient from './index';

export interface BusinessIntro {
  main_business: string | null;
  business_scope: string | null;
  product_type: string | null;
  product_name: string | null;
}

export interface BusinessCompositionItem {
  report_date: string;
  category_type: string;
  business_name: string;
  revenue: number | null;
  revenue_pct: number | null;
  cost: number | null;
  cost_pct: number | null;
  profit: number | null;
  profit_pct: number | null;
  gross_margin: number | null;
}

export interface AnalystForecast {
  analyst: string;
  researcher: string;
  eps_2026: number | null;
  eps_2027: number | null;
  eps_2028: number | null;
  net_profit_2026: number | null;
  net_profit_2027: number | null;
  net_profit_2028: number | null;
  report_date: string;
}

export interface MetricPeriodValue {
  period: string;
  value: number | null;
}

export interface KeyMetric {
  name: string;
  values: MetricPeriodValue[];
}

export interface GrowthRate {
  period: string;
  growth_rate: number;
}

export interface GrowthRateItem {
  name: string;
  rates: GrowthRate[];
}

export interface FinancialSummary {
  key_metrics: KeyMetric[];
  growth_rates: GrowthRateItem[];
}

export interface LlmAnalysis {
  llm_used: boolean;
  model?: string;
  analysis?: string;
  llm_input?: string;
  error?: string;
}

export interface BusinessResponse {
  symbol: string;
  intro: BusinessIntro;
  composition: BusinessCompositionItem[];
  profit_forecast: AnalystForecast[];
  financial_summary: FinancialSummary;
  events: {
    announcements: { title: string; type: string; date: string }[];
    news: { title: string; content: string; source: string; time: string }[];
  };
  llm_analysis: LlmAnalysis;
  _fetched_at?: string;
  _cached?: boolean;
}

export const businessApi = {
  async getBusiness(symbol: string, force: boolean = false): Promise<BusinessResponse> {
    const response = await apiClient.get<BusinessResponse>(
      '/api/v1/stocks/business',
      { params: { symbol, force }, timeout: 120000 },
    );
    return response.data;
  },
};
