import apiClient from './index';

export interface IndustryCycleCheckItem {
  item: string;
  passed: boolean;
  reason: string;
  source?: string;
}

export interface IndustryCycleDetector {
  passed: boolean;
  conclusion: string;
  failed_reason?: string | null;
  checklist: IndustryCycleCheckItem[];
}

export interface IndustryCycleMatchedMainlineItem {
  name?: string;
  rank?: number | null;
  stage?: string | null;
  stage_hint?: string | null;
  score?: number | null;
  reason?: string | null;
}

export interface IndustryCycleSectorSnapshot {
  rank?: number | null;
  total?: number | null;
  change_pct?: number | null;
  leading_stock?: string | null;
  leading_stock_change_pct?: number | null;
  up_count?: number | null;
  down_count?: number | null;
}

export interface IndustryCycleFundFlowSnapshot {
  rank?: number | null;
  total?: number | null;
  main_net_inflow?: number | null;
  pct_chg?: number | null;
  leading_stock?: string | null;
}

export interface IndustryCyclePeerGroup {
  sample_size?: number | null;
  sample_names?: string[];
  source?: string | null;
  error?: string | null;
}

export interface IndustryCycleThemeDetail {
  name?: string;
  rank_label?: string | null;
  stage?: string | null;
  components?: string[];
  thesis?: string | null;
  stage_reason?: string | null;
  policy_signal?: string | null;
  industry_trend?: string | null;
  valuation_view?: string | null;
  expectation_view?: string | null;
  risks?: string[];
  evidence?: string[];
}

export interface IndustryCycleSentimentSnapshot {
  news_count?: number | null;
  research_count?: number | null;
  positive_research_count?: number | null;
  sentiment_score?: number | null;
  social_score?: number | null;
  discussion_count?: number | null;
}

export interface IndustryCycleRiskSnapshot {
  high_risk_count?: number | null;
  medium_risk_count?: number | null;
  top_risk_labels?: string[];
}

export interface IndustryCycleDataQuality {
  missing_fields?: string[];
  summary?: string | null;
}

export interface IndustryCycleValuationSnapshot {
  pe_ttm?: number | null;
  pb?: number | null;
  industry_name?: string | null;
  industry_pe?: number | null;
  industry_pb?: number | null;
  industry_sample_size?: number | null;
  pe_premium_vs_industry?: number | null;
  pb_premium_vs_industry?: number | null;
  price_overdraft_status?: string | null;
  price_overdraft_score?: number | null;
}

export interface IndustryCycleFinancialSnapshot {
  latest_report_date?: string | null;
  revenue?: number | null;
  revenue_yoy?: number | null;
  net_profit?: number | null;
  net_profit_yoy?: number | null;
  roe?: number | null;
  gross_margin?: number | null;
  debt_ratio?: number | null;
  eps?: number | null;
}

export interface IndustryCycleStockFocusSnapshot {
  focus_view?: string | null;
  business_binding_strength?: string | null;
  finance_state?: string | null;
  holder_state?: string | null;
  trading_state?: string | null;
  direct_evidence_strength?: string | null;
  finance_points?: string[];
  holder_points?: string[];
  trading_points?: string[];
}

export interface IndustryCycleResponse {
  symbol: string;
  industry_cycle: {
    stock_name: string;
    industry_name: string;
    analysis_status: '主线' | '分支主线' | '观察' | '退潮' | '非主线';
    beneficiary_level?: string;
    beneficiary_reason?: string;
    cycle_phase?: string;
    cycle_phase_reason?: string;
    prosperity_score: number;
    prosperity_judgement: string;
    core_logic: string;
    killer_reason?: string | null;
    observation_window: string;
    catalysts: string[];
    risks: string[];
    observation_points: string[];
    mainline_detector: IndustryCycleDetector;
    industry_beta_detector: IndustryCycleDetector;
    evidence: {
      stock_focus_snapshot?: IndustryCycleStockFocusSnapshot;
      market_mainline: {
        report_pending: boolean;
        market_stage?: Record<string, unknown>;
        matched_current_mainlines: IndustryCycleMatchedMainlineItem[];
        matched_future_mainlines: IndustryCycleMatchedMainlineItem[];
        current_theme_detail?: IndustryCycleThemeDetail;
        future_theme_detail?: IndustryCycleThemeDetail;
      };
      sector_snapshot: IndustryCycleSectorSnapshot;
      fund_flow: IndustryCycleFundFlowSnapshot;
      peer_group: IndustryCyclePeerGroup;
      financial_snapshot?: IndustryCycleFinancialSnapshot;
      valuation_snapshot?: IndustryCycleValuationSnapshot;
      sentiment_snapshot: IndustryCycleSentimentSnapshot;
      risk_snapshot: IndustryCycleRiskSnapshot;
      data_quality?: IndustryCycleDataQuality;
      driver_signals: Record<string, string[]>;
    };
  };
  report_pending?: boolean;
  llm_used?: boolean;
  model_used?: string | null;
  raw_stream_output?: string;
  raw_response?: string;
  debug_input?: {
    system_prompt?: string;
    user_prompt?: string;
    evidence_pack?: Record<string, unknown>;
  } | null;
  _fetched_at?: string;
  _cached?: boolean;
  fallback_used?: boolean;
}

export interface IndustryCycleReportTaskAccepted {
  task_id: string;
  status: 'pending' | 'processing';
  message?: string;
}

export const industryCycleApi = {
  async getIndustryCycle(
    symbol: string,
    force: boolean = false,
  ): Promise<IndustryCycleResponse> {
    const response = await apiClient.get<IndustryCycleResponse>(
      '/api/v1/stocks/industry-cycle',
      { params: { symbol, force }, timeout: 45000 },
    );
    return response.data;
  },

  async getIndustryCycleReport(
    symbol: string,
    force: boolean = false,
  ): Promise<IndustryCycleResponse> {
    const response = await apiClient.get<IndustryCycleResponse>(
      '/api/v1/stocks/industry-cycle/report',
      { params: { symbol, force }, timeout: 45000 },
    );
    return response.data;
  },

  async createIndustryCycleReportTask(
    symbol: string,
    force: boolean = true,
  ): Promise<IndustryCycleReportTaskAccepted> {
    const response = await apiClient.post<IndustryCycleReportTaskAccepted>(
      '/api/v1/stocks/industry-cycle/report/tasks',
      null,
      { params: { symbol, force }, timeout: 10000 },
    );
    return response.data;
  },
};
