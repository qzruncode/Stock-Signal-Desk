// apps/dsa-web/src/api/buyCriteria.ts
import apiClient from './index';

// ── Types ────────────────────────────────────────────────────────────────

export type CriterionId =
  | 'mainline_position'
  | 'prosperity_cycle'
  | 'growth_space'
  | 'competition_landscape'
  | 'growth_drivers'
  | 'catalyst_events'
  | 'valuation_level'
  | 'fatal_risks';

export type CriterionStatus = 'idle' | 'running' | 'pass' | 'fail' | 'not_evaluated';

export interface CriterionEvidence {
  raw_data: Record<string, unknown>;
  data_summary: string;
}

export interface CriterionResult {
  criterion_id: CriterionId;
  criterion_name: string;
  index: number;
  passed: boolean;
  verdict: string;
  evidence: CriterionEvidence;
  prompt_text: string;
  analyzed_at: string;
}

export interface CriterionStartEvent {
  criterion_id: CriterionId;
  criterion_name: string;
  index: number;
}

export interface AnalysisCompleteEvent {
  final_decision: '可买入' | '不可买入';
  passed_count: number;
  failed_count: number;
  not_evaluated_count: number;
  stopped_at: CriterionId | null;
  summary: string;
}

export interface AnalysisErrorEvent {
  criterion_id: string;
  message: string;
}

// SSE event types
export type CriteriaSSEEventType =
  | 'criterion_start'
  | 'criterion_complete'
  | 'analysis_complete'
  | 'error';

// ── Criterion metadata (ordered) ────────────────────────────────────────

export const CRITERIA_ORDER: { id: CriterionId; name: string }[] = [
  { id: 'mainline_position', name: '市场主线属性' },
  { id: 'prosperity_cycle', name: '景气上行周期' },
  { id: 'growth_space', name: '未来3年空间' },
  { id: 'competition_landscape', name: '竞争格局' },
  { id: 'growth_drivers', name: '驱动因素' },
  { id: 'catalyst_events', name: '催化事件' },
  { id: 'valuation_level', name: '估值水位' },
  { id: 'fatal_risks', name: '致命风险' },
];

// ── Cached record response ─────────────────────────────────────────────

export interface CachedCriteriaResponse {
  id: number;
  symbol: string;
  trade_date: string;
  stock_name: string | null;
  final_decision: '可买入' | '不可买入';
  passed_count: number;
  failed_count: number;
  not_evaluated_count: number;
  stopped_at: string | null;
  summary: string;
  results: CriterionResult[];
  created_at: string;
}

// ── API ─────────────────────────────────────────────────────────────────

export const buyCriteriaApi = {
  /** Get the SSE URL for criteria analysis, optionally including pre-fetched data. */
  getCriteriaStreamUrl(
    symbol: string,
    preFetchedData?: Record<string, unknown>,
  ): string {
    const base = apiClient.defaults.baseURL || '';
    let url = `${base}/api/v1/stocks/criteria/analyze?symbol=${encodeURIComponent(symbol)}`;
    if (preFetchedData) {
      const json = JSON.stringify(preFetchedData);
      const encoded = btoa(encodeURIComponent(json).replace(/%([0-9A-F]{2})/g, (_, p1) => String.fromCharCode(parseInt(p1, 16))))
        .replace(/\+/g, '-')
        .replace(/\//g, '_')
        .replace(/=+$/, '');
      url += `&pre_fetched=${encodeURIComponent(encoded)}`;
    }
    return url;
  },

  /** Fetch cached buy criteria result for today (or a specific date). */
  getCached: async (
    symbol: string,
    targetDate?: string,
  ): Promise<CachedCriteriaResponse> => {
    let url = `/api/v1/stocks/criteria/cached/${encodeURIComponent(symbol)}`;
    if (targetDate) {
      url += `?date=${encodeURIComponent(targetDate)}`;
    }
    const { data } = await apiClient.get<CachedCriteriaResponse>(url);
    return data;
  },
};
