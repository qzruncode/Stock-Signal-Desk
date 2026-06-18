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

export type EnvironmentSignal = '利好' | '利空' | '中性';

export interface EnvironmentDimension {
  signal: EnvironmentSignal;
  summary: string;
  factors: string[];
}

export interface EnvironmentAnalysis {
  policy: EnvironmentDimension;
  technology: EnvironmentDimension;
  demand: EnvironmentDimension;
  supply_competition: EnvironmentDimension;
  macro_context: string;
  llm_used: boolean;
  model?: string;
  raw_text?: string;
  llm_input?: string;
  error?: string;
}

export interface PeerSnapshot {
  name: string;
  symbol: string;
  revenue_growth: number | null;
  gross_margin_trend: string;
  net_profit_growth: number | null;
}

export interface TrackDimension {
  verdict: string;
  evidence: string;
}

export interface TrackQualityAnalysis {
  cycle_position: TrackDimension;
  growth_potential: TrackDimension;
  competition_intensity: TrackDimension;
  overall_verdict: string;
  peer_snapshot: PeerSnapshot[];
  llm_used: boolean;
  model?: string;
  raw_text?: string;
  llm_input?: string;
  error?: string;
}

export interface CatalystItem {
  type: string;
  description: string;
  timeframe: string;
  confidence: '高' | '中' | '低';
  impact: '重大' | '中等' | '有限';
}

export interface CatalystAnalysis {
  overall_assessment: '催化充分' | '催化一般' | '催化不足';
  summary: string;
  catalysts: CatalystItem[];
  key_dates: string[];
  risks: string[];
  llm_used: boolean;
  model?: string;
  raw_text?: string;
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
  environment_analysis?: EnvironmentAnalysis;
  track_quality?: TrackQualityAnalysis;
  catalyst_analysis?: CatalystAnalysis;
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
