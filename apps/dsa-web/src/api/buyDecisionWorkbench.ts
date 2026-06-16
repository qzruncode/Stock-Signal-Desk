import apiClient from './index';

export type BuyDecisionStepKey =
  | 'profile_mapping'
  | 'industry_beta'
  | 'mainline_position'
  | 'company_benefit'
  | 'buy_constraints'
  | 'final_decision';

export type BuyDecisionStepStatus = 'idle' | 'running' | 'success' | 'failed';

export interface BuyDecisionChecklistItem {
  item: string;
  passed: boolean;
  reason: string;
  source: string;
}

export interface BuyDecisionDetector {
  passed: boolean;
  conclusion: string;
  failed_reason?: string | null;
  checklist: BuyDecisionChecklistItem[];
}

export interface BuyDecisionStepState<T = unknown> {
  status: BuyDecisionStepStatus;
  from_cache?: boolean;
  started_at?: string | null;
  finished_at?: string | null;
  summary?: string | null;
  error?: string | null;
  blocking?: boolean;
  next_step_enabled?: boolean;
  data?: T;
}

export interface BuyDecisionWorkbenchResponse {
  session_id: string;
  symbol: string;
  stock_name: string;
  industry_name: string;
  current_step: BuyDecisionStepKey;
  steps: Record<BuyDecisionStepKey, BuyDecisionStepState>;
}

export interface BuyDecisionRunStepResponse<T = unknown> extends BuyDecisionStepState<T> {
  session_id: string;
  step: BuyDecisionStepKey;
}

export interface BuyDecisionReportResponse {
  session_id: string;
  symbol: string;
  buy_decision: {
    decision: '可买入' | '可跟踪等待' | '暂不买入' | '禁止追高';
    decision_reason: string;
    entry_type: '趋势跟随' | '分歧低吸' | '右侧确认' | '仅观察' | '禁止参与' | string;
    not_buy_reasons: string[];
    must_watch_points: string[];
  };
  industry_cycle: {
    analysis_status: '主线' | '分支主线' | '观察' | '退潮' | '非主线' | string;
    beneficiary_level?: string;
    cycle_phase?: string;
    prosperity_score?: number;
    prosperity_judgement?: string;
    core_logic?: string;
  };
  final_summary: string;
  raw_stream_output?: string;
  model_used?: string | null;
}

export const buyDecisionWorkbenchApi = {
  async createWorkbench(symbol: string, forceReset: boolean = false): Promise<BuyDecisionWorkbenchResponse> {
    const response = await apiClient.post<BuyDecisionWorkbenchResponse>(
      '/api/v1/stocks/buy-decision/workbench',
      { symbol, force_reset: forceReset },
      { timeout: 15000 },
    );
    return response.data;
  },

  async getWorkbench(sessionId: string): Promise<BuyDecisionWorkbenchResponse> {
    const response = await apiClient.get<BuyDecisionWorkbenchResponse>(
      `/api/v1/stocks/buy-decision/workbench/${sessionId}`,
      { timeout: 15000 },
    );
    return response.data;
  },

  async runStep<T = unknown>(
    sessionId: string,
    stepKey: BuyDecisionStepKey,
    force: boolean = false,
  ): Promise<BuyDecisionRunStepResponse<T>> {
    const response = await apiClient.post<BuyDecisionRunStepResponse<T>>(
      `/api/v1/stocks/buy-decision/workbench/${sessionId}/steps/${stepKey}/run`,
      { force },
      { timeout: 45000 },
    );
    return response.data;
  },

  async getReport(sessionId: string): Promise<BuyDecisionReportResponse> {
    const response = await apiClient.get<BuyDecisionReportResponse>(
      `/api/v1/stocks/buy-decision/workbench/${sessionId}/report`,
      { timeout: 45000 },
    );
    return response.data;
  },

  async runStepStreaming(sessionId: string): Promise<{ task_id: string; status: string }> {
    const response = await apiClient.post<{ task_id: string; status: string }>(
      `/api/v1/stocks/buy-decision/workbench/${sessionId}/steps/industry_beta/run-streaming`,
      {},
      { timeout: 15000 },
    );
    return response.data;
  },
};
