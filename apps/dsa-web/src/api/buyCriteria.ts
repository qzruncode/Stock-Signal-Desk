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

// ── API ─────────────────────────────────────────────────────────────────

export const buyCriteriaApi = {
  /** Get the SSE URL for criteria analysis. */
  getCriteriaStreamUrl(symbol: string): string {
    const base = apiClient.defaults.baseURL || '';
    return `${base}/api/v1/stocks/buy-decision/criteria/analyze?symbol=${encodeURIComponent(symbol)}`;
  },
};
