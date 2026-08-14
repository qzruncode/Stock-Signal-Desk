import apiClient from './index';
import { toCamelCase } from './utils';

export type AgentRunStatus =
  | 'queued'
  | 'running'
  | 'recovering'
  | 'interrupted'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'cancelled'
  | 'blocked';

export interface AgentRunFeedback {
  id: string;
  runId: string;
  rating: -1 | 1;
  category?: string | null;
  comment?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AgentRunSummary {
  runId: string;
  conversationId: string;
  status: AgentRunStatus;
  errorCode?: string | null;
  engine?: string | null;
  tools: string[];
  toolObservationCount: number;
  evidenceCount: number;
  evidenceLinksVerified: boolean;
  qualityScore: number;
  qualityStatus: 'passed' | 'failed';
  feedback?: AgentRunFeedback | null;
  toolCallCount: number;
  providerCallCount: number;
  estimatedTokenCount: number;
  estimatedCostMicros: number;
  durationMs?: number | null;
  createdAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  finalTextPreview?: string;
}

export interface AgentRunListResponse {
  items: AgentRunSummary[];
  total: number;
  page: number;
  limit: number;
}

export interface AgentRunQualityDimension {
  score: number;
  weight: number;
  details: Record<string, unknown>;
}

export interface AgentRunQualityScore {
  evaluatorVersion: string;
  status: 'passed' | 'failed';
  passed: boolean;
  totalScore: number;
  minimumScore: number;
  dimensions: Record<string, AgentRunQualityDimension>;
  violations: Array<{ code: string; details: unknown }>;
  feedback?: AgentRunFeedback | Record<string, never>;
}

export interface AgentRunDetail {
  snapshot: {
    run: Record<string, unknown>;
    trace: Record<string, unknown>;
    qualityProjection: {
      toolResults?: Array<Record<string, unknown>>;
      evidence?: Array<Record<string, unknown>>;
      loop?: Record<string, unknown>;
      completedToolCallIds?: string[];
    };
    steps: Array<Record<string, unknown>>;
    artifacts: Array<Record<string, unknown>>;
    feedback?: AgentRunFeedback | null;
  };
  score: AgentRunQualityScore;
}

export interface AgentQualitySummary {
  windowDays: number;
  terminalRuns: number;
  quality: {
    scoredRuns: number;
    passedRuns: number;
    passRate?: number | null;
    averageScore?: number | null;
    dimensionScores: Record<string, number>;
    violations: Record<string, number>;
  };
  feedback: {
    total: number;
    positive: number;
    negative: number;
    positiveRate?: number | null;
  };
  releaseGate: {
    evaluations: number;
    passed: number;
    failed: number;
    passRate?: number | null;
  };
}

export const runExplorerApi = {
  async listRuns(params: {
    status?: string;
    tool?: string;
    page?: number;
    limit?: number;
  } = {}): Promise<AgentRunListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/runs', { params });
    return toCamelCase<AgentRunListResponse>(response.data);
  },

  async getRun(
    runId: string,
    options: { includePayloads?: boolean } = {},
  ): Promise<AgentRunDetail> {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/agent/runs/${runId}`, {
      params: options.includePayloads ? { include_payloads: true } : undefined,
    });
    return toCamelCase<AgentRunDetail>(response.data);
  },

  async getQualitySummary(days = 30): Promise<AgentQualitySummary> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/quality/summary', {
      params: { days },
    });
    return toCamelCase<AgentQualitySummary>(response.data);
  },

  async saveFeedback(
    runId: string,
    rating: -1 | 1,
    category?: string,
  ): Promise<AgentRunFeedback> {
    const response = await apiClient.put<Record<string, unknown>>(
      `/api/v1/agent/runs/${runId}/feedback`,
      { rating, category },
    );
    return toCamelCase<AgentRunFeedback>(response.data);
  },
};
